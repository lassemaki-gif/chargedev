"""Host SEPA payout batching.

Hosts are paid their 80%-of-net earnings via weekly SEPA transfers from
ChargedEV's bank. This module does the aggregation and CSV generation; the
actual bank upload is a manual step the admin does after downloading the CSV.

Flow:
    1. Admin hits GET  /api/admin/payouts/pending  → preview of who's owed what
    2. Admin hits POST /api/admin/payouts/generate → creates a HostPayout batch,
       locking those booking IDs so a second run doesn't double-count them.
    3. Admin hits GET  /api/admin/payouts/{id}/csv → downloads a CSV for the bank
    4. Admin uploads the CSV manually to Nordea / OP / etc.
    5. Admin hits POST /api/admin/payouts/{id}/mark-paid → flips the batch and
       its underlying bookings to paid_out = true, stamps paid_at.

A booking is "ready to pay out" when:
    - status == completed
    - paid_out == false
    - the host has an iban + account_holder on file

Hosts without IBAN appear as "blocked" in the preview — we hold their earnings
until they add one, rather than skipping them silently.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Booking, BookingStatus, HostPayout, Listing, User


# Advisory lock key for serializing concurrent generate_batch runs.
# `pg_advisory_xact_lock` releases automatically at transaction end;
# any 64-bit int works as the key — this one is just a stable hash of
# the string "chargedev:host_payouts_generate".
_GENERATE_BATCH_LOCK_KEY = 7318926431_201457502


# Formula-injection sentinels. Any CSV cell whose first character is one
# of these would be evaluated as a formula by Excel / LibreOffice when the
# file is opened — including the inline bank-upload previewers. We prefix
# such cells with a single quote so the spreadsheet treats them as text.
_FORMULA_INJECTION_CHARS = {"=", "+", "-", "@", "\t", "\r"}


def _csv_safe(cell: str) -> str:
    """Neutralise CSV formula injection in a single cell."""
    if cell and cell[0] in _FORMULA_INJECTION_CHARS:
        return "'" + cell
    return cell


async def pending_by_host(session: AsyncSession) -> list[dict]:
    """Preview: aggregate every completed-and-unpaid booking by host.

    Shape of each row:
        {host_id, host_name, host_email, iban_set, account_holder,
         iban_last4, booking_count, net_eur, earliest, latest}
    `iban_set` tells the caller whether this host is immediately payable; a
    host without IBAN still shows up (so admin can see the amount owed and
    chase them for one), but their row cannot be included in a generated batch.
    """
    # Join booking → listing → host (listings.seller_id -> users.id).
    stmt = (
        select(Booking, Listing, User)
        .join(Listing, Booking.listing_id == Listing.id)
        .join(User, Listing.seller_id == User.id)
        .where(Booking.status == BookingStatus.completed, Booking.paid_out == False)  # noqa: E712
    )
    rows = (await session.execute(stmt)).all()

    agg: dict[int, dict] = {}
    for b, _lst, host in rows:
        slot = agg.get(host.id)
        if slot is None:
            # IBAN is encrypted at rest; we only need the last-4 for display,
            # which the user's own payout_account_set logic exposes elsewhere.
            # Avoid decrypting here to keep this preview endpoint cheap.
            slot = {
                "host_id": host.id,
                "host_name": host.full_name,
                "host_email": host.email,
                "iban_set": bool(host.iban and host.payout_account_holder),
                "account_holder": host.payout_account_holder,
                "iban_last4": None,  # populated at batch-generation time
                "booking_count": 0,
                "net_eur": 0.0,
                "earliest": b.created_at,
                "latest": b.created_at,
                "booking_ids": [],
            }
            agg[host.id] = slot
        slot["booking_count"] += 1
        slot["net_eur"] = round(slot["net_eur"] + b.seller_earnings_eur, 2)
        slot["booking_ids"].append(b.id)
        if b.created_at < slot["earliest"]:
            slot["earliest"] = b.created_at
        if b.created_at > slot["latest"]:
            slot["latest"] = b.created_at

    # Deterministic order: highest-owed first.
    return sorted(agg.values(), key=lambda r: r["net_eur"], reverse=True)


async def generate_batch(
    session: AsyncSession,
    *,
    decrypt_iban,  # callable injected from main.py to avoid import cycle
) -> list[HostPayout]:
    """Create one HostPayout row per payable host, link their booking IDs.

    Does NOT yet mark bookings as paid_out — only the mark-paid endpoint does
    that, after the admin confirms the SEPA transfer actually ran. The booking
    is "locked" via the booking_ids_json array: generate_batch skips bookings
    that already appear in a pending-or-paid HostPayout.

    Serialised via a Postgres transaction advisory lock so two concurrent
    callers (admin double-click, UI retry) can't both insert HostPayout rows
    for the same host and have the admin accidentally pay them twice.
    """
    # Advisory lock + the partial unique index on (host_id) WHERE status='pending'
    # together make this operation safe under concurrency. The lock serialises
    # the generation attempt; the index is the belt-and-braces fallback that
    # would reject a second insert even if the lock were somehow released early.
    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": _GENERATE_BATCH_LOCK_KEY},
        )
    except Exception:
        # On SQLite in local dev the function doesn't exist — fine; it's a
        # single-process context there anyway.
        pass

    pending = await pending_by_host(session)

    # Collect every booking_id already locked in a non-cancelled payout row,
    # so a second generate call doesn't double-batch them.
    locked_stmt = select(HostPayout).where(HostPayout.status != "cancelled")
    locked_rows = (await session.execute(locked_stmt)).scalars().all()
    locked_ids: set[int] = set()
    for row in locked_rows:
        try:
            locked_ids.update(json.loads(row.booking_ids_json or "[]"))
        except json.JSONDecodeError:
            continue

    created: list[HostPayout] = []
    now = datetime.utcnow()

    for row in pending:
        if not row["iban_set"]:
            continue  # host hasn't added IBAN yet; hold the earnings
        booking_ids = [bid for bid in row["booking_ids"] if bid not in locked_ids]
        if not booking_ids:
            continue  # all this host's unpaid bookings are already in a batch

        # Recompute net for the filtered subset so we don't bill them for
        # bookings already covered by an earlier batch.
        subset_stmt = select(Booking).where(Booking.id.in_(booking_ids))
        subset = (await session.execute(subset_stmt)).scalars().all()
        net = round(sum(b.seller_earnings_eur for b in subset), 2)
        if net <= 0:
            continue

        host = await session.get(User, row["host_id"])
        try:
            iban_full = decrypt_iban(host.iban) if host and host.iban else ""
        except Exception:
            # IBAN decrypt failed — skip this host, admin will see the row
            # under "pending" but unable to batch until re-saved.
            continue
        iban_last4 = iban_full[-4:] if len(iban_full) >= 4 else iban_full

        payout = HostPayout(
            host_id=row["host_id"],
            period_start=row["earliest"],
            period_end=row["latest"],
            net_eur=net,
            booking_ids_json=json.dumps(booking_ids),
            iban_last4=iban_last4,
            account_holder=row["account_holder"],
            status="pending",
            created_at=now,
        )
        session.add(payout)
        created.append(payout)

    await session.commit()
    for p in created:
        await session.refresh(p)
    return created


def to_csv(
    payouts: list[HostPayout],
    *,
    decrypt_iban,
    host_lookup: dict[int, User],
    reference_prefix: str = "ChargedEV payout",
) -> str:
    """Return a bank-upload CSV covering the given payouts.

    Columns: IBAN,Name,Amount,Currency,Reference.
    Finnish OP + Nordea both accept this shape for manual batch uploads.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["IBAN", "Name", "Amount", "Currency", "Reference"])

    for p in payouts:
        host = host_lookup.get(p.host_id)
        try:
            iban = decrypt_iban(host.iban) if host and host.iban else ""
        except Exception:
            iban = ""
        if not iban:
            continue  # skip rows we can't fill — admin will see it missing and fix the IBAN
        ref = f"{reference_prefix} #{p.id}"
        name = p.account_holder or (host.full_name if host else "")
        writer.writerow([
            _csv_safe(iban),
            _csv_safe(name),
            _csv_safe(f"{p.net_eur:.2f}"),
            "EUR",
            _csv_safe(ref),
        ])

    return buf.getvalue()


async def mark_batch_paid(
    session: AsyncSession,
    payout: HostPayout,
    sepa_reference: Optional[str] = None,
) -> HostPayout:
    """Flip the batch and its underlying bookings to paid.

    Idempotent — calling twice on the same batch leaves bookings paid_out and
    doesn't fail. Does nothing if the batch is cancelled.
    """
    if payout.status == "cancelled":
        return payout
    try:
        ids = json.loads(payout.booking_ids_json or "[]")
    except json.JSONDecodeError:
        ids = []
    if ids:
        now = datetime.utcnow()
        stmt = select(Booking).where(Booking.id.in_(ids))
        bookings = (await session.execute(stmt)).scalars().all()
        for b in bookings:
            if not b.paid_out:
                b.paid_out = True
                b.paid_out_at = now
    payout.status = "paid"
    payout.paid_at = datetime.utcnow()
    if sepa_reference:
        payout.sepa_reference = sepa_reference
    await session.commit()
    await session.refresh(payout)
    return payout

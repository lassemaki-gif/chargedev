"""SumUp Online Payments API client.

Single-merchant acquiring: unlike Mollie Connect, SumUp settles the full gross
to ChargedEV's own merchant account. VAT and the host's share are routed by us
afterwards — the host's SEPA payout runs weekly from backend/app/payouts.

Scope kept intentionally small: create a hosted checkout, verify its final
status, refund a transaction, list our settlement payouts. Everything else
lives in the SumUp dashboard.

Endpoints referenced here were taken from https://developer.sumup.com/api as of
2026-10-30. Confirm paths against openapi.json before the first prod call —
the public docs don't always list the exact v-prefix.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import HTTPException

from .config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.sumup.com"
TIMEOUT_S = 15.0


def _headers() -> dict:
    if not settings.sumup_secret_key:
        raise HTTPException(503, "SumUp is not configured on this environment")
    return {
        "Authorization": f"Bearer {settings.sumup_secret_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _scrub(text: str) -> str:
    """Trim long bodies in error logs so a stray card BIN or token can't leak."""
    return (text or "")[:400]


async def _request(method: str, path: str, **kwargs) -> dict:
    url = f"{API_BASE}{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        resp = await client.request(method, url, headers=_headers(), **kwargs)
    if resp.status_code >= 400:
        logger.error("SumUp %s %s failed: status=%s body=%s",
                     method, path, resp.status_code, _scrub(resp.text))
        resp.raise_for_status()
    return resp.json() if resp.content else {}


async def create_checkout(
    *,
    checkout_reference: str,
    amount_eur: Decimal,
    description: str,
    redirect_url: str,
    webhook_url: Optional[str] = None,
    customer_email: Optional[str] = None,
) -> dict:
    """Create a hosted checkout and return the hosted-page URL.

    `checkout_reference` must be unique per payment (e.g. "booking-{id}") so a
    double-submit from the frontend hits SumUp's idempotency rather than
    creating a second charge.

    SumUp's two URL fields are confusingly named:
      - `redirect_url`: the "back to merchant" button on SumUp's success page.
      - `return_url`:   the HTTP POST webhook SumUp calls on status changes.
    We expose them under their semantic names (redirect_url, webhook_url).

    Returns a dict with:
      - checkout_id: SumUp's own ID (store on the booking row)
      - checkout_url: hosted page to redirect the buyer to
    """
    if not settings.sumup_merchant_code:
        raise HTTPException(503, "SUMUP_MERCHANT_CODE is not configured")

    body: dict = {
        "checkout_reference": checkout_reference,
        "amount": float(amount_eur),  # SumUp expects a decimal number, not minor units
        "currency": "EUR",
        "merchant_code": settings.sumup_merchant_code,
        "description": description,
        # Without this flag the checkout exists but SumUp doesn't mint a hosted
        # page URL — the response comes back with no hosted_checkout_url field.
        "hosted_checkout": {"enabled": True},
        "redirect_url": redirect_url,
    }
    if webhook_url:
        body["return_url"] = webhook_url
    # customer_email is a client-level receipt convenience; SumUp ignores
    # unknown fields silently, so passing it is safe either way.
    if customer_email:
        body["customer_email"] = customer_email

    data = await _request("POST", "/v0.1/checkouts", json=body)
    return {
        "checkout_id": data.get("id"),
        "checkout_url": data.get("hosted_checkout_url"),
        "status": data.get("status"),
        "raw": data,
    }


async def verify_checkout(checkout_id: str) -> dict:
    """Fetch a checkout's current status.

    Status values observed in SumUp docs: PENDING, PAID, FAILED, EXPIRED.
    The success page polls this after return from the hosted page.

    Returns:
      - status: one of the above
      - transaction_id: present once status == PAID; used for refunds
    """
    data = await _request("GET", f"/v0.1/checkouts/{checkout_id}")
    txns = data.get("transactions") or []
    paid_txn = next(
        (t for t in txns if (t.get("status") or "").upper() == "SUCCESSFUL"),
        None,
    )
    return {
        "status": (data.get("status") or "").upper(),
        "transaction_id": paid_txn.get("id") if paid_txn else None,
        "transaction_code": paid_txn.get("transaction_code") if paid_txn else None,
        "raw": data,
    }


async def refund(transaction_id: str, amount_eur: Optional[Decimal] = None) -> dict:
    """Refund a successful transaction, in full by default or partial if amount given."""
    if not settings.sumup_merchant_code:
        raise HTTPException(503, "SUMUP_MERCHANT_CODE is not configured")
    path = f"/v1.0/merchants/{settings.sumup_merchant_code}/payments/{transaction_id}/refunds"
    body: dict = {}
    if amount_eur is not None:
        body["amount"] = float(amount_eur)
    return await _request("POST", path, json=body)


async def list_payouts(limit: int = 50) -> list[dict]:
    """List settlement payouts from SumUp to ChargedEV's bank. For admin dashboard."""
    if not settings.sumup_merchant_code:
        raise HTTPException(503, "SUMUP_MERCHANT_CODE is not configured")
    data = await _request(
        "GET",
        f"/v1.0/merchants/{settings.sumup_merchant_code}/payouts",
        params={"limit": limit},
    )
    return data.get("items") or data.get("payouts") or []

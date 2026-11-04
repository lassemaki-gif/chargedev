"use client";
import { Suspense, useEffect, useMemo, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Nav } from "@/components/Nav";
import { MessageThread } from "@/components/MessageThread";
import { api, Booking } from "@/lib/api";
import { pickUIT, UIT } from "@/lib/ui";

function statusBadge(s: string, t: UIT) {
  if (s === "completed") return <span className="badge-green">{t.statusCompleted}</span>;
  if (s === "confirmed") return <span className="badge-blue">{t.statusConfirmed}</span>;
  if (s === "active") return <span className="badge-yellow">{t.statusActive}</span>;
  if (s === "pending") return <span className="badge-gray">{t.statusPending}</span>;
  if (s === "cancelled") return <span className="badge-gray">{t.statusCancelled}</span>;
  return <span className="badge-gray">{s}</span>;
}

function CopyPin({ pin, t }: { pin: string; t: UIT }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(pin).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={copy}
      className="inline-flex items-center gap-1.5 font-mono text-volt text-sm bg-volt/10 px-2.5 py-1 rounded hover:bg-volt/20 transition-colors"
    >
      {pin}
      <span className="text-xs text-volt/60">{copied ? t.bookingsCopied : t.bookingsCopy}</span>
    </button>
  );
}

export default function BookingsPage() {
  return (
    <Suspense fallback={null}>
      <BookingsPageInner />
    </Suspense>
  );
}

function BookingsPageInner() {
  const router = useRouter();
  const search = useSearchParams();
  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT(uiLang), [uiLang]);
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.myBookings();
      setBookings(data.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.includes("401") || msg.includes("authenticated") || msg.includes("Not authenticated")) {
        router.push("/charge");
      } else {
        setError(msg || "Failed to load bookings");
      }
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => { load(); }, [load]);

  const active = bookings.filter((b) => b.status === "confirmed" || b.status === "active");
  const past = bookings.filter((b) => b.status === "completed" || b.status === "cancelled" || b.status === "pending");

  if (loading) return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-56px)] text-ash">Loading…</div>
    </div>
  );

  return (
    <div>
      <Nav />
      <div className="px-4 sm:px-6 lg:px-16 py-8 sm:py-12 max-w-3xl">
        <div className="mb-8">
          <h1 className="text-2xl sm:text-3xl font-bold text-white">{t.bookingsTitle}</h1>
        </div>

        {error && (
          <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-6">{error}</div>
        )}

        {bookings.length === 0 && (
          <div className="card text-center py-12">
            <div className="text-4xl mb-3">⚡</div>
            <p className="text-white font-medium mb-1">{t.bookingsNoBookingsTitle}</p>
            <p className="text-ash text-sm mb-6">{t.bookingsNoBookingsBody}</p>
            <Link href={`/charge${langSuffix}`} className="btn-volt text-sm">{t.bookingsFindCharger}</Link>
          </div>
        )}

        {active.length > 0 && (
          <section className="mb-8">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-volt mb-3">{t.bookingsActive}</h2>
            <div className="space-y-3">
              {active.map((b) => (
                <BookingCard key={b.id} booking={b} t={t} langSuffix={langSuffix} />
              ))}
            </div>
          </section>
        )}

        {past.length > 0 && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-widest text-ash mb-3">{t.bookingsHistory}</h2>
            <div className="space-y-3">
              {past.map((b) => (
                <BookingCard key={b.id} booking={b} t={t} langSuffix={langSuffix} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function BookingCard({ booking: b, t, langSuffix }: { booking: Booking; t: UIT; langSuffix: string }) {
  const [msgOpen, setMsgOpen] = useState(false);
  const date = new Date(b.created_at).toLocaleDateString(undefined, {
    day: "numeric", month: "short", year: "numeric",
  });

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <Link
            href={`/charge/${b.listing_id}${langSuffix}`}
            className="font-semibold text-white hover:text-volt transition-colors line-clamp-1"
          >
            {b.listing_title}
          </Link>
          <p className="text-ash text-xs sm:text-sm truncate">{b.listing_address}</p>
        </div>
        <div className="shrink-0 text-right">
          {statusBadge(b.status, t)}
          <p className="text-ash text-xs mt-1">{date}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ash mb-3">
        <span><span className="text-white font-medium">{b.package_kwh} kWh</span></span>
        <span>€{b.total_eur.toFixed(2)}</span>
      </div>

      {b.pin_code && (
        <div className="flex items-center gap-2">
          <span className="text-ash text-xs">{t.bookingsSessionPin}</span>
          <CopyPin pin={b.pin_code} t={t} />
        </div>
      )}

      {b.status === "confirmed" && (
        <Link
          href={`/charge/${b.listing_id}/start${langSuffix}`}
          className="btn-volt w-full text-sm text-center mt-3"
        >
          {t.bookingsStartCharging} →
        </Link>
      )}

      {b.status === "pending" && (
        <p className="text-ash text-xs mt-2">{t.bookingsPaymentPending}</p>
      )}

      {b.status !== "pending" && (
        <div className="mt-3">
          <button
            onClick={() => setMsgOpen((v) => !v)}
            className="text-ash hover:text-white text-xs transition-colors"
          >
            {msgOpen ? `${t.bookingsHideMessages} ↑` : `${t.bookingsMessageHost} ↓`}
          </button>
          {msgOpen && (
            <div className="mt-2">
              <MessageThread bookingId={b.id} viewerRole="buyer" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

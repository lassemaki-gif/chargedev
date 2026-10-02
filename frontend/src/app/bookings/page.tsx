"use client";
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Nav } from "@/components/Nav";
import { api, Booking } from "@/lib/api";

function statusBadge(s: string) {
  if (s === "completed") return <span className="badge-green">Completed</span>;
  if (s === "confirmed") return <span className="badge-blue">Confirmed</span>;
  if (s === "active") return <span className="badge-yellow">Active</span>;
  if (s === "pending") return <span className="badge-gray">Pending</span>;
  return <span className="badge-gray">{s}</span>;
}

function CopyPin({ pin }: { pin: string }) {
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
      <span className="text-xs text-volt/60">{copied ? "✓" : "copy"}</span>
    </button>
  );
}

export default function BookingsPage() {
  const router = useRouter();
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
          <h1 className="text-2xl sm:text-3xl font-bold text-white">My bookings</h1>
          <p className="text-ash text-sm mt-1">Your charging sessions and history</p>
        </div>

        {error && (
          <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-6">{error}</div>
        )}

        {bookings.length === 0 && (
          <div className="card text-center py-12">
            <div className="text-4xl mb-3">⚡</div>
            <p className="text-white font-medium mb-1">No bookings yet</p>
            <p className="text-ash text-sm mb-6">Find a charger near you and book your first session.</p>
            <Link href="/charge" className="btn-volt text-sm">Find a charger</Link>
          </div>
        )}

        {active.length > 0 && (
          <section className="mb-8">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-volt mb-3">Active</h2>
            <div className="space-y-3">
              {active.map((b) => (
                <BookingCard key={b.id} booking={b} />
              ))}
            </div>
          </section>
        )}

        {past.length > 0 && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-widest text-ash mb-3">History</h2>
            <div className="space-y-3">
              {past.map((b) => (
                <BookingCard key={b.id} booking={b} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function BookingCard({ booking: b }: { booking: Booking }) {
  const date = new Date(b.created_at).toLocaleDateString(undefined, {
    day: "numeric", month: "short", year: "numeric",
  });

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <Link
            href={`/charge/${b.listing_id}`}
            className="font-semibold text-white hover:text-volt transition-colors line-clamp-1"
          >
            {b.listing_title}
          </Link>
          <p className="text-ash text-xs sm:text-sm truncate">{b.listing_address}</p>
        </div>
        <div className="shrink-0 text-right">
          {statusBadge(b.status)}
          <p className="text-ash text-xs mt-1">{date}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ash mb-3">
        <span><span className="text-white font-medium">{b.package_kwh} kWh</span></span>
        <span>€{b.total_eur.toFixed(2)} paid</span>
      </div>

      {b.pin_code && (
        <div className="flex items-center gap-2">
          <span className="text-ash text-xs">Session PIN</span>
          <CopyPin pin={b.pin_code} />
        </div>
      )}

      {b.status === "pending" && (
        <p className="text-ash text-xs mt-2">Payment pending — your PIN will appear here once confirmed.</p>
      )}
    </div>
  );
}

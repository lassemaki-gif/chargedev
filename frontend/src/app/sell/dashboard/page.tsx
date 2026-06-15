"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api, Listing, Booking } from "@/lib/api";
import { useRouter } from "next/navigation";

function statusBadge(s: string) {
  if (s === "completed") return <span className="badge-green">Completed</span>;
  if (s === "confirmed") return <span className="badge-blue">Confirmed</span>;
  if (s === "active") return <span className="badge-yellow">Active</span>;
  return <span className="badge-gray">{s}</span>;
}

export default function SellerDashboard() {
  const router = useRouter();
  const [listings, setListings] = useState<Listing[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.myListings(), api.sellerBookings()])
      .then(([l, b]) => { setListings(l); setBookings(b); })
      .catch((e) => {
        if (e.message?.includes("401") || e.message?.includes("authenticated")) router.push("/sell");
        else setError(e.message);
      })
      .finally(() => setLoading(false));
  }, [router]);

  async function toggle(id: number) {
    const res = await api.toggleListing(id);
    setListings((prev) => prev.map((l) => l.id === id ? { ...l, is_available: res.is_available } : l));
  }

  async function complete(id: number) {
    await api.completeBooking(id);
    setBookings((prev) => prev.map((b) => b.id === id ? { ...b, status: "completed" } : b));
  }

  const totalEarned = bookings.filter((b) => b.status === "completed").reduce((s, b) => s + b.seller_earnings_eur, 0);
  const totalKwh = bookings.filter((b) => b.status === "completed").reduce((s, b) => s + b.package_kwh, 0);

  if (loading) return <div className="min-h-screen flex items-center justify-center text-ash">Loading…</div>;

  return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-12 max-w-5xl">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-white">Host dashboard</h1>
            <p className="text-ash mt-1">Manage your chargers and bookings</p>
          </div>
          <Link href="/sell/listing/new" className="btn-volt text-sm">+ Add charger</Link>
        </div>

        {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-6">{error}</div>}

        {/* Stats */}
        <div className="grid sm:grid-cols-3 gap-4 mb-10">
          {[
            { label: "Total earned", value: `€${totalEarned.toFixed(2)}` },
            { label: "kWh delivered", value: `${totalKwh} kWh` },
            { label: "Completed sessions", value: bookings.filter((b) => b.status === "completed").length },
          ].map((s) => (
            <div key={s.label} className="card">
              <div className="text-ash text-sm mb-1">{s.label}</div>
              <div className="text-2xl font-bold text-white">{s.value}</div>
            </div>
          ))}
        </div>

        {/* Listings */}
        <h2 className="text-lg font-semibold text-white mb-4">Your chargers</h2>
        {listings.length === 0 ? (
          <div className="card text-ash text-sm mb-8">
            No chargers listed yet.{" "}
            <Link href="/sell/listing/new" className="text-volt hover:underline">Add your first charger →</Link>
          </div>
        ) : (
          <div className="space-y-3 mb-10">
            {listings.map((l) => (
              <div key={l.id} className="card flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="font-semibold text-white truncate">{l.title}</div>
                  <div className="text-ash text-sm">{l.address} · {l.charger_type} · {l.max_power_kw} kW · €{l.price_per_kwh}/kWh</div>
                </div>
                <button
                  onClick={() => toggle(l.id)}
                  className={`shrink-0 px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${l.is_available ? "bg-green-900/40 text-green-400 hover:bg-red-900/40 hover:text-red-400" : "bg-gray-800 text-gray-400 hover:bg-green-900/40 hover:text-green-400"}`}
                >
                  {l.is_available ? "Available" : "Unavailable"}
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Bookings */}
        <h2 className="text-lg font-semibold text-white mb-4">Bookings</h2>
        {bookings.length === 0 ? (
          <div className="card text-ash text-sm">No bookings yet.</div>
        ) : (
          <div className="space-y-3">
            {bookings.map((b) => (
              <div key={b.id} className="card">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="font-semibold text-white">{b.listing_title}</div>
                    <div className="text-ash text-sm mt-0.5">
                      {b.buyer_name} · {b.package_kwh} kWh · €{b.total_eur.toFixed(2)} total · you earn €{b.seller_earnings_eur.toFixed(2)}
                    </div>
                    <div className="mt-2 flex items-center gap-3">
                      {statusBadge(b.status)}
                      {b.pin_code && (
                        <span className="font-mono text-volt text-sm bg-volt/10 px-2 py-0.5 rounded">
                          PIN: {b.pin_code}
                        </span>
                      )}
                    </div>
                  </div>
                  {b.status === "confirmed" && (
                    <button
                      onClick={() => complete(b.id)}
                      className="shrink-0 px-3 py-1.5 rounded-lg text-sm bg-volt/10 text-volt hover:bg-volt/20 transition-colors font-medium"
                    >
                      Mark complete
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

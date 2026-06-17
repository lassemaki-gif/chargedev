"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api, Listing, Booking, SellerEarnings } from "@/lib/api";
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
  const [earnings, setEarnings] = useState<SellerEarnings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [iban, setIban] = useState("");
  const [ibanSaving, setIbanSaving] = useState(false);
  const [ibanSaved, setIbanSaved] = useState(false);
  const [completing, setCompleting] = useState<number | null>(null);
  const [notifEnabled, setNotifEnabled] = useState(false);

  useEffect(() => {
    Promise.all([api.myListings(), api.sellerBookings(), api.sellerEarnings()])
      .then(([l, b, e]) => {
        setListings(l);
        setBookings(b);
        setEarnings(e);
        setIban(e.iban ?? "");
      })
      .catch((e) => {
        const msg: string = e.message ?? "";
        if (msg === "Not authenticated" || msg === "Invalid token") router.push("/sell");
        else setError(msg || "Failed to load dashboard");
      })
      .finally(() => setLoading(false));
  }, [router]);

  async function enableNotifications() {
    if (!("Notification" in window)) { alert("Your browser does not support notifications."); return; }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return;
    setNotifEnabled(true);
    let lastChecked = Date.now() / 1000;
    const interval = setInterval(async () => {
      try {
        const res = await api.newBookingsSince(lastChecked);
        if (res.count > 0) {
          new Notification("ChargedEV — New booking ⚡", {
            body: `You have ${res.count} new booking${res.count > 1 ? "s" : ""}!`,
            icon: "/favicon.ico",
          });
        }
        lastChecked = Date.now() / 1000;
      } catch { clearInterval(interval); }
    }, 30000);
    return () => clearInterval(interval);
  }

  async function saveIban(e: React.FormEvent) {
    e.preventDefault();
    setIbanSaving(true);
    try {
      await api.updateProfile({ iban });
      setIbanSaved(true);
      setTimeout(() => setIbanSaved(false), 3000);
      if (earnings) setEarnings({ ...earnings, iban });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save IBAN");
    } finally {
      setIbanSaving(false);
    }
  }

  async function toggle(id: number) {
    const res = await api.toggleListing(id);
    setListings((prev) => prev.map((l) => l.id === id ? { ...l, is_available: res.is_available } : l));
  }

  async function complete(id: number) {
    setCompleting(id);
    try {
      await api.completeBooking(id);
      setBookings((prev) => prev.map((b) => b.id === id ? { ...b, status: "completed" } : b));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to mark complete");
    } finally {
      setCompleting(null);
    }
  }

  if (loading) return <div className="min-h-screen flex items-center justify-center text-ash">Loading…</div>;

  const nextPayout = earnings?.next_payout_date
    ? new Date(earnings.next_payout_date).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })
    : "—";

  return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-12 max-w-5xl">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-white">Host dashboard</h1>
            <p className="text-ash mt-1">Manage your chargers and bookings</p>
          </div>
          <div className="flex gap-2">
          {!notifEnabled && (
            <button onClick={enableNotifications} className="btn-outline text-sm">
              🔔 Enable notifications
            </button>
          )}
          {notifEnabled && <span className="text-volt text-sm self-center">🔔 Notifications on</span>}
          <Link href="/sell/listing/new" className="btn-volt text-sm">+ Add charger</Link>
        </div>
        </div>

        {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-6">{error}</div>}

        {/* Earnings */}
        {earnings && (
          <div className="grid sm:grid-cols-3 gap-4 mb-6">
            <div className="card border-volt/20">
              <div className="text-ash text-sm mb-1">Pending payout</div>
              <div className="text-2xl font-bold text-volt">€{earnings.pending_eur.toFixed(2)}</div>
              <div className="text-ash text-xs mt-1">Next payout: {nextPayout}</div>
            </div>
            <div className="card">
              <div className="text-ash text-sm mb-1">Total earned</div>
              <div className="text-2xl font-bold text-white">€{earnings.total_eur.toFixed(2)}</div>
            </div>
            <div className="card">
              <div className="text-ash text-sm mb-1">Already paid out</div>
              <div className="text-2xl font-bold text-white">€{earnings.paid_out_eur.toFixed(2)}</div>
            </div>
          </div>
        )}

        {/* IBAN */}
        <div className="card mb-10">
          <h2 className="font-semibold text-white mb-1">Payout bank account</h2>
          <p className="text-ash text-sm mb-4">We transfer your earnings to this IBAN on the 1st of each month.</p>
          <form onSubmit={saveIban} className="flex gap-3">
            <input
              className="input flex-1 font-mono"
              placeholder="FI00 0000 0000 0000 00"
              value={iban}
              onChange={(e) => setIban(e.target.value.toUpperCase())}
            />
            <button type="submit" disabled={ibanSaving} className="btn-volt text-sm px-5 shrink-0">
              {ibanSaving ? "Saving…" : ibanSaved ? "Saved ✓" : "Save"}
            </button>
          </form>
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
                      {b.buyer_name} · {b.package_kwh} kWh · €{b.total_eur.toFixed(2)} total · you earn <span className="text-volt font-medium">€{b.seller_earnings_eur.toFixed(2)}</span>
                    </div>
                    <div className="mt-2 flex items-center gap-3 flex-wrap">
                      {statusBadge(b.status)}
                      {b.pin_code && (
                        <span className="font-mono text-volt text-sm bg-volt/10 px-2 py-0.5 rounded">
                          PIN: {b.pin_code}
                        </span>
                      )}
                      {b.paid_out && <span className="badge-green">Paid out</span>}
                    </div>
                  </div>
                  {(b.status === "confirmed" || b.status === "active") && (
                    <button
                      onClick={() => complete(b.id)}
                      disabled={completing === b.id}
                      className="shrink-0 px-3 py-1.5 rounded-lg text-sm bg-volt/10 text-volt hover:bg-volt/20 transition-colors font-medium disabled:opacity-50"
                    >
                      {completing === b.id ? "Saving…" : "Mark complete"}
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

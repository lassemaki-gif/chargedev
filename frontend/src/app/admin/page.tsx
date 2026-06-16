"use client";
import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { api, PlatformStats, User, Listing, Booking, saveToken, saveRole } from "@/lib/api";

type Tab = "overview" | "users" | "listings" | "bookings";

function statusBadge(s: string) {
  if (s === "completed") return <span className="badge-green">Completed</span>;
  if (s === "confirmed") return <span className="badge-blue">Confirmed</span>;
  if (s === "active") return <span className="badge-yellow">Active</span>;
  if (s === "pending") return <span className="badge-gray">Pending</span>;
  return <span className="badge-gray">{s}</span>;
}

export default function AdminDashboard() {
  const [authed, setAuthed] = useState(false);
  const [loginForm, setLoginForm] = useState({ email: "", password: "" });
  const [loginError, setLoginError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [listings, setListings] = useState<Listing[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [loading, setLoading] = useState(false);

  async function adminLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoginError(null);
    try {
      const res = await api.login(loginForm.email, loginForm.password);
      if (res.role !== "admin") { setLoginError("Not an admin account"); return; }
      saveToken(res.access_token);
      saveRole(res.role);
      setAuthed(true);
      loadAll();
    } catch (err: unknown) {
      setLoginError(err instanceof Error ? err.message : "Login failed");
    }
  }

  async function loadAll() {
    setLoading(true);
    try {
      const [s, u, l, b] = await Promise.all([
        api.adminStats(), api.adminUsers(), api.adminListings(), api.adminBookings(),
      ]);
      setStats(s); setUsers(u); setListings(l); setBookings(b);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("ll_token") : null;
    const role = typeof window !== "undefined" ? localStorage.getItem("ll_role") : null;
    if (token && role === "admin") { setAuthed(true); loadAll(); }
  }, []);

  async function toggleUser(id: number) {
    await api.toggleUser(id);
    setUsers((u) => u.map((user) => user.id === id ? { ...user, is_active: !user.is_active } : user));
  }

  if (!authed) return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="card max-w-sm w-full">
        <div className="text-center mb-6">
          <div className="text-3xl mb-2">🔐</div>
          <h1 className="text-xl font-bold text-white">Admin access</h1>
          <p className="text-ash text-sm mt-1">ChargedEV platform administration</p>
        </div>
        {loginError && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-4">{loginError}</div>}
        <form onSubmit={adminLogin} className="space-y-4">
          <div>
            <label className="label">Email</label>
            <input className="input" type="email" value={loginForm.email} onChange={(e) => setLoginForm((f) => ({ ...f, email: e.target.value }))} required />
          </div>
          <div>
            <label className="label">Password</label>
            <input className="input" type="password" value={loginForm.password} onChange={(e) => setLoginForm((f) => ({ ...f, password: e.target.value }))} required />
          </div>
          <button type="submit" className="btn-volt w-full text-center">Sign in as admin</button>
        </form>
      </div>
    </div>
  );

  const TABS: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "users", label: `Users (${users.length})` },
    { key: "listings", label: `Listings (${listings.length})` },
    { key: "bookings", label: `Bookings (${bookings.length})` },
  ];

  return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-10 max-w-6xl">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-white">Admin dashboard</h1>
            <p className="text-ash mt-1">Platform overview and management</p>
          </div>
          <span className="badge-yellow">Admin</span>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-border mb-8">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${tab === t.key ? "border-volt text-volt" : "border-transparent text-ash hover:text-white"}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {loading && <div className="text-ash">Loading…</div>}

        {/* Overview */}
        {tab === "overview" && stats && (
          <div className="space-y-6">
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { label: "Total users", value: stats.total_users, sub: `${stats.total_sellers} sellers · ${stats.total_buyers} buyers` },
                { label: "Active listings", value: stats.active_listings, sub: `${stats.total_listings} total` },
                { label: "Completed sessions", value: stats.completed_bookings, sub: `${stats.total_bookings} total bookings` },
                { label: "Platform earnings", value: `€${stats.platform_earnings_eur.toFixed(2)}`, sub: `€${stats.total_revenue_eur.toFixed(2)} GMV` },
              ].map((s) => (
                <div key={s.label} className="card">
                  <div className="text-ash text-sm mb-1">{s.label}</div>
                  <div className="text-2xl font-bold text-white">{s.value}</div>
                  <div className="text-ash text-xs mt-0.5">{s.sub}</div>
                </div>
              ))}
            </div>
            <div className="grid sm:grid-cols-2 gap-4">
              <div className="card">
                <div className="text-ash text-sm mb-1">Total kWh delivered</div>
                <div className="text-2xl font-bold text-volt">{stats.total_kwh_delivered} kWh</div>
              </div>
              <div className="card">
                <div className="text-ash text-sm mb-1">Platform fee rate</div>
                <div className="text-2xl font-bold text-white">20%</div>
                <div className="text-ash text-xs">of each transaction</div>
              </div>
            </div>
          </div>
        )}

        {/* Users */}
        {tab === "users" && (
          <div className="space-y-3">
            {users.map((u) => (
              <div key={u.id} className="card flex items-center justify-between gap-4">
                <div>
                  <div className="font-medium text-white">{u.full_name}</div>
                  <div className="text-ash text-sm">{u.email} · {u.phone ?? "no phone"}</div>
                  <div className="mt-1 flex gap-2">
                    <span className={u.role === "seller" ? "badge-green" : u.role === "admin" ? "badge-yellow" : "badge-blue"}>{u.role}</span>
                    {!u.is_active && <span className="badge-gray">Suspended</span>}
                  </div>
                </div>
                {u.role !== "admin" && (
                  <button onClick={() => toggleUser(u.id)} className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${u.is_active ? "bg-red-900/30 text-red-400 hover:bg-red-900/50" : "bg-green-900/30 text-green-400 hover:bg-green-900/50"}`}>
                    {u.is_active ? "Suspend" : "Restore"}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Listings */}
        {tab === "listings" && (
          <div className="space-y-3">
            {listings.map((l) => (
              <div key={l.id} className="card flex items-center justify-between gap-4">
                <div>
                  <div className="font-medium text-white">{l.title}</div>
                  <div className="text-ash text-sm">{l.address}, {l.city} · {l.charger_type} · {l.max_power_kw} kW · €{l.price_per_kwh}/kWh</div>
                  <div className="text-ash text-xs mt-1">by {l.seller_name}</div>
                </div>
                <span className={l.is_available ? "badge-green" : "badge-gray"}>{l.is_available ? "Active" : "Inactive"}</span>
              </div>
            ))}
          </div>
        )}

        {/* Bookings */}
        {tab === "bookings" && (
          <div className="space-y-3">
            {bookings.map((b) => (
              <div key={b.id} className="card">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-mono text-ash text-xs">#{b.id}</span>
                      <span className="font-medium text-white">{b.listing_title}</span>
                    </div>
                    <div className="text-ash text-sm">{b.listing_address}</div>
                    <div className="text-ash text-sm mt-0.5">
                      Buyer: {b.buyer_name} · {b.package_kwh} kWh · €{b.total_eur.toFixed(2)}
                      <span className="text-ash/60"> (platform: €{b.platform_fee_eur.toFixed(2)} · seller: €{b.seller_earnings_eur.toFixed(2)})</span>
                    </div>
                    <div className="mt-2 flex gap-2 items-center flex-wrap">
                      {statusBadge(b.status)}
                      {b.pin_code
                        ? <span className="font-mono text-volt text-xs bg-volt/10 px-2 py-0.5 rounded">PIN: {b.pin_code}</span>
                        : <span className="text-ash text-xs">No PIN yet</span>
                      }
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-2 shrink-0">
                    <div className="text-ash text-xs">{new Date(b.created_at).toLocaleDateString()}</div>
                    {b.status === "pending" && (
                      <button
                        onClick={async () => {
                          const token = localStorage.getItem("ll_token");
                          const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"}/api/admin/bookings/${b.id}/confirm`, {
                            method: "PUT",
                            headers: { Authorization: `Bearer ${token}` },
                          });
                          const data = await res.json();
                          if (data.pin_code) {
                            setBookings((prev) => prev.map((x) => x.id === b.id ? { ...x, status: "confirmed", pin_code: data.pin_code } : x));
                          }
                        }}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-volt/10 text-volt hover:bg-volt/20 transition-colors"
                      >
                        Generate PIN
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

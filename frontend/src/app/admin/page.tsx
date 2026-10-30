"use client";
import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { api, PlatformStats, User, Listing, Booking, PendingPayoutHost, HostPayoutOut, saveRole } from "@/lib/api";

type Tab = "overview" | "users" | "listings" | "bookings" | "payouts";

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
  const [vatFrom, setVatFrom] = useState("");
  const [vatTo, setVatTo] = useState("");
  const [pendingPayouts, setPendingPayouts] = useState<PendingPayoutHost[]>([]);
  const [batches, setBatches] = useState<HostPayoutOut[]>([]);
  const [payoutsLoading, setPayoutsLoading] = useState(false);
  const [payoutsError, setPayoutsError] = useState<string | null>(null);
  const [batchFilter, setBatchFilter] = useState<"pending" | "paid" | "all">("pending");
  const [markPaidRef, setMarkPaidRef] = useState<{ id: number; value: string } | null>(null);

  async function adminLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoginError(null);
    try {
      const res = await api.login(loginForm.email, loginForm.password);
      if (res.role !== "admin") { setLoginError("Not an admin account"); return; }
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
    const role = typeof window !== "undefined" ? localStorage.getItem("ll_role") : null;
    if (role === "admin") { setAuthed(true); loadAll(); }
  }, []);

  async function toggleUser(id: number) {
    await api.toggleUser(id);
    setUsers((u) => u.map((user) => user.id === id ? { ...user, is_active: !user.is_active } : user));
  }

  async function loadPayouts() {
    setPayoutsLoading(true);
    setPayoutsError(null);
    try {
      const [pending, batchRows] = await Promise.all([
        api.adminPayoutsPending(),
        api.adminPayoutsList(batchFilter === "all" ? undefined : batchFilter),
      ]);
      setPendingPayouts(pending);
      setBatches(batchRows);
    } catch (err: unknown) {
      setPayoutsError(err instanceof Error ? err.message : "Failed to load payouts");
    } finally {
      setPayoutsLoading(false);
    }
  }

  // Reload the Payouts tab whenever it's opened or the status filter changes.
  useEffect(() => {
    if (authed && tab === "payouts") loadPayouts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, tab, batchFilter]);

  async function generateBatches() {
    const payable = pendingPayouts.filter((p) => p.iban_set).length;
    if (payable === 0) {
      alert("No payable hosts (every pending host is missing an IBAN).");
      return;
    }
    if (!confirm(`Generate ${payable} payout batch(es)? Included booking IDs will be locked.`)) return;
    try {
      await api.adminPayoutsGenerate();
      await loadPayouts();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Generate failed");
    }
  }

  async function downloadBatchCsv(payoutId?: number) {
    const path = payoutId !== undefined
      ? `/api/admin/payouts/${payoutId}/csv`
      : "/api/admin/payouts/csv";
    const resp = await fetch(`/api/proxy${path}`, { credentials: "include" });
    if (!resp.ok) { alert("CSV download failed"); return; }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = payoutId !== undefined ? `payout-${payoutId}.csv` : `payouts-${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  async function confirmMarkPaid() {
    if (!markPaidRef) return;
    try {
      await api.adminPayoutMarkPaid(markPaidRef.id, { sepa_reference: markPaidRef.value || undefined });
      setMarkPaidRef(null);
      await loadPayouts();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Mark paid failed");
    }
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

  const sellers = users.filter((u) => u.role === "seller");

  const TABS: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "payouts", label: `Payouts${pendingPayouts.length ? ` (${pendingPayouts.length})` : ""}` },
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
            {(() => {
              const listingCountry = Object.fromEntries(listings.map((l) => [l.id, l.country]));
              const completed = bookings.filter((b) => {
                if (b.status !== "completed") return false;
                if (vatFrom && b.created_at < vatFrom) return false;
                if (vatTo && b.created_at > vatTo + "T23:59:59") return false;
                return true;
              });
              const byCountry: Record<string, { vat: number; count: number }> = {};
              let vatTotal = 0;
              for (const b of completed) {
                const vat = b.total_eur - b.seller_earnings_eur - b.platform_fee_eur;
                const country = listingCountry[b.listing_id] ?? "Unknown";
                if (!byCountry[country]) byCountry[country] = { vat: 0, count: 0 };
                byCountry[country].vat += vat;
                byCountry[country].count += 1;
                vatTotal += vat;
              }
              const rows = Object.entries(byCountry).sort((a, b) => b[1].vat - a[1].vat);
              return (
                <div className="card border-yellow-700/40">
                  <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
                    <div className="text-ash text-sm font-medium">VAT collected (ALV 25,5%) — remit to Vero</div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <input
                        type="date"
                        value={vatFrom}
                        onChange={(e) => setVatFrom(e.target.value)}
                        className="input text-sm py-1 px-2 w-36"
                        placeholder="From"
                      />
                      <span className="text-ash text-sm">–</span>
                      <input
                        type="date"
                        value={vatTo}
                        onChange={(e) => setVatTo(e.target.value)}
                        className="input text-sm py-1 px-2 w-36"
                        placeholder="To"
                      />
                      {(vatFrom || vatTo) && (
                        <button
                          onClick={() => { setVatFrom(""); setVatTo(""); }}
                          className="text-ash text-xs hover:text-white"
                        >
                          Clear
                        </button>
                      )}
                    </div>
                  </div>
                  {rows.length > 0 ? (
                    <table className="w-full text-sm mb-4">
                      <thead>
                        <tr className="text-ash text-xs border-b border-border">
                          <th className="text-left pb-2 font-medium">Country</th>
                          <th className="text-right pb-2 font-medium">Bookings</th>
                          <th className="text-right pb-2 font-medium">VAT collected</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map(([country, { vat, count }]) => (
                          <tr key={country} className="border-b border-border/40">
                            <td className="py-2 text-white">{country}</td>
                            <td className="py-2 text-right text-ash">{count}</td>
                            <td className="py-2 text-right text-yellow-400 font-medium">€{vat.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr>
                          <td className="pt-3 font-semibold text-white">Total</td>
                          <td className="pt-3 text-right text-ash">{completed.length}</td>
                          <td className="pt-3 text-right text-3xl font-bold text-yellow-400">€{vatTotal.toFixed(2)}</td>
                        </tr>
                      </tfoot>
                    </table>
                  ) : (
                    <div className="text-3xl font-bold text-yellow-400 mb-1">€0.00</div>
                  )}
                  <div className="text-ash text-xs">VAT = total paid − host earnings − platform fee · 25,5% Finnish VAT</div>
                </div>
              );
            })()}
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

        {/* Payouts */}
        {tab === "payouts" && (
          <div className="space-y-6">
            {payoutsError && (
              <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm">
                {payoutsError}
              </div>
            )}

            {/* Pending by host */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-semibold text-white text-lg">Pending by host</h2>
                <div className="flex gap-2">
                  <button
                    onClick={loadPayouts}
                    disabled={payoutsLoading}
                    className="text-ash hover:text-white text-xs"
                  >
                    {payoutsLoading ? "Loading…" : "Refresh"}
                  </button>
                  <button
                    onClick={generateBatches}
                    disabled={payoutsLoading || pendingPayouts.filter((p) => p.iban_set).length === 0}
                    className="btn-volt text-xs px-3 py-1.5 disabled:opacity-40"
                  >
                    Generate batches
                  </button>
                </div>
              </div>
              {pendingPayouts.length === 0 ? (
                <div className="card text-ash text-sm">No pending payouts.</div>
              ) : (
                <div className="space-y-3">
                  {pendingPayouts.map((p) => (
                    <div key={p.host_id} className="card">
                      <div className="flex items-start justify-between gap-4 flex-wrap">
                        <div>
                          <div className="font-medium text-white">{p.host_name}</div>
                          <div className="text-ash text-xs">{p.host_email}</div>
                          <div className="mt-2 flex items-center gap-3 text-sm flex-wrap">
                            <span className="text-volt font-semibold">€{p.net_eur.toFixed(2)}</span>
                            <span className="text-ash text-xs">{p.booking_count} booking{p.booking_count !== 1 ? "s" : ""}</span>
                            <span className="text-ash text-xs">
                              {new Date(p.earliest).toLocaleDateString()} → {new Date(p.latest).toLocaleDateString()}
                            </span>
                          </div>
                        </div>
                        <div>
                          {p.iban_set ? (
                            <span className="badge-green">Payable</span>
                          ) : (
                            <span className="badge-yellow">Awaiting IBAN</span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Batches */}
            <div>
              <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                <h2 className="font-semibold text-white text-lg">Batches</h2>
                <div className="flex items-center gap-2">
                  <div className="flex gap-1 text-xs">
                    {(["pending", "paid", "all"] as const).map((f) => (
                      <button
                        key={f}
                        onClick={() => setBatchFilter(f)}
                        className={`px-2 py-1 rounded ${batchFilter === f ? "bg-volt/20 text-volt" : "text-ash hover:text-white"}`}
                      >
                        {f.charAt(0).toUpperCase() + f.slice(1)}
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={() => downloadBatchCsv()}
                    className="text-xs text-ash hover:text-white border border-border rounded px-2 py-1"
                  >
                    Download all pending CSV
                  </button>
                </div>
              </div>
              {batches.length === 0 ? (
                <div className="card text-ash text-sm">No batches to show.</div>
              ) : (
                <div className="space-y-2">
                  {batches.map((b) => (
                    <div key={b.id} className="card">
                      <div className="flex items-start justify-between gap-4 flex-wrap">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-white">#{b.id}</span>
                            <span className="text-ash text-sm">{b.host_name || `host ${b.host_id}`}</span>
                            {b.status === "pending" && <span className="badge-yellow">Pending</span>}
                            {b.status === "paid" && <span className="badge-green">Paid</span>}
                            {b.status === "cancelled" && <span className="badge-gray">Cancelled</span>}
                          </div>
                          <div className="mt-1 flex items-center gap-3 text-xs text-ash flex-wrap">
                            <span className="text-volt text-sm font-semibold">€{b.net_eur.toFixed(2)}</span>
                            <span>{b.booking_count} booking{b.booking_count !== 1 ? "s" : ""}</span>
                            {b.iban_last4 && <span>IBAN ••{b.iban_last4}</span>}
                            {b.account_holder && <span>{b.account_holder}</span>}
                          </div>
                          <div className="mt-1 text-ash text-xs">
                            Created {new Date(b.created_at).toLocaleDateString()}
                            {b.paid_at && ` · Paid ${new Date(b.paid_at).toLocaleDateString()}`}
                            {b.sepa_reference && ` · ref ${b.sepa_reference}`}
                          </div>
                        </div>
                        <div className="flex gap-2 shrink-0">
                          <button
                            onClick={() => downloadBatchCsv(b.id)}
                            className="text-xs text-ash hover:text-white border border-border rounded px-2 py-1"
                          >
                            CSV
                          </button>
                          {b.status === "pending" && (
                            <button
                              onClick={() => setMarkPaidRef({ id: b.id, value: "" })}
                              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-volt/10 text-volt hover:bg-volt/20 transition-colors"
                            >
                              Mark paid
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Mark-paid modal */}
            {markPaidRef && (
              <div
                className="fixed inset-0 bg-black/70 flex items-center justify-center px-4 z-50"
                onClick={() => setMarkPaidRef(null)}
              >
                <div
                  className="card max-w-sm w-full"
                  onClick={(e) => e.stopPropagation()}
                >
                  <h3 className="font-semibold text-white mb-2">Mark batch #{markPaidRef.id} paid</h3>
                  <p className="text-ash text-xs mb-3">
                    This flips the batch and its underlying bookings to paid. Optionally record the SEPA
                    reference your bank gave you after upload.
                  </p>
                  <label className="block text-ash text-xs mb-1">SEPA reference (optional)</label>
                  <input
                    type="text"
                    value={markPaidRef.value}
                    onChange={(e) => setMarkPaidRef({ ...markPaidRef, value: e.target.value })}
                    placeholder="e.g. nordea-20261030-01"
                    className="input w-full mb-4"
                    maxLength={64}
                    autoFocus
                  />
                  <div className="flex gap-2 justify-end">
                    <button onClick={() => setMarkPaidRef(null)} className="btn-outline text-sm px-4">Cancel</button>
                    <button onClick={confirmMarkPaid} className="btn-volt text-sm px-4">Mark paid</button>
                  </div>
                </div>
              </div>
            )}
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
                <button
                  onClick={async () => {
                    const res = await api.adminToggleListing(l.id);
                    setListings((prev) => prev.map((x) => x.id === l.id ? { ...x, is_available: res.is_available } : x));
                  }}
                  className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${l.is_available ? "bg-green-900/40 text-green-400 hover:bg-red-900/30 hover:text-red-400" : "bg-gray-800 text-gray-400 hover:bg-green-900/30 hover:text-green-400"}`}
                >
                  {l.is_available ? "Active" : "Inactive"}
                </button>
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
                          const res = await fetch(`/api/proxy/api/admin/bookings/${b.id}/confirm`, {
                            method: "PUT",
                            credentials: "include",
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

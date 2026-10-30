"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api, Listing, Booking, SellerEarnings, ShellyStatus, OcppChargePointStatus, PayoutAccount } from "@/lib/api";
import { useRouter, useSearchParams } from "next/navigation";

function statusBadge(s: string) {
  if (s === "completed") return <span className="badge-green">Completed</span>;
  if (s === "confirmed") return <span className="badge-blue">Confirmed</span>;
  if (s === "active") return <span className="badge-yellow">Active</span>;
  return <span className="badge-gray">{s}</span>;
}

export default function SellerDashboard() {
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center text-ash">Loading…</div>}>
      <SellerDashboardInner />
    </Suspense>
  );
}

function SellerDashboardInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [listings, setListings] = useState<Listing[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [earnings, setEarnings] = useState<SellerEarnings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feeLoading, setFeeLoading] = useState(false);
  const [payoutAccount, setPayoutAccount] = useState<PayoutAccount | null>(null);
  const [payoutForm, setPayoutForm] = useState({ iban: "", account_holder: "" });
  const [payoutSaving, setPayoutSaving] = useState(false);
  const [payoutError, setPayoutError] = useState<string | null>(null);
  const [payoutEditing, setPayoutEditing] = useState(false);
  const [completing, setCompleting] = useState<number | null>(null);
  const [notifEnabled, setNotifEnabled] = useState(false);
  const [shellyOpen, setShellyOpen] = useState<number | null>(null);
  const [shellyForm, setShellyForm] = useState({ device_id: "", auth_key: "", server: "shelly-91-cloud.shelly.cloud" });
  const [shellyStatus, setShellyStatus] = useState<Record<number, ShellyStatus>>({});
  const [shellySaving, setShellySaving] = useState(false);
  const [ocppOpen, setOcppOpen] = useState<number | null>(null);
  const [ocppChargePointId, setOcppChargePointId] = useState("");
  const [ocppStatus, setOcppStatus] = useState<Record<number, OcppChargePointStatus>>({});
  const [ocppSaving, setOcppSaving] = useState(false);

  useEffect(() => {
    // Check if returning from joining fee payment
    const paymentId = searchParams.get("joining_fee") || sessionStorage.getItem("joining_fee_payment_id");
    if (paymentId) {
      sessionStorage.removeItem("joining_fee_payment_id");
      api.verifyJoiningFee(paymentId).catch(() => {}).finally(() => {
        const url = new URL(window.location.href);
        url.searchParams.delete("joining_fee");
        window.history.replaceState({}, "", url.toString());
      });
    }
    Promise.all([api.myListings(), api.sellerBookings(), api.sellerEarnings(), api.payoutAccount()])
      .then(([l, b, e, p]) => {
        setListings(l);
        setBookings(b);
        setEarnings(e);
        setPayoutAccount(p);
      })
      .catch((e) => {
        const msg: string = e.message ?? "";
        if (msg === "Not authenticated" || msg === "Invalid token") router.push("/sell");
        else setError(msg || "Failed to load dashboard");
      })
      .finally(() => setLoading(false));
  }, [router]);

  async function savePayoutAccount() {
    setPayoutError(null);
    setPayoutSaving(true);
    try {
      const updated = await api.setPayoutAccount({
        iban: payoutForm.iban.trim().replace(/\s+/g, "").toUpperCase(),
        account_holder: payoutForm.account_holder.trim(),
      });
      setPayoutAccount(updated);
      setPayoutForm({ iban: "", account_holder: "" });
      setPayoutEditing(false);
      // Refresh earnings so payout_account_set flips to true in the UI.
      try { setEarnings(await api.sellerEarnings()); } catch { /* ignore */ }
    } catch (err: unknown) {
      setPayoutError(err instanceof Error ? err.message : "Failed to save payout account.");
    } finally {
      setPayoutSaving(false);
    }
  }

  async function payFee() {
    setFeeLoading(true);
    try {
      const res = await api.payJoiningFee();
      if ("checkout_url" in res) {
        sessionStorage.setItem("joining_fee_payment_id", res.payment_id);
        window.location.href = res.checkout_url;
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start payment");
      setFeeLoading(false);
    }
  }

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

  async function toggle(id: number) {
    const res = await api.toggleListing(id);
    setListings((prev) => prev.map((l) => l.id === id ? { ...l, is_available: res.is_available } : l));
  }

  async function openShelly(l: Listing) {
    setShellyOpen(l.id);
    if (l.shelly_enabled) {
      try {
        const s = await api.shellyStatus(l.id);
        setShellyStatus((prev) => ({ ...prev, [l.id]: s }));
      } catch { /* ignore */ }
    }
  }

  async function saveShelly(listingId: number) {
    setShellySaving(true);
    try {
      const s = await api.shellyConnect(listingId, shellyForm);
      setShellyStatus((prev) => ({ ...prev, [listingId]: s }));
      setListings((prev) => prev.map((l) => l.id === listingId ? { ...l, shelly_enabled: true } : l));
      setShellyOpen(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to connect Shelly device");
    } finally {
      setShellySaving(false);
    }
  }

  async function disconnectShelly(listingId: number) {
    await api.shellyDisconnect(listingId);
    setListings((prev) => prev.map((l) => l.id === listingId ? { ...l, shelly_enabled: false } : l));
    setShellyStatus((prev) => { const n = { ...prev }; delete n[listingId]; return n; });
    setShellyOpen(null);
  }

  async function openOcpp(l: Listing) {
    setOcppOpen(l.id);
    if (l.ocpp_enabled) {
      try {
        const s = await api.ocppStatus(l.id);
        setOcppStatus((prev) => ({ ...prev, [l.id]: s }));
      } catch { /* ignore */ }
    }
  }

  async function saveOcpp(listingId: number) {
    setOcppSaving(true);
    try {
      const s = await api.ocppRegister(listingId, ocppChargePointId.trim());
      setOcppStatus((prev) => ({ ...prev, [listingId]: s }));
      setListings((prev) => prev.map((l) => l.id === listingId ? { ...l, ocpp_enabled: true } : l));
      setOcppOpen(null);
      setOcppChargePointId("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to register OCPP charge point");
    } finally {
      setOcppSaving(false);
    }
  }

  async function disconnectOcpp(listingId: number) {
    await api.ocppUnregister(listingId);
    setListings((prev) => prev.map((l) => l.id === listingId ? { ...l, ocpp_enabled: false } : l));
    setOcppStatus((prev) => { const n = { ...prev }; delete n[listingId]; return n; });
    setOcppOpen(null);
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
  return (
    <div>
      <Nav />
      <div className="px-4 sm:px-6 lg:px-16 py-6 sm:py-12 max-w-5xl">
        <div className="flex items-start justify-between gap-4 mb-6 sm:mb-8">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold text-white">Host dashboard</h1>
            <p className="text-ash text-sm mt-1">Manage your chargers and bookings</p>
          </div>
          <div className="flex flex-wrap gap-2 shrink-0">
            {!notifEnabled && (
              <button onClick={enableNotifications} className="btn-outline text-sm px-3 py-2">
                <span className="hidden sm:inline">🔔 Enable notifications</span>
                <span className="sm:hidden">🔔</span>
              </button>
            )}
            {notifEnabled && <span className="text-volt text-sm self-center">🔔</span>}
            {earnings?.joining_fee_paid !== false && (
              <Link href="/sell/listing/new" className="btn-volt text-sm px-4 py-2">+ Add</Link>
            )}
          </div>
        </div>

        {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-6">{error}</div>}

        {/* Earnings */}
        {earnings && (
          <div className="grid sm:grid-cols-3 gap-4 mb-6">
            <div className="card border-volt/20">
              <div className="text-ash text-sm mb-1">Pending transfer</div>
              <div className="text-2xl font-bold text-volt">€{earnings.pending_eur.toFixed(2)}</div>
              <div className="text-ash text-xs mt-1">Transferred immediately on payment</div>
            </div>
            <div className="card">
              <div className="text-ash text-sm mb-1">Total earned</div>
              <div className="text-2xl font-bold text-white">€{earnings.total_eur.toFixed(2)}</div>
            </div>
            <div className="card">
              <div className="text-ash text-sm mb-1">Transferred to you</div>
              <div className="text-2xl font-bold text-white">€{earnings.paid_out_eur.toFixed(2)}</div>
            </div>
          </div>
        )}

        {/* Joining fee gate */}
        {earnings?.joining_fee_paid === false && (
          <div className="card border-volt/40 mb-8">
            <h2 className="font-semibold text-white mb-1">One-time host joining fee</h2>
            <p className="text-ash text-sm mb-4">
              A one-time €1 joining fee is required to activate your host account and create listings.
              This helps us keep the platform free of bots and spam.
            </p>
            <button
              onClick={payFee}
              disabled={feeLoading}
              className="btn-volt text-sm px-6 disabled:opacity-40"
            >
              {feeLoading ? "Redirecting…" : "Pay €1 joining fee"}
            </button>
          </div>
        )}

        {/* Payout account — IBAN for weekly SEPA host payouts */}
        <div className="card mb-10">
          <h2 className="font-semibold text-white mb-1">Payout account</h2>
          {payoutAccount?.is_set && !payoutEditing ? (
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <span className="text-green-400 text-sm font-medium">Payout account on file ✓</span>
                <p className="text-ash text-xs mt-0.5">
                  {payoutAccount.account_holder} · IBAN ending {payoutAccount.iban_last4}
                </p>
                <p className="text-ash text-xs mt-1">
                  Your earnings (80% of each booking, net of VAT) are paid out weekly via SEPA to this account.
                </p>
              </div>
              <button
                onClick={() => { setPayoutEditing(true); setPayoutError(null); }}
                className="text-xs text-ash hover:text-white"
              >
                Update
              </button>
            </div>
          ) : (
            <>
              <p className="text-ash text-sm mb-4">
                Add the bank account where you&apos;d like your weekly earnings paid out. SEPA transfers only, EUR.
              </p>
              <div className="space-y-3">
                <div>
                  <label className="block text-ash text-xs mb-1">Account holder</label>
                  <input
                    type="text"
                    value={payoutForm.account_holder}
                    onChange={(e) => setPayoutForm({ ...payoutForm, account_holder: e.target.value })}
                    placeholder="Full name as it appears on your bank account"
                    className="input w-full"
                    maxLength={120}
                  />
                </div>
                <div>
                  <label className="block text-ash text-xs mb-1">IBAN</label>
                  <input
                    type="text"
                    value={payoutForm.iban}
                    onChange={(e) => setPayoutForm({ ...payoutForm, iban: e.target.value })}
                    placeholder="FI00 0000 0000 0000 00"
                    className="input w-full font-mono tracking-wider"
                    maxLength={42}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
                {payoutError && <p className="text-red-400 text-xs">{payoutError}</p>}
                <div className="flex gap-2">
                  <button
                    onClick={savePayoutAccount}
                    disabled={payoutSaving || !payoutForm.iban || !payoutForm.account_holder}
                    className="btn-volt text-sm px-6 disabled:opacity-40"
                  >
                    {payoutSaving ? "Saving…" : "Save payout account"}
                  </button>
                  {payoutEditing && (
                    <button
                      onClick={() => { setPayoutEditing(false); setPayoutError(null); setPayoutForm({ iban: "", account_holder: "" }); }}
                      className="btn-outline text-sm px-4"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            </>
          )}
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
              <div key={l.id} className="card">
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-white truncate">{l.title}</span>
                      {l.shelly_enabled && (
                        <span className="shrink-0 text-xs font-medium px-2 py-0.5 rounded-full bg-volt/10 text-volt border border-volt/20">⚡ Premium</span>
                      )}
                    </div>
                    <div className="text-ash text-xs sm:text-sm truncate">{l.city} · {l.charger_type} · {l.max_power_kw} kW · €{l.price_per_kwh}/kWh</div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Link
                      href={`/sell/listing/${l.id}/edit`}
                      className="px-3 py-1.5 rounded-lg text-xs font-medium bg-surface border border-border text-ash hover:text-white hover:border-white/30 transition-colors"
                    >
                      Edit
                    </Link>
                    <button
                      onClick={() => openOcpp(l)}
                      className={`hidden sm:block px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${l.ocpp_enabled ? "bg-blue-900/30 border-blue-700 text-blue-400" : "bg-surface border-border text-ash hover:text-white hover:border-blue-700/40"}`}
                    >
                      {l.ocpp_enabled ? "OCPP ✓" : "OCPP"}
                    </button>
                    <button
                      onClick={() => openShelly(l)}
                      className="hidden sm:block px-3 py-1.5 rounded-lg text-xs font-medium bg-surface border border-border text-ash hover:text-white hover:border-volt/40 transition-colors"
                    >
                      {l.shelly_enabled ? "Shelly ✓" : "Shelly"}
                    </button>
                    <button
                      onClick={() => toggle(l.id)}
                      className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${l.is_available ? "bg-green-900/40 text-green-400 hover:bg-red-900/40 hover:text-red-400" : "bg-gray-800 text-gray-400 hover:bg-green-900/40 hover:text-green-400"}`}
                    >
                      {l.is_available ? "Available" : "Unavailable"}
                    </button>
                  </div>
                </div>

                {/* Shelly config panel */}
                {shellyOpen === l.id && (
                  <div className="mt-4 pt-4 border-t border-border">
                    {l.shelly_enabled && shellyStatus[l.id] ? (
                      <div className="space-y-3">
                        <div className="flex items-center gap-3 flex-wrap text-sm">
                          <span className={`font-medium ${shellyStatus[l.id].connected ? "text-volt" : "text-red-400"}`}>
                            {shellyStatus[l.id].connected ? "● Online" : "● Offline"}
                          </span>
                          {shellyStatus[l.id].relay_on !== undefined && (
                            <span className="text-ash">Relay: <span className="text-white">{shellyStatus[l.id].relay_on ? "ON" : "OFF"}</span></span>
                          )}
                          {shellyStatus[l.id].power_w !== undefined && (
                            <span className="text-ash">Power: <span className="text-white">{shellyStatus[l.id].power_w?.toFixed(0)} W</span></span>
                          )}
                          {shellyStatus[l.id].energy_total_wh !== undefined && (
                            <span className="text-ash">Total: <span className="text-white">{((shellyStatus[l.id].energy_total_wh ?? 0) / 1000).toFixed(2)} kWh</span></span>
                          )}
                        </div>
                        <div className="flex gap-2">
                          <button onClick={() => setShellyOpen(null)} className="btn-outline text-xs px-4 py-1.5">Close</button>
                          <button onClick={() => disconnectShelly(l.id)} className="text-xs text-red-400 hover:text-red-300 px-3">Disconnect Shelly</button>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-3">
                        <p className="text-ash text-sm">Connect a Shelly device (Pro 3EM, Plus 1PM, or similar) to enable auto-start and metered sessions.</p>
                        <div className="rounded-lg bg-red-950/50 border border-red-700 px-3 py-2.5 text-sm">
                          <div className="font-semibold text-red-300">⚠ Electrical safety — qualified electrician only</div>
                          <p className="text-red-200/90 mt-1 text-xs leading-relaxed">
                            Wiring a Shelly into the EV charger supply is <span className="font-semibold">mains electrical work</span> and must be done by a <span className="font-semibold">licensed electrician</span> (in Finland: an authorised sähköurakoitsija).
                            Do not open the charger, consumer unit, or wall socket yourself. Incorrect wiring can cause fire, electric shock, or death, and voids the charger&apos;s warranty and your home insurance.
                            Only the fields below (Device ID, Auth key, Server) are safe to fill in yourself — the physical install is not.
                          </p>
                        </div>
                        <details className="rounded-lg bg-night/60 border border-border px-3 py-2 text-sm">
                          <summary className="cursor-pointer text-white font-medium">How to find these values (step-by-step)</summary>
                          <ol className="list-decimal pl-5 mt-2 space-y-1.5 text-ash">
                            <li>Install the <span className="text-white">Shelly Smart Control</span> app on your phone and add your device (Shelly Plus 1PM, Pro 1PM, or Pro 3EM work best).</li>
                            <li><span className="text-red-300 font-semibold">Have a licensed electrician</span> wire the Shelly between the mains and the EV charger&apos;s supply, so toggling the relay switches charging on and off. <span className="text-red-300">Never do this yourself.</span></li>
                            <li>Open <span className="text-white">home.shelly.cloud</span> on a laptop and sign in.</li>
                            <li><span className="text-white">Device ID</span> — click your device → <span className="text-white">Settings → Device information</span>. Copy the ID exactly (looks like <span className="font-mono text-xs">shellyplus1pm-aabbccddeeff</span>).</li>
                            <li><span className="text-white">Auth key</span> — top-right profile menu → <span className="text-white">User Settings → Authorization cloud key → Get key</span>. Copy the whole string.</li>
                            <li><span className="text-white">Server</span> — same User Settings page shows your server (for example <span className="font-mono text-xs">shelly-91-cloud.shelly.cloud</span>). If unsure, leave the default.</li>
                            <li>Paste all three into the fields below and click <span className="text-white">Connect device</span>. The status badge should turn green within a few seconds.</li>
                          </ol>
                        </details>
                        <div className="grid sm:grid-cols-2 gap-3">
                          <div>
                            <label className="label">Device ID</label>
                            <input className="input text-sm" placeholder="e.g. shellyplus1pm-aabbcc" value={shellyForm.device_id} onChange={(e) => setShellyForm((f) => ({ ...f, device_id: e.target.value }))} />
                          </div>
                          <div>
                            <label className="label">Auth Key</label>
                            <input className="input text-sm" type="password" placeholder="From Shelly Cloud → Settings → Auth key" value={shellyForm.auth_key} onChange={(e) => setShellyForm((f) => ({ ...f, auth_key: e.target.value }))} />
                          </div>
                          <div className="sm:col-span-2">
                            <label className="label">Server</label>
                            <input className="input text-sm" placeholder="shelly-91-cloud.shelly.cloud" value={shellyForm.server} onChange={(e) => setShellyForm((f) => ({ ...f, server: e.target.value }))} />
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <button onClick={() => saveShelly(l.id)} disabled={shellySaving || !shellyForm.device_id || !shellyForm.auth_key} className="btn-volt text-sm px-5 disabled:opacity-40">
                            {shellySaving ? "Connecting…" : "Connect device"}
                          </button>
                          <button onClick={() => setShellyOpen(null)} className="btn-outline text-sm px-4">Cancel</button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* OCPP config panel */}
                {ocppOpen === l.id && (
                  <div className="mt-4 pt-4 border-t border-border">
                    {l.ocpp_enabled && ocppStatus[l.id] ? (
                      <div className="space-y-3">
                        <div className="flex items-center gap-3 flex-wrap text-sm">
                          <span className={`font-medium ${ocppStatus[l.id].status === "offline" ? "text-red-400" : "text-blue-400"}`}>
                            ● {ocppStatus[l.id].status}
                          </span>
                          {ocppStatus[l.id].vendor && (
                            <span className="text-ash">{ocppStatus[l.id].vendor} {ocppStatus[l.id].model}</span>
                          )}
                          {ocppStatus[l.id].last_heartbeat && (
                            <span className="text-ash text-xs">Last seen: {new Date(ocppStatus[l.id].last_heartbeat!).toLocaleTimeString()}</span>
                          )}
                        </div>
                        <div className="bg-night rounded-lg px-3 py-2 text-xs font-mono text-ash break-all">
                          {ocppStatus[l.id].ws_url}
                        </div>
                        <p className="text-ash text-xs">Configure your wallbox to connect to this WebSocket URL using OCPP 1.6.</p>
                        <div className="flex gap-2">
                          <button onClick={() => setOcppOpen(null)} className="btn-outline text-xs px-4 py-1.5">Close</button>
                          <button onClick={() => disconnectOcpp(l.id)} className="text-xs text-red-400 hover:text-red-300 px-3">Unregister</button>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-3">
                        <p className="text-ash text-sm">Register an OCPP 1.6 compatible wallbox (Easee, Zaptec, Wallbox, etc.) for auto-start and real-time energy tracking.</p>
                        <details className="rounded-lg bg-night/60 border border-border px-3 py-2 text-sm">
                          <summary className="cursor-pointer text-white font-medium">How to connect your wallbox (step-by-step)</summary>
                          <ol className="list-decimal pl-5 mt-2 space-y-1.5 text-ash">
                            <li>Pick a <span className="text-white">Charge Point ID</span> below. Any unique string works — most hosts use the wallbox serial number (for example <span className="font-mono text-xs">EASEE-ABC123</span>).</li>
                            <li>Click <span className="text-white">Register charger</span>. ChargedEV generates a secure WebSocket URL for your wallbox.</li>
                            <li>Open your wallbox app or installer menu and find the OCPP settings. Common paths:
                              <ul className="list-disc pl-5 mt-1 space-y-0.5 text-xs">
                                <li><span className="text-white">Easee</span>: Easee app → Site → Settings → <span className="text-white">Advanced → OCPP</span>.</li>
                                <li><span className="text-white">Zaptec</span>: ZapCloud → charger → Settings → <span className="text-white">OCPP</span>.</li>
                                <li><span className="text-white">Wallbox</span>: myWallbox portal → charger → <span className="text-white">Advanced options → OCPP</span>.</li>
                                <li><span className="text-white">go-eCharger / Alfen / Keba</span>: installer web UI → <span className="text-white">OCPP / Backend</span>.</li>
                              </ul>
                            </li>
                            <li>Set these values in the wallbox:
                              <ul className="list-disc pl-5 mt-1 space-y-0.5 text-xs">
                                <li><span className="text-white">OCPP version:</span> 1.6-J (JSON over WebSocket)</li>
                                <li><span className="text-white">Central System / Backend URL:</span> paste the full WebSocket URL ChargedEV showed you (starts with <span className="font-mono">wss://</span>)</li>
                                <li><span className="text-white">Charge Point ID / Identity:</span> the same ID you picked in step 1</li>
                                <li><span className="text-white">Authorization:</span> none (the token is already in the URL)</li>
                              </ul>
                            </li>
                            <li>Save and reboot the wallbox. The status badge above should flip from <span className="text-red-400">Offline</span> to <span className="text-blue-400">Available</span> within a minute.</li>
                            <li>From then on, bookings auto-start charging and stop at the purchased kWh. No PIN handover needed.</li>
                          </ol>
                        </details>
                        <div>
                          <label className="label">Charge Point ID</label>
                          <input
                            className="input text-sm font-mono"
                            placeholder="e.g. EASEE-ABC123 (your wallbox serial or custom ID)"
                            value={ocppChargePointId}
                            onChange={(e) => setOcppChargePointId(e.target.value)}
                          />
                          <p className="text-ash text-xs mt-1.5">Use any unique identifier. You will configure the same ID in your wallbox settings under OCPP Central System URL.</p>
                        </div>
                        <div className="flex gap-2">
                          <button
                            onClick={() => saveOcpp(l.id)}
                            disabled={ocppSaving || !ocppChargePointId.trim()}
                            className="btn-volt text-sm px-5 disabled:opacity-40"
                          >
                            {ocppSaving ? "Registering…" : "Register charger"}
                          </button>
                          <button onClick={() => setOcppOpen(null)} className="btn-outline text-sm px-4">Cancel</button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
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
                    <div className="font-semibold text-white text-sm sm:text-base truncate">{b.listing_title}</div>
                    <div className="text-ash text-xs sm:text-sm mt-0.5">
                      <span className="block sm:inline">{b.buyer_name} · {b.package_kwh} kWh · €{b.total_eur.toFixed(2)}</span>
                      <span className="block sm:inline sm:ml-1">you earn <span className="text-volt font-medium">€{b.seller_earnings_eur.toFixed(2)}</span></span>
                    </div>
                    <div className="mt-2 flex items-center gap-3 flex-wrap">
                      {statusBadge(b.status)}
                      {b.pin_code && (
                        <span className="font-mono text-volt text-sm bg-volt/10 px-2 py-0.5 rounded">
                          PIN: {b.pin_code}
                        </span>
                      )}
                      {b.paid_out && <span className="badge-green">Transferred</span>}
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

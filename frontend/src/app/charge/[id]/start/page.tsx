"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Nav } from "@/components/Nav";
import { api, ChargeStartContext, HostSessionOut } from "@/lib/api";

export default function ChargeStartPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const listingId = Number(params?.id);

  const [ctx, setCtx] = useState<ChargeStartContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [hostSession, setHostSession] = useState<HostSessionOut | null>(null);
  const [buyerStarted, setBuyerStarted] = useState(false);

  const load = useCallback(async () => {
    if (!listingId) return;
    try {
      const c = await api.chargeContext(listingId);
      setCtx(c);
      if (c.role === "host" && c.host_session_active) {
        const hs = await api.hostSessionStatus(listingId);
        setHostSession(hs);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load charger";
      if (msg === "Not authenticated" || msg === "Invalid token") {
        router.push(`/sell?return=/charge/${listingId}/start`);
        return;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [listingId, router]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll the host session every 20s so the kWh counter stays warm while the
  // relay is actually drawing power.
  useEffect(() => {
    if (!hostSession || hostSession.status !== "active") return;
    const t = setInterval(async () => {
      try {
        const hs = await api.hostSessionStatus(listingId);
        setHostSession(hs);
      } catch {
        /* ignore transient errors */
      }
    }, 20000);
    return () => clearInterval(t);
  }, [hostSession, listingId]);

  async function startHostSession() {
    setStarting(true);
    setError(null);
    try {
      const hs = await api.hostSessionStart(listingId);
      setHostSession(hs);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start session");
    } finally {
      setStarting(false);
    }
  }

  async function stopHostSession() {
    if (!confirm("Stop charging now?")) return;
    setStopping(true);
    try {
      const hs = await api.hostSessionStop(listingId);
      setHostSession(hs);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to stop session");
    } finally {
      setStopping(false);
    }
  }

  async function startBuyerSession() {
    if (!ctx?.booking_id) return;
    setStarting(true);
    setError(null);
    try {
      await api.buyerStartSession(ctx.booking_id);
      setBuyerStarted(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start charging");
    } finally {
      setStarting(false);
    }
  }

  if (loading) {
    return (
      <div>
        <Nav />
        <div className="flex items-center justify-center min-h-[calc(100vh-64px)] text-ash text-sm">
          Loading…
        </div>
      </div>
    );
  }

  if (error && !ctx) {
    return (
      <div>
        <Nav />
        <div className="max-w-md mx-auto px-6 py-16">
          <div className="card text-center">
            <div className="text-3xl mb-3">⚠️</div>
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!ctx) return null;

  return (
    <div>
      <Nav />
      <div className="max-w-md mx-auto px-4 sm:px-6 py-10 sm:py-14">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="text-4xl mb-2">⚡</div>
          <h1 className="text-xl sm:text-2xl font-bold text-white mb-1">
            {ctx.listing_title}
          </h1>
          <p className="text-ash text-xs">{ctx.listing_address}</p>
        </div>

        {error && (
          <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-4">
            {error}
          </div>
        )}

        {/* HOST BRANCH */}
        {ctx.role === "host" && (
          <div className="card">
            {hostSession && hostSession.status === "active" ? (
              <>
                <h2 className="font-semibold text-white mb-1">Your charger is active</h2>
                <p className="text-ash text-xs mb-4">
                  Session auto-stops after 8 hours or when you tap Stop.
                </p>
                <div className="bg-night rounded-xl p-4 mb-4 text-center">
                  <div className="text-ash text-xs uppercase tracking-widest mb-1">
                    Delivered
                  </div>
                  <div className="text-3xl font-bold text-volt">
                    {(hostSession.energy_consumed_wh / 1000).toFixed(2)}{" "}
                    <span className="text-base font-normal text-ash">kWh</span>
                  </div>
                  <div className="text-ash text-xs mt-2">
                    Started {new Date(hostSession.started_at).toLocaleTimeString()} · expires{" "}
                    {new Date(hostSession.expires_at).toLocaleTimeString()}
                  </div>
                </div>
                <button
                  onClick={stopHostSession}
                  disabled={stopping}
                  className="w-full btn-outline text-sm py-3 disabled:opacity-40"
                >
                  {stopping ? "Stopping…" : "Stop charging"}
                </button>
              </>
            ) : (
              <>
                <h2 className="font-semibold text-white mb-1">Charge your own car</h2>
                <p className="text-ash text-xs mb-4">
                  Starts the charger for a session up to 8 hours. Blocked when a buyer booking is
                  active or starting within 60 minutes.
                </p>
                <button
                  onClick={startHostSession}
                  disabled={starting}
                  className="w-full btn-volt text-sm py-3 disabled:opacity-40"
                >
                  {starting ? "Starting…" : "Start my charging (8h)"}
                </button>
              </>
            )}
          </div>
        )}

        {/* BUYER BRANCH */}
        {ctx.role === "buyer" && ctx.booking_pin && (
          <div className="card">
            {buyerStarted ? (
              <div className="text-center">
                <div className="text-5xl mb-3">✅</div>
                <h2 className="text-lg font-semibold text-white mb-1">Charging started</h2>
                <p className="text-ash text-sm mb-6">
                  Your session will stop automatically at {ctx.booking_package_kwh} kWh.
                </p>
                <Link href="/bookings" className="btn-outline text-sm">
                  View my bookings
                </Link>
              </div>
            ) : (
              <>
                <h2 className="font-semibold text-white mb-1">Ready to charge</h2>
                <p className="text-ash text-xs mb-4">
                  Plug the cable in, then tap Start. Your session stops automatically at{" "}
                  {ctx.booking_package_kwh} kWh.
                </p>
                <div className="bg-night rounded-xl p-5 mb-4 text-center">
                  <div className="text-ash text-xs uppercase tracking-widest mb-1">Session PIN</div>
                  <div className="font-mono text-3xl font-bold text-volt tracking-[0.2em]">
                    {ctx.booking_pin}
                  </div>
                  {ctx.booking_expires_at && (
                    <div className="text-ash text-xs mt-3">
                      Expires {new Date(ctx.booking_expires_at).toLocaleTimeString()}
                    </div>
                  )}
                </div>
                <button
                  onClick={startBuyerSession}
                  disabled={starting}
                  className="w-full btn-volt text-sm py-3 disabled:opacity-40"
                >
                  {starting ? "Starting…" : "Start charging"}
                </button>
              </>
            )}
          </div>
        )}

        {/* NO ELIGIBLE BOOKING / HOST */}
        {ctx.role === "none" && (
          <div className="card text-center">
            <div className="text-3xl mb-3">🔌</div>
            <h2 className="font-semibold text-white mb-2">Book this charger</h2>
            <p className="text-ash text-xs mb-5">
              You don&rsquo;t have an active booking for this charger yet.
            </p>
            <Link href={`/charge/${listingId}`} className="btn-volt text-sm">
              View booking options
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

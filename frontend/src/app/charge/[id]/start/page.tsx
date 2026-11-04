"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Nav } from "@/components/Nav";
import { api, ChargeStartContext, HostSessionOut } from "@/lib/api";
import { pickUIT2 } from "@/lib/ui";

export default function ChargeStartPage() {
  return (
    <Suspense fallback={null}>
      <ChargeStartInner />
    </Suspense>
  );
}

function ChargeStartInner() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT2(uiLang), [uiLang]);
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";
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
      const msg = err instanceof Error ? err.message : t.startFailedLoad;
      if (msg === "Not authenticated" || msg === "Invalid token") {
        router.push(`/sell${langSuffix}${langSuffix ? "&" : "?"}return=/charge/${listingId}/start`);
        return;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [listingId, router, t.startFailedLoad, langSuffix]);

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
      setError(err instanceof Error ? err.message : t.startFailedStart);
    } finally {
      setStarting(false);
    }
  }

  async function stopHostSession() {
    if (!confirm(t.startConfirmStop)) return;
    setStopping(true);
    try {
      const hs = await api.hostSessionStop(listingId);
      setHostSession(hs);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t.startFailedStop);
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
      setError(err instanceof Error ? err.message : t.startFailedStart);
    } finally {
      setStarting(false);
    }
  }

  if (loading) {
    return (
      <div>
        <Nav />
        <div className="flex items-center justify-center min-h-[calc(100vh-64px)] text-ash text-sm">
          {t.startLoading}
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
                <h2 className="font-semibold text-white mb-1">{t.startHostActiveTitle}</h2>
                <p className="text-ash text-xs mb-4">{t.startHostActiveBody}</p>
                <div className="bg-night rounded-xl p-4 mb-4 text-center">
                  <div className="text-ash text-xs uppercase tracking-widest mb-1">
                    {t.startDelivered}
                  </div>
                  <div className="text-3xl font-bold text-volt">
                    {(hostSession.energy_consumed_wh / 1000).toFixed(2)}{" "}
                    <span className="text-base font-normal text-ash">kWh</span>
                  </div>
                  <div className="text-ash text-xs mt-2">
                    {t.startStarted} {new Date(hostSession.started_at).toLocaleTimeString()} · {t.startExpires}{" "}
                    {new Date(hostSession.expires_at).toLocaleTimeString()}
                  </div>
                </div>
                <button
                  onClick={stopHostSession}
                  disabled={stopping}
                  className="w-full btn-outline text-sm py-3 disabled:opacity-40"
                >
                  {stopping ? t.startStopping : t.startStopCharging}
                </button>
              </>
            ) : (
              <>
                <h2 className="font-semibold text-white mb-1">{t.startHostIdleTitle}</h2>
                <p className="text-ash text-xs mb-4">{t.startHostIdleBody}</p>
                <button
                  onClick={startHostSession}
                  disabled={starting}
                  className="w-full btn-volt text-sm py-3 disabled:opacity-40"
                >
                  {starting ? t.startStarting : t.startStartMy}
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
                <h2 className="text-lg font-semibold text-white mb-1">{t.startChargingStarted}</h2>
                <p className="text-ash text-sm mb-6">
                  {t.startStopsAt.replace("{kwh}", String(ctx.booking_package_kwh))}
                </p>
                <Link href={`/bookings${langSuffix}`} className="btn-outline text-sm">
                  {t.startViewBookings}
                </Link>
              </div>
            ) : (
              <>
                <h2 className="font-semibold text-white mb-1">{t.startReadyTitle}</h2>
                <p className="text-ash text-xs mb-4">
                  {t.startReadyBody.replace("{kwh}", String(ctx.booking_package_kwh))}
                </p>
                <div className="bg-night rounded-xl p-5 mb-4 text-center">
                  <div className="text-ash text-xs uppercase tracking-widest mb-1">{t.startSessionPin}</div>
                  <div className="font-mono text-3xl font-bold text-volt tracking-[0.2em]">
                    {ctx.booking_pin}
                  </div>
                  {ctx.booking_expires_at && (
                    <div className="text-ash text-xs mt-3">
                      {t.startExpiresAt} {new Date(ctx.booking_expires_at).toLocaleTimeString()}
                    </div>
                  )}
                </div>
                <button
                  onClick={startBuyerSession}
                  disabled={starting}
                  className="w-full btn-volt text-sm py-3 disabled:opacity-40"
                >
                  {starting ? t.startStarting : t.startStartCharging}
                </button>
              </>
            )}
          </div>
        )}

        {/* NO ELIGIBLE BOOKING / HOST */}
        {ctx.role === "none" && (
          <div className="card text-center">
            <div className="text-3xl mb-3">🔌</div>
            <h2 className="font-semibold text-white mb-2">{t.startNoBookingTitle}</h2>
            <p className="text-ash text-xs mb-5">{t.startNoBookingBody}</p>
            <Link href={`/charge/${listingId}${langSuffix}`} className="btn-volt text-sm">
              {t.startViewBookingOptions}
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

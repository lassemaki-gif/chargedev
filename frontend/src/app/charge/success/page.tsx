"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Nav } from "@/components/Nav";
import { api, Booking } from "@/lib/api";

function SuccessContent() {
  const params = useSearchParams();
  const router = useRouter();
  const sessionId = params.get("session_id");
  const [booking, setBooking] = useState<Booking | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) { router.push("/charge"); return; }
    const confirm = async () => {
      try {
        // verify endpoint confirms payment with Stripe directly — no webhook needed
        const b = await api.verifyCheckout(sessionId);
        if (b.pin_code) { setBooking(b); return; }
        // PIN not yet available — fall back to polling
        let attempts = 0;
        const poll = async () => {
          try {
            const b2 = await api.bookingBySession(sessionId);
            if (b2.pin_code) { setBooking(b2); return; }
            if (++attempts < 6) setTimeout(poll, 2000);
            else setError("Payment confirmed but PIN generation is taking longer than expected. Check your email or bookings shortly.");
          } catch {
            if (++attempts < 6) setTimeout(poll, 2000);
            else setError("Could not load booking. Please check your bookings page.");
          }
        };
        poll();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not verify payment.");
      }
    };
    confirm();
  }, [sessionId, router]);

  if (!booking && !error) return (
    <>
      <div className="text-4xl mb-4 animate-pulse">⚡</div>
      <h2 className="text-xl font-bold text-white mb-2">Payment confirmed!</h2>
      <p className="text-ash text-sm">Generating your session PIN…</p>
    </>
  );

  if (error) return (
    <>
      <div className="text-4xl mb-4">⚠️</div>
      <p className="text-ash text-sm mb-4">{error}</p>
      <button onClick={() => router.push("/charge")} className="btn-outline w-full text-center text-sm">Back to listings</button>
    </>
  );

  return (
    <>
      <div className="text-5xl mb-4">✅</div>
      <h2 className="text-2xl font-bold text-white mb-2">You&apos;re all set!</h2>
      <p className="text-ash text-sm mb-6">Show this PIN to the host to start your charging session.</p>
      <div className="bg-night rounded-xl p-6 mb-6">
        <p className="text-ash text-xs uppercase tracking-widest mb-2">Your session PIN</p>
        <p className="font-mono text-5xl font-bold text-volt tracking-[0.2em]">{booking!.pin_code}</p>
      </div>
      <div className="text-left space-y-2 text-sm text-ash mb-6">
        <div className="flex justify-between"><span>Charger</span><span className="text-white">{booking!.listing_title}</span></div>
        <div className="flex justify-between"><span>Address</span><span className="text-white">{booking!.listing_address}</span></div>
        <div className="flex justify-between"><span>Package</span><span className="text-white">{booking!.package_kwh} kWh</span></div>
        <div className="flex justify-between"><span>Total paid</span><span className="text-white font-medium">€{booking!.total_eur.toFixed(2)}</span></div>
      </div>
      <button onClick={() => router.push("/charge")} className="btn-outline w-full text-center text-sm">
        Browse more chargers
      </button>
    </>
  );
}

export default function SuccessPage() {
  return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)] px-6">
        <div className="card max-w-md w-full text-center">
          <Suspense fallback={<p className="text-ash text-sm">Loading…</p>}>
            <SuccessContent />
          </Suspense>
        </div>
      </div>
    </div>
  );
}

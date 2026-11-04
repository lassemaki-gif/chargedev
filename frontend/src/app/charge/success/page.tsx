"use client";
import { Suspense, useEffect, useMemo, useState, useCallback } from "react";
import { track } from "@vercel/analytics";
import { useSearchParams, useRouter } from "next/navigation";
import QRCode from "qrcode";
import { Nav } from "@/components/Nav";
import { api, Booking } from "@/lib/api";
import { vatFromGross, netFromGross, VAT_LABEL } from "@/lib/vat";
import { pickUIT, pickUIT2 } from "@/lib/ui";

function SuccessContent() {
  const params = useSearchParams();
  const router = useRouter();
  const bookingIdParam = params.get("booking_id");
  const bookingId = bookingIdParam ? parseInt(bookingIdParam, 10) : null;
  const queryLang = params.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT2(uiLang), [uiLang]);
  const t1 = useMemo(() => pickUIT(uiLang), [uiLang]);
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";
  const [booking, setBooking] = useState<Booking | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [qrSvg, setQrSvg] = useState<string | null>(null);

  const copyPin = useCallback((pin: string) => {
    navigator.clipboard.writeText(pin).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, []);

  // Generate a QR for the Start-charging URL once the booking lands. The QR
  // is scannable from another device (laptop → phone, phone → tablet) and
  // doubles as a visual confirmation; the PIN stays below as a backup.
  useEffect(() => {
    if (!booking?.listing_id) return;
    // Hardcoded production host — a preview URL in the QR would 404 the moment
    // the preview was deprovisioned; a lookalike domain would phish scanners.
    const base = process.env.NEXT_PUBLIC_SITE_URL || "https://chargedev.io";
    const url = `${base}/charge/${booking.listing_id}/start`;
    QRCode.toString(url, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 1,
      color: { dark: "#F7FF2B", light: "#0B0E13" },  // volt on night
      width: 220,
    }).then(setQrSvg).catch(() => setQrSvg(null));
  }, [booking]);

  useEffect(() => {
    if (!bookingId) { router.push("/charge"); return; }
    const confirm = async () => {
      try {
        // verify endpoint confirms payment with SumUp directly — webhook is a backup, not required
        const b = await api.verifyCheckout(bookingId);
        if (b.pin_code) {
          setBooking(b);
          track("booking_completed", {
            listing_id: b.listing_id,
            package_kwh: b.package_kwh,
            total_eur: b.total_eur,
          });
          return;
        }
        // PIN not yet available — fall back to polling
        let attempts = 0;
        const poll = async () => {
          try {
            const b2 = await api.bookingById(bookingId);
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
        setError(err instanceof Error ? err.message : t.successCouldNotVerify);
      }
    };
    confirm();
  }, [bookingId, router, t.successCouldNotVerify]);

  if (!booking && !error) return (
    <>
      <div className="text-4xl mb-4 animate-pulse">⚡</div>
      <h2 className="text-xl font-bold text-white mb-2">{t.successConfirmedTitle}</h2>
      <p className="text-ash text-sm">{t.successGeneratingPin}</p>
    </>
  );

  if (error) return (
    <>
      <div className="text-4xl mb-4">⚠️</div>
      <p className="text-ash text-sm mb-4">{error}</p>
      <button onClick={() => router.push(`/charge${langSuffix}`)} className="btn-outline w-full text-center text-sm">{t.successBackToListings}</button>
    </>
  );

  return (
    <>
      <div className="text-5xl mb-4">✅</div>
      <h2 className="text-2xl font-bold text-white mb-2">{t.successAllSet}</h2>
      <p className="text-ash text-sm mb-6">{t.successScanQr}</p>

      <div className="bg-night rounded-xl p-5 mb-3 flex flex-col items-center">
        {qrSvg ? (
          <div
            className="w-56 h-56 flex items-center justify-center mb-3"
            dangerouslySetInnerHTML={{ __html: qrSvg }}
          />
        ) : (
          <div className="w-56 h-56 flex items-center justify-center text-ash text-xs mb-3">
            {t.successGeneratingQr}
          </div>
        )}
        <p className="text-ash text-xs">{t.successScanToStart}</p>
      </div>

      <button
        onClick={() => router.push(`/charge/${booking!.listing_id}/start${langSuffix}`)}
        className="btn-volt w-full text-sm py-3 mb-4"
      >
        {t.successStartNow} →
      </button>

      <div className="flex items-center justify-between gap-3 mb-6 text-xs">
        <span className="text-ash">{t.successBackupPin}</span>
        <button
          onClick={() => copyPin(booking!.pin_code!)}
          className="font-mono font-semibold text-volt tracking-widest hover:text-white transition-colors"
        >
          {copied ? t.successCopied : booking!.pin_code}
        </button>
      </div>
      <div className="text-left space-y-2 text-sm text-ash mb-4">
        <div className="flex justify-between"><span>{t.successCharger}</span><span className="text-white">{booking!.listing_title}</span></div>
        <div className="flex justify-between"><span>{t.successAddress}</span><span className="text-white">{booking!.listing_address}</span></div>
        <div className="flex justify-between"><span>{t.successPackage}</span><span className="text-white">{booking!.package_kwh} kWh</span></div>
        <div className="flex justify-between border-t border-border pt-2 mt-1">
          <span>{t.successPriceExclVat}</span>
          <span>€{netFromGross(booking!.total_eur).toFixed(2)}</span>
        </div>
        <div className="flex justify-between">
          <span>{VAT_LABEL}</span>
          <span>€{vatFromGross(booking!.total_eur).toFixed(2)}</span>
        </div>
        <div className="flex justify-between font-medium text-white">
          <span>{t.successTotalPaid}</span>
          <span>€{booking!.total_eur.toFixed(2)}</span>
        </div>
      </div>
      <div className="flex gap-3">
        <button onClick={() => router.push(`/bookings${langSuffix}`)} className="btn-volt flex-1 text-sm text-center">
          {t1.navMyBookings}
        </button>
        <button onClick={() => router.push(`/charge${langSuffix}`)} className="btn-outline flex-1 text-sm text-center">
          {t.successFindMore}
        </button>
      </div>
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

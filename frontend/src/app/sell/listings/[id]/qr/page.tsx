"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import QRCode from "qrcode";
import { api } from "@/lib/api";
import { pickUIT2 } from "@/lib/ui";

// Hardcoded production host — the QR will be physically printed and stuck on a
// charger. Deriving from window.location.origin meant preview URLs produced
// QRs that would 404 the moment the preview was deprovisioned, and a host
// viewing from a lookalike domain would print a QR that phished every scanner.
const BASE = process.env.NEXT_PUBLIC_SITE_URL || "https://chargedev.io";

export default function QRCodePage() {
  return (
    <Suspense fallback={null}>
      <QRCodePageInner />
    </Suspense>
  );
}

function QRCodePageInner() {
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
  const listingId = params?.id;
  const [svg, setSvg] = useState<string | null>(null);
  const [listingTitle, setListingTitle] = useState<string | null>(null);
  const [notAllowed, setNotAllowed] = useState(false);

  useEffect(() => {
    if (!listingId) return;
    const idNum = Number(listingId);

    // Ownership gate: only hosts who own this listing can print its QR. A
    // malicious host could otherwise navigate to /sell/listings/<other>/qr,
    // print a legitimate-looking QR for a competitor's charger, and stick
    // it on their own box. We resolve ownership via the seller-scoped
    // listing collection — if the id isn't in it, the user doesn't own it.
    api.myListings()
      .then((rows) => {
        const mine = rows.find((l) => l.id === idNum);
        if (!mine) {
          setNotAllowed(true);
          return;
        }
        setListingTitle(mine.title);
        const url = `${BASE}/charge/${idNum}/start`;
        return QRCode.toString(url, {
          type: "svg",
          errorCorrectionLevel: "M",
          margin: 2,
          color: { dark: "#000000", light: "#FFFFFF" },
          width: 420,
        }).then(setSvg);
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "";
        if (msg === "Not authenticated" || msg === "Invalid token") {
          router.push("/sell");
        } else {
          setNotAllowed(true);
        }
      });
  }, [listingId, router]);

  return (
    <div className="min-h-screen bg-white text-black">
      <style jsx global>{`
        @media print {
          .no-print { display: none !important; }
          body { background: white; }
        }
      `}</style>

      <div className="max-w-xl mx-auto px-6 py-10">
        <div className="no-print mb-6 flex items-center justify-between">
          <a href="/sell/dashboard" className="text-sm text-ash hover:text-black">
            ← {t.qrBackDashboard}
          </a>
          {!notAllowed && (
            <button
              onClick={() => window.print()}
              className="px-4 py-2 bg-black text-white rounded-lg text-sm hover:bg-neutral-800"
            >
              {t.qrPrint}
            </button>
          )}
        </div>

        {notAllowed ? (
          <div className="border border-black rounded-2xl p-8 text-center">
            <div className="text-3xl mb-3">🔒</div>
            <h1 className="text-lg font-semibold mb-2">{t.qrNotYoursTitle}</h1>
            <p className="text-sm text-neutral-600">{t.qrNotYoursBody}</p>
          </div>
        ) : (
        <div className="border border-black rounded-2xl p-8 text-center">
          <div className="mb-4">
            <div className="text-2xl font-mono mb-1">⚡ ChargedEV</div>
            <h1 className="text-lg font-semibold">
              {listingTitle || `${t.qrChargerFallback} #${listingId}`}
            </h1>
          </div>

          {svg ? (
            <div
              className="flex justify-center my-6"
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ) : (
            <div className="h-[420px] flex items-center justify-center text-neutral-400">
              {t.qrGenerating}
            </div>
          )}

          <div className="mt-4 border-t border-neutral-200 pt-4 text-sm">
            <div className="font-semibold mb-1">{t.qrScanToStartTitle}</div>
            <div className="text-neutral-600 text-xs whitespace-pre-line">{t.qrScanBody}</div>
          </div>

          <div className="mt-5 text-[11px] text-neutral-500 font-mono break-all">
            {BASE}/charge/{listingId}/start
          </div>
        </div>
        )}

        <p className="no-print text-xs text-neutral-500 mt-6 text-center">{t.qrPrintHint}</p>
      </div>
    </div>
  );
}

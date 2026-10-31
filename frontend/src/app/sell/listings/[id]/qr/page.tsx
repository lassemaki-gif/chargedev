"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import QRCode from "qrcode";

const BASE = typeof window !== "undefined"
  ? window.location.origin
  : "https://chargedev.io";

export default function QRCodePage() {
  const params = useParams<{ id: string }>();
  const listingId = params?.id;
  const [svg, setSvg] = useState<string | null>(null);
  const [listingTitle, setListingTitle] = useState<string | null>(null);

  useEffect(() => {
    if (!listingId) return;
    const url = `${BASE}/charge/${listingId}/start`;
    QRCode.toString(url, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 2,
      color: { dark: "#000000", light: "#FFFFFF" },
      width: 420,
    }).then(setSvg).catch(() => setSvg(null));

    // Best-effort fetch of the listing title so the printed sheet is self-labelling.
    fetch(`/api/proxy/api/listings/${listingId}`, { credentials: "include" })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => data && setListingTitle(data.title))
      .catch(() => { /* ignore */ });
  }, [listingId]);

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
            ← Back to dashboard
          </a>
          <button
            onClick={() => window.print()}
            className="px-4 py-2 bg-black text-white rounded-lg text-sm hover:bg-neutral-800"
          >
            Print
          </button>
        </div>

        <div className="border border-black rounded-2xl p-8 text-center">
          <div className="mb-4">
            <div className="text-2xl font-mono mb-1">⚡ ChargedEV</div>
            <h1 className="text-lg font-semibold">
              {listingTitle || `Charger #${listingId}`}
            </h1>
          </div>

          {svg ? (
            <div
              className="flex justify-center my-6"
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ) : (
            <div className="h-[420px] flex items-center justify-center text-neutral-400">
              Generating…
            </div>
          )}

          <div className="mt-4 border-t border-neutral-200 pt-4 text-sm">
            <div className="font-semibold mb-1">Scan to start charging</div>
            <div className="text-neutral-600 text-xs">
              Already paid? Scan to start.
              <br />
              New here? Scan to see available packages.
            </div>
          </div>

          <div className="mt-5 text-[11px] text-neutral-500 font-mono break-all">
            {BASE}/charge/{listingId}/start
          </div>
        </div>

        <p className="no-print text-xs text-neutral-500 mt-6 text-center">
          Print this sheet and stick it on the charger. Buyers scan it after
          booking to start their session; you scan it to charge your own car.
        </p>
      </div>
    </div>
  );
}

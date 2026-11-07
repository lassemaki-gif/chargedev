"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Nav } from "@/components/Nav";
import { AvailabilityGrid, DEFAULT_AVAILABILITY, WeeklyAvailability } from "@/components/AvailabilityGrid";
import { api } from "@/lib/api";
import { useRouter, useSearchParams } from "next/navigation";
import { pickUIT2 } from "@/lib/ui";

const CHARGER_TYPES = ["3-phase", "Type 2", "CCS", "CHAdeMO", "CEE"];

export default function NewListing() {
  return (
    <Suspense fallback={null}>
      <NewListingInner />
    </Suspense>
  );
}

function NewListingInner() {
  const router = useRouter();
  const search = useSearchParams();
  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT2(uiLang), [uiLang]);
  const [availability, setAvailability] = useState<WeeklyAvailability>(DEFAULT_AVAILABILITY);
  const [form, setForm] = useState({
    title: "",
    description: "",
    address: "",
    city: "",
    country: "Finland",
    charger_type: "Type 2",
    max_power_kw: 11,
    price_per_kwh: 0.30,
    instructions: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [geocodeWarning, setGeocodeWarning] = useState<number | null>(null);

  const set = (k: string, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const created = await api.createListing(form);
      await api.setAvailability(created.id, availability);
      if (created.lat == null || created.lng == null) {
        // Listing saved but Nominatim couldn't resolve the address — keep the
        // host on the page so they can fix the spelling while context is fresh.
        setGeocodeWarning(created.id);
      } else {
        router.push("/sell/dashboard");
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t.listingFailedGeneric);
    } finally {
      setLoading(false);
    }
  }

  const estimatedMonthly = (form.price_per_kwh / 1.255 * 40 * 0.80 * 10).toFixed(0);

  return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-12 max-w-2xl">
        <button onClick={() => router.back()} className="text-ash text-sm hover:text-white mb-6 flex items-center gap-2">← {t.listingBack}</button>
        <h1 className="text-3xl font-bold text-white mb-2">{t.listingCreateTitle}</h1>
        <p className="text-ash mb-8">{t.listingCreateSubtitle}</p>

        {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-6">{error}</div>}

        {geocodeWarning != null && (
          <div className="bg-yellow-900/20 border border-yellow-700/60 text-yellow-200 rounded-lg px-4 py-3 text-sm mb-6">
            <div className="font-semibold mb-1">⚠️ {t.listingGeocodeWarningTitle}</div>
            <p className="mb-3">{t.listingGeocodeWarningBody}</p>
            <div className="flex gap-2">
              <button
                onClick={() => router.push(`/sell/listing/${geocodeWarning}/edit`)}
                className="btn-outline text-sm px-4 py-1.5"
              >
                {t.listingGeocodeWarningEditAddress}
              </button>
              <button
                onClick={() => router.push("/sell/dashboard")}
                className="btn-volt text-sm px-4 py-1.5"
              >
                {t.listingGeocodeWarningContinue}
              </button>
            </div>
          </div>
        )}

        <form onSubmit={submit} className="space-y-5">
          <div>
            <label className="label">{t.listingTitleLabel}</label>
            <input className="input" placeholder={t.listingTitlePlaceholder} value={form.title} onChange={(e) => set("title", e.target.value)} required />
          </div>

          <div>
            <label className="label">{t.listingDescLabel}</label>
            <textarea className="input min-h-[80px] resize-none" placeholder={t.listingDescPlaceholder} value={form.description} onChange={(e) => set("description", e.target.value)} />
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="label">{t.listingAddressLabel}</label>
              <input
                className="input"
                placeholder={t.listingAddressPlaceholder}
                value={form.address}
                onChange={(e) => set("address", e.target.value)}
                pattern=".*\d+.*"
                title={t.listingAddressHint}
                required
              />
              <p className="text-ash text-xs mt-1">{t.listingAddressHint}</p>
            </div>
            <div>
              <label className="label">{t.listingCityLabel}</label>
              <input className="input" placeholder={t.listingCityPlaceholder} value={form.city} onChange={(e) => set("city", e.target.value)} required />
            </div>
          </div>

          <div>
            <label className="label">{t.listingCountryLabel}</label>
            <input className="input" value={form.country} onChange={(e) => set("country", e.target.value)} />
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="label">{t.listingChargerTypeLabel}</label>
              <select className="input" value={form.charger_type} onChange={(e) => set("charger_type", e.target.value)}>
                {CHARGER_TYPES.map((ct) => <option key={ct}>{ct}</option>)}
              </select>
            </div>
            <div>
              <label className="label">{t.listingMaxPowerLabel}</label>
              <input className="input" type="number" min={1.4} max={350} step={0.1} value={form.max_power_kw} onChange={(e) => set("max_power_kw", parseFloat(e.target.value))} />
            </div>
          </div>

          <div>
            <label className="label">{t.listingPriceLabel}</label>
            <input className="input" type="number" min={0.15} max={0.95} step={0.01} value={form.price_per_kwh} onChange={(e) => set("price_per_kwh", parseFloat(e.target.value))} />
            <p className="text-ash text-xs mt-1.5">
              {t.listingEstimatedMonthly} <span className="text-volt font-medium">€{estimatedMonthly}</span>
            </p>
          </div>

          <div>
            <label className="label">{t.listingAccessInstructionsLabel}</label>
            <textarea className="input min-h-[80px] resize-none" placeholder={t.listingAccessInstructionsPlaceholder} value={form.instructions} onChange={(e) => set("instructions", e.target.value)} />
          </div>

          <div>
            <label className="label mb-3 block">{t.listingAvailabilityLabel}</label>
            <AvailabilityGrid value={availability} onChange={setAvailability} />
          </div>

          <button type="submit" disabled={loading} className="btn-volt w-full text-center">
            {loading ? t.listingCreating : t.listingPublish}
          </button>
        </form>
      </div>
    </div>
  );
}

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { markets, marketById, packagePrice } from "@/lib/markets";

export async function generateStaticParams() {
  return markets.map((m) => ({ country: m.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ country: string }> }): Promise<Metadata> {
  const { country } = await params;
  const m = marketById[country];
  if (!m) return {};
  const title = `ChargedEV ${m.countryNameEn} — ${m.t.headline1} ${m.t.headline2}`;
  return {
    title,
    description: m.t.body,
    alternates: { canonical: `https://chargedev.io/${m.id}` },
    openGraph: { title, description: m.t.body, url: `https://chargedev.io/${m.id}` },
  };
}

const PACKAGES = [
  { kwh: 20, label: "⚡ Quick" },
  { kwh: 40, label: "🚗 Day trip" },
  { kwh: 60, label: "🛣️ Long run" },
  { kwh: 80, label: "🔋 Full tank" },
];

export default async function CountryPage({ params }: { params: Promise<{ country: string }> }) {
  const { country } = await params;
  const m = marketById[country];
  if (!m) notFound();
  const { t } = m;

  return (
    <div className="min-h-screen">
      {/* Nav */}
      <nav className="border-b border-border px-6 lg:px-16 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-volt font-mono text-xl">⚡</span>
          <span className="font-semibold text-white text-lg tracking-tight">ChargedEV</span>
        </Link>
        <div className="flex items-center gap-3">
          <Link href="/charge" className="text-ash hover:text-white text-sm font-medium transition-colors">{t.findCharger}</Link>
          <Link href="/sell" className="btn-volt text-sm py-2 px-4">{t.becomeHost}</Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative px-6 lg:px-16 pt-24 pb-28 overflow-hidden">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-volt/5 rounded-full blur-3xl pointer-events-none" />
        <div className="relative max-w-4xl">
          <p className="text-volt text-xs font-semibold uppercase tracking-[0.25em] mb-6">{t.kicker}</p>
          <h1 className="text-5xl lg:text-7xl font-bold text-white leading-[1.05] tracking-tight mb-6">
            {t.headline1}<br />
            <span className="text-volt">{t.headline2}</span>
          </h1>
          <p className="text-xl text-ash max-w-xl mb-10 leading-relaxed">{t.body}</p>
          <div className="flex flex-wrap gap-4">
            <Link href="/charge" className="btn-volt text-base">{t.findCharger} →</Link>
            <Link href="/sell" className="btn-outline text-base">{t.becomeHost}</Link>
          </div>
        </div>

        {/* Package prices */}
        <div className="relative mt-20">
          <p className="text-ash text-xs uppercase tracking-widest mb-4">{t.pkg} {packagePrice(m, 20)}</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 max-w-2xl">
            {PACKAGES.map(({ kwh, label }) => (
              <div key={kwh} className="card text-center">
                <div className="text-2xl font-bold text-white">{kwh}<span className="text-sm font-normal text-ash"> kWh</span></div>
                <div className="text-volt font-semibold mt-1">{packagePrice(m, kwh)}</div>
                <div className="text-ash text-xs mt-1">{label}</div>
              </div>
            ))}
          </div>
          <p className="text-ash text-xs mt-3 opacity-60">{m.currencySymbol}{m.pricePerKwh}/kWh · {m.cities}</p>
        </div>
      </section>

      {/* Trust strip */}
      <section className="border-t border-border px-6 lg:px-16 py-12 grid sm:grid-cols-3 gap-6 max-w-3xl">
        {[
          { icon: "🔐", text: "PIN-secured sessions" },
          { icon: "💳", text: "Pay before you plug" },
          { icon: "⚡", text: "20–80 kWh packages" },
        ].map((f) => (
          <div key={f.text} className="flex items-center gap-3">
            <span className="text-2xl">{f.icon}</span>
            <span className="text-sm text-ash">{f.text}</span>
          </div>
        ))}
      </section>

      {/* CTAs */}
      <section className="border-t border-border px-6 lg:px-16 py-16 flex flex-col sm:flex-row gap-6 items-start sm:items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white mb-1">{t.becomeHost}</h2>
          <p className="text-ash text-sm">Earn {packagePrice(m, 80)} per 80 kWh session.</p>
        </div>
        <div className="flex gap-3">
          <Link href="/sell" className="btn-volt">{t.becomeHost}</Link>
          <Link href="/charge" className="btn-outline">{t.findCharger}</Link>
        </div>
      </section>

      <footer className="border-t border-border px-6 lg:px-16 py-6 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-volt font-mono">⚡</span>
          <span className="text-ash text-sm">ChargedEV</span>
        </Link>
        <span className="text-ash text-xs">{m.countryNameEn} · chargedev.io/{m.id}</span>
      </footer>
    </div>
  );
}

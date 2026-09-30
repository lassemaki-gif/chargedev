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
  { kwh: 20, label: "Quick charge" },
  { kwh: 40, label: "Day trip" },
  { kwh: 60, label: "Long run" },
  { kwh: 80, label: "Full tank" },
];

export default async function CountryPage({ params }: { params: Promise<{ country: string }> }) {
  const { country } = await params;
  const m = marketById[country];
  if (!m) notFound();
  const { t } = m;

  return (
    <div className="min-h-screen">
      {/* Nav */}
      <nav className="border-b border-border px-4 sm:px-6 lg:px-16 h-14 sm:h-16 flex items-center justify-between gap-3">
        <Link href="/" className="flex items-center gap-2 shrink-0">
          <span className="text-volt font-mono text-lg sm:text-xl">⚡</span>
          <span className="font-semibold text-white text-base sm:text-lg tracking-tight">ChargedEV</span>
        </Link>
        <div className="flex items-center gap-2">
          <Link href="/charge" className="hidden sm:block text-ash hover:text-white text-sm font-medium transition-colors px-2">
            {t.findCharger}
          </Link>
          <Link href="/sell" className="btn-volt text-sm py-1.5 px-3 sm:py-2 sm:px-4">
            <span className="hidden sm:inline">{t.becomeHost}</span>
            <span className="sm:hidden">Host</span>
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative px-4 sm:px-6 lg:px-16 pt-14 sm:pt-24 pb-14 sm:pb-28 overflow-hidden">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-volt/5 rounded-full blur-3xl pointer-events-none" />
        <div className="relative max-w-4xl">
          <div className="inline-flex items-center gap-2 bg-volt/10 border border-volt/20 rounded-full px-3 sm:px-4 py-1.5 text-volt text-xs sm:text-sm font-medium mb-6 sm:mb-8">
            <span>⚡</span>
            <span>{t.kicker}</span>
          </div>
          <h1 className="text-4xl sm:text-5xl lg:text-7xl font-bold text-white leading-[1.05] tracking-tight mb-5 sm:mb-6">
            {t.headline1}<br />
            <span className="text-volt">{t.headline2}</span>
          </h1>
          <p className="text-lg sm:text-xl text-ash max-w-xl mb-7 sm:mb-10 leading-relaxed">{t.body}</p>
          <div className="flex flex-wrap gap-3">
            <Link href="/charge" className="btn-volt text-sm sm:text-base">{t.findCharger} →</Link>
            <Link href="/sell" className="btn-outline text-sm sm:text-base">{t.becomeHost}</Link>
          </div>
        </div>

        {/* Package prices */}
        <div className="relative mt-10 sm:mt-20">
          <p className="text-ash text-xs uppercase tracking-widest mb-4">{t.pkg} {packagePrice(m, 20)}</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 max-w-2xl">
            {PACKAGES.map(({ kwh, label }) => (
              <div key={kwh} className="card text-center">
                <div className="text-xl sm:text-2xl font-bold text-white">{kwh}<span className="text-sm font-normal text-ash"> kWh</span></div>
                <div className="text-volt font-semibold mt-1">{packagePrice(m, kwh)}</div>
                <div className="text-ash text-xs mt-1">{label}</div>
              </div>
            ))}
          </div>
          <p className="text-ash text-xs mt-3 opacity-60">{m.currencySymbol}{m.pricePerKwh}/kWh · {m.cities}</p>
        </div>
      </section>

      {/* Payment trust strip */}
      <section className="border-t border-border bg-card/50 px-4 sm:px-6 lg:px-16 py-6 sm:py-8">
        <div className="flex flex-wrap items-center gap-4 sm:gap-8 lg:gap-12">
          <div className="flex items-center gap-2.5 text-ash">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-volt shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            <span className="text-sm">Secured by <span className="text-white font-medium">Stripe</span></span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {["Visa", "Mastercard", "Amex", "Apple Pay", "Google Pay"].map((brand) => (
              <span key={brand} className="text-xs font-medium text-ash border border-border rounded px-2 py-0.5">{brand}</span>
            ))}
          </div>
        </div>
      </section>

      {/* Trust features */}
      <section className="border-t border-border px-4 sm:px-6 lg:px-16 py-10 sm:py-16 grid sm:grid-cols-3 gap-6 sm:gap-8 max-w-3xl">
        {[
          { icon: "🔐", t: "PIN-secured sessions", b: "Every booking generates a unique 6-digit PIN. No PIN, no charge." },
          { icon: "⚡", t: "Instant confirmation", b: "Your PIN arrives the moment payment clears — no waiting." },
          { icon: "🌍", t: "Available in 59 markets", b: "Home charging network available worldwide." },
        ].map((f) => (
          <div key={f.t} className="flex flex-col gap-2">
            <span className="text-2xl">{f.icon}</span>
            <div className="font-semibold text-white text-sm">{f.t}</div>
            <div className="text-ash text-xs sm:text-sm leading-relaxed">{f.b}</div>
          </div>
        ))}
      </section>

      {/* CTA banner */}
      <section className="border-t border-border px-4 sm:px-6 lg:px-16 py-12 sm:py-16 flex flex-col sm:flex-row gap-5 sm:gap-6 items-start sm:items-center justify-between">
        <div>
          <h2 className="text-xl sm:text-2xl font-bold text-white mb-1">{t.becomeHost}</h2>
          <p className="text-ash text-sm">Earn {packagePrice(m, 80)} per 80 kWh session. No subscription.</p>
        </div>
        <div className="flex gap-3 shrink-0">
          <Link href="/sell" className="btn-volt text-sm">{t.becomeHost}</Link>
          <Link href="/charge" className="btn-outline text-sm">{t.findCharger}</Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border px-4 sm:px-6 lg:px-16 py-8 sm:py-10">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-volt font-mono">⚡</span>
              <span className="text-white font-semibold text-sm">ChargedEV</span>
            </div>
            <p className="text-ash text-xs">{m.countryNameEn} · chargedev.io/{m.id}</p>
          </div>
          <div className="flex items-center gap-1.5 text-ash text-xs">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            <span>Payments secured by <span className="text-white/70">Stripe</span></span>
          </div>
        </div>
      </footer>
    </div>
  );
}

import Link from "next/link";

const PACKAGES = [
  { kwh: 20, label: "City hop", km: "~80 km", eur: "5.00" },
  { kwh: 40, label: "Day trip", km: "~160 km", eur: "10.00" },
  { kwh: 60, label: "Long run", km: "~250 km", eur: "15.00" },
  { kwh: 80, label: "Full tank", km: "~320 km", eur: "20.00" },
];

export default function Landing() {
  return (
    <div className="min-h-screen">
      {/* Nav */}
      <nav className="border-b border-border px-6 lg:px-16 h-16 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-volt font-mono text-xl">⚡</span>
          <span className="font-semibold text-white text-lg tracking-tight">ChargedEV</span>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/charge" className="text-ash hover:text-white text-sm font-medium transition-colors">Find a charger</Link>
          <Link href="/sell" className="btn-volt text-sm py-2 px-4">Become a host</Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative px-6 lg:px-16 pt-28 pb-32 overflow-hidden">
        {/* Glow */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-volt/5 rounded-full blur-3xl pointer-events-none" />

        <div className="relative max-w-4xl">
          <div className="inline-flex items-center gap-2 bg-volt/10 border border-volt/20 rounded-full px-4 py-1.5 text-volt text-sm font-medium mb-8">
            <span>⚡</span>
            <span>The home charging network</span>
          </div>
          <h1 className="text-5xl lg:text-7xl font-bold text-white leading-[1.05] tracking-tight mb-6">
            Your outlet.<br />
            <span className="text-volt">Their charge.</span><br />
            Your income.
          </h1>
          <p className="text-xl text-ash max-w-xl mb-10 leading-relaxed">
            Got a home charger or three-phase socket? List it on ChargedEV and earn money every time an EV driver charges up. Available worldwide.
          </p>
          <div className="flex flex-wrap gap-4">
            <Link href="/sell" className="btn-volt text-base">Start hosting →</Link>
            <Link href="/charge" className="btn-outline text-base">Find a charger</Link>
          </div>
        </div>

        {/* Stats strip */}
        <div className="relative mt-20 flex flex-wrap gap-10">
          {[
            { n: "€0.25", label: "per kWh in Finland" },
            { n: "80%", label: "goes to the host" },
            { n: "4 sizes", label: "20 · 40 · 60 · 80 kWh" },
            { n: "6-digit PIN", label: "secure session access" },
          ].map((s) => (
            <div key={s.n}>
              <div className="text-3xl font-bold text-white">{s.n}</div>
              <div className="text-ash text-sm mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="border-t border-border px-6 lg:px-16 py-24">
        <div className="grid lg:grid-cols-2 gap-16">
          {/* Sellers */}
          <div>
            <p className="text-volt text-sm font-semibold uppercase tracking-widest mb-4">For hosts</p>
            <h2 className="text-3xl font-bold text-white mb-8">Earn while your car charges elsewhere</h2>
            <div className="space-y-6">
              {[
                { n: "01", t: "List your charger", b: "Register, add your address, charger type, and set your price per kWh. Takes 5 minutes." },
                { n: "02", t: "Accept bookings", b: "Buyers pick a package (20–80 kWh) and pay upfront. You get notified instantly." },
                { n: "03", t: "Share the PIN", b: "A secure 6-digit PIN is generated per booking. Share it, they charge, you earn." },
              ].map((s) => (
                <div key={s.n} className="flex gap-5">
                  <span className="font-mono text-volt text-sm mt-1 shrink-0">{s.n}</span>
                  <div>
                    <div className="font-semibold text-white mb-1">{s.t}</div>
                    <div className="text-ash text-sm leading-relaxed">{s.b}</div>
                  </div>
                </div>
              ))}
            </div>
            <Link href="/sell" className="btn-volt inline-flex mt-8 text-sm">Start hosting</Link>
          </div>

          {/* Buyers */}
          <div>
            <p className="text-blue-400 text-sm font-semibold uppercase tracking-widest mb-4">For drivers</p>
            <h2 className="text-3xl font-bold text-white mb-8">Charge at homes near you</h2>
            <div className="space-y-6">
              {[
                { n: "01", t: "Find a charger", b: "Browse available home chargers by city. See power, charger type, and price upfront." },
                { n: "02", t: "Choose a package", b: "Pick 20, 40, 60, or 80 kWh. Pay instantly. No hourly guesswork — you know the cost." },
                { n: "03", t: "Get your PIN, start charging", b: "A 6-digit PIN unlocks your session. Plug in and go." },
              ].map((s) => (
                <div key={s.n} className="flex gap-5">
                  <span className="font-mono text-blue-400 text-sm mt-1 shrink-0">{s.n}</span>
                  <div>
                    <div className="font-semibold text-white mb-1">{s.t}</div>
                    <div className="text-ash text-sm leading-relaxed">{s.b}</div>
                  </div>
                </div>
              ))}
            </div>
            <Link href="/charge" className="inline-flex mt-8 border border-blue-500/40 text-blue-400 font-medium px-6 py-3 rounded-lg hover:border-blue-400 hover:bg-blue-900/20 transition-all text-sm">
              Find a charger
            </Link>
          </div>
        </div>
      </section>

      {/* Packages */}
      <section className="border-t border-border px-6 lg:px-16 py-24 bg-card/30">
        <div className="mb-12">
          <p className="text-volt text-sm font-semibold uppercase tracking-widest mb-3">Packages</p>
          <h2 className="text-3xl font-bold text-white">Simple, predictable pricing</h2>
          <p className="text-ash mt-2">At €0.25/kWh — prices vary per host.</p>
        </div>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {PACKAGES.map((pkg, i) => (
            <div key={pkg.kwh} className={`card flex flex-col gap-3 ${i === 1 ? "border-volt/40" : ""}`}>
              {i === 1 && <span className="badge-green self-start">Most popular</span>}
              <div className="text-3xl font-bold text-white">{pkg.kwh} <span className="text-lg font-normal text-ash">kWh</span></div>
              <div className="text-volt font-semibold text-lg">€{pkg.eur}</div>
              <div className="text-ash text-sm">{pkg.label} · {pkg.km}</div>
            </div>
          ))}
        </div>
        <p className="text-ash text-xs mt-4">Prices shown at €0.25/kWh. Actual price set by each host. 20% platform fee applies.</p>
      </section>

      {/* Trust */}
      <section className="border-t border-border px-6 lg:px-16 py-24">
        <div className="grid sm:grid-cols-3 gap-8 max-w-3xl">
          {[
            { icon: "🔐", t: "PIN-secured sessions", b: "Every booking generates a unique 6-digit PIN. No PIN, no charge." },
            { icon: "💳", t: "Pay before you plug", b: "Buyers pay upfront. Hosts earn as soon as the session is confirmed." },
            { icon: "🌍", t: "Built for global markets", b: "Starting in Finland, available anywhere with home EV charging." },
          ].map((f) => (
            <div key={f.t} className="flex flex-col gap-3">
              <span className="text-3xl">{f.icon}</span>
              <div className="font-semibold text-white">{f.t}</div>
              <div className="text-ash text-sm leading-relaxed">{f.b}</div>
            </div>
          ))}
        </div>
      </section>

      {/* CTA banner */}
      <section className="border-t border-border px-6 lg:px-16 py-20 flex flex-col lg:flex-row items-start lg:items-center justify-between gap-8">
        <div>
          <h2 className="text-3xl font-bold text-white">Ready to start?</h2>
          <p className="text-ash mt-2">Join as a host and start earning in minutes.</p>
        </div>
        <div className="flex gap-4">
          <Link href="/sell" className="btn-volt">Become a host</Link>
          <Link href="/charge" className="btn-outline">Find a charger</Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border px-6 lg:px-16 py-8 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-volt font-mono">⚡</span>
          <span className="text-ash text-sm">ChargedEV</span>
        </div>
        <div className="flex gap-6 text-ash text-sm">
          <Link href="/sell" className="hover:text-white transition-colors">Host</Link>
          <Link href="/charge" className="hover:text-white transition-colors">Driver</Link>
          <Link href="/admin" className="hover:text-white transition-colors">Admin</Link>
        </div>
      </footer>
    </div>
  );
}

"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api, Listing } from "@/lib/api";

export default function BrowseChargers() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [city, setCity] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listings().then(setListings).finally(() => setLoading(false));
  }, []);

  const filtered = city
    ? listings.filter((l) => l.city.toLowerCase().includes(city.toLowerCase()))
    : listings;

  function chargerIcon(type: string) {
    if (type === "CCS") return "🔵";
    if (type === "CHAdeMO") return "🟠";
    if (type === "Schuko") return "🔌";
    return "⚡";
  }

  return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-12 max-w-5xl">
        <h1 className="text-3xl font-bold text-white mb-2">Find a charger</h1>
        <p className="text-ash mb-8">Browse home EV chargers available near you.</p>

        <div className="flex gap-3 mb-8">
          <input
            className="input max-w-xs"
            placeholder="Filter by city…"
            value={city}
            onChange={(e) => setCity(e.target.value)}
          />
          <button onClick={() => setCity("")} className={`text-ash text-sm hover:text-white transition-colors ${!city && "opacity-0 pointer-events-none"}`}>
            Clear
          </button>
        </div>

        {loading && <div className="text-ash">Loading chargers…</div>}

        {!loading && filtered.length === 0 && (
          <div className="card text-ash text-sm">
            No chargers found{city ? ` in "${city}"` : ""}. Try a different city or check back later.
          </div>
        )}

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((l) => (
            <Link key={l.id} href={`/charge/${l.id}`} className="card hover:border-volt/40 transition-colors group">
              <div className="flex items-start justify-between mb-3">
                <span className="text-2xl">{chargerIcon(l.charger_type)}</span>
                <span className="badge-green">{l.is_available ? "Available" : "Unavailable"}</span>
              </div>
              <h3 className="font-semibold text-white group-hover:text-volt transition-colors mb-1 line-clamp-2">{l.title}</h3>
              <p className="text-ash text-sm mb-4">{l.city} · {l.charger_type} · {l.max_power_kw} kW</p>
              <div className="flex items-end justify-between">
                <div>
                  <span className="text-volt font-bold text-lg">€{l.price_per_kwh.toFixed(2)}</span>
                  <span className="text-ash text-sm">/kWh</span>
                </div>
                <span className="text-ash text-xs">by {l.seller_name}</span>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

"use client";
// Metadata is set in the parent layout — this is a client component
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api, Listing } from "@/lib/api";

const ChargerMap = dynamic(() => import("@/components/ChargerMap"), {
  ssr: false,
  loading: () => <div className="h-full flex items-center justify-center text-ash text-sm">Loading map…</div>,
});

export default function BrowseChargers() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [city, setCity] = useState("");
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"list" | "map">("list");

  useEffect(() => {
    api.listings().then(setListings).finally(() => setLoading(false));
  }, []);

  const filtered = city
    ? listings.filter((l) => l.city.toLowerCase().includes(city.toLowerCase()))
    : listings;

  function chargerIcon(type: string) {
    if (type === "CCS") return "🔵";
    if (type === "CHAdeMO") return "🟠";
    if (type === "CEE") return "🔌";
    if (type === "3-phase") return "⚡";
    return "⚡";
  }

  return (
    <div className="flex flex-col h-screen">
      <Nav />

      {/* Toolbar */}
      <div className="px-6 lg:px-16 py-4 border-b border-border flex items-center gap-4 flex-wrap">
        <div className="flex-1 min-w-0">
          <input
            className="input max-w-xs"
            placeholder="Filter by city…"
            value={city}
            onChange={(e) => setCity(e.target.value)}
          />
        </div>
        {/* Mobile view toggle */}
        <div className="flex lg:hidden gap-1 border border-border rounded-lg p-0.5">
          {(["list", "map"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${view === v ? "bg-white/10 text-white" : "text-ash"}`}
            >
              {v === "list" ? "List" : "Map"}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 min-h-0">
        {/* List — always visible on desktop, toggleable on mobile */}
        <div className={`w-full lg:w-[420px] lg:flex flex-col overflow-y-auto border-r border-border ${view === "list" ? "flex" : "hidden"}`}>
          {loading && <p className="text-ash p-6 text-sm">Loading chargers…</p>}
          {!loading && filtered.length === 0 && (
            <div className="card m-6 text-ash text-sm">
              No chargers found{city ? ` in "${city}"` : ""}. Try a different city.
            </div>
          )}
          <div className="p-4 space-y-3">
            {filtered.map((l) => (
              <Link key={l.id} href={`/charge/${l.id}`} className="card hover:border-volt/40 transition-colors group block">
                <div className="flex items-start justify-between mb-2">
                  <span className="text-xl">{chargerIcon(l.charger_type)}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${l.is_available ? "bg-green-900/40 text-green-400" : "bg-gray-800 text-gray-400"}`}>
                    {l.is_available ? "Available" : "Unavailable"}
                  </span>
                </div>
                <h3 className="font-semibold text-white group-hover:text-volt transition-colors mb-1 line-clamp-2">{l.title}</h3>
                <p className="text-ash text-sm mb-3">{l.city} · {l.charger_type} · {l.max_power_kw} kW</p>
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

        {/* Map — always visible on desktop, toggleable on mobile */}
        <div className={`flex-1 ${view === "map" ? "flex" : "hidden"} lg:flex`} style={{ minHeight: 0 }}>
          <div className="w-full h-full">
            <ChargerMap listings={filtered} />
          </div>
        </div>
      </div>
    </div>
  );
}

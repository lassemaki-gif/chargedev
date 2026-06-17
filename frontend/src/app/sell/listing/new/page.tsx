"use client";
import { useState } from "react";
import { Nav } from "@/components/Nav";
import { AvailabilityGrid, DEFAULT_AVAILABILITY, WeeklyAvailability } from "@/components/AvailabilityGrid";
import { api } from "@/lib/api";
import { useRouter } from "next/navigation";

const CHARGER_TYPES = ["3-phase", "Type 2", "CCS", "CHAdeMO", "CEE"];

export default function NewListing() {
  const router = useRouter();
  const [availability, setAvailability] = useState<WeeklyAvailability>(DEFAULT_AVAILABILITY);
  const [form, setForm] = useState({
    title: "",
    description: "",
    address: "",
    city: "",
    country: "Finland",
    charger_type: "Type 2",
    max_power_kw: 11,
    price_per_kwh: 0.25,
    instructions: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const set = (k: string, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const created = await api.createListing(form);
      await api.setAvailability(created.id, availability);
      router.push("/sell/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  const estimatedMonthly = (form.price_per_kwh * 40 * 0.80 * 10).toFixed(0);

  return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-12 max-w-2xl">
        <button onClick={() => router.back()} className="text-ash text-sm hover:text-white mb-6 flex items-center gap-2">← Back</button>
        <h1 className="text-3xl font-bold text-white mb-2">List your charger</h1>
        <p className="text-ash mb-8">Add your charging location. Buyers will find it in the marketplace.</p>

        {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-6">{error}</div>}

        <form onSubmit={submit} className="space-y-5">
          <div>
            <label className="label">Listing title</label>
            <input className="input" placeholder="e.g. Fast Type 2 charger in Kallio, Helsinki" value={form.title} onChange={(e) => set("title", e.target.value)} required />
          </div>

          <div>
            <label className="label">Description (optional)</label>
            <textarea className="input min-h-[80px] resize-none" placeholder="Tell drivers what to expect — parking, access, etc." value={form.description} onChange={(e) => set("description", e.target.value)} />
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="label">Street address</label>
              <input className="input" placeholder="Mannerheimintie 1" value={form.address} onChange={(e) => set("address", e.target.value)} required />
            </div>
            <div>
              <label className="label">City</label>
              <input className="input" placeholder="Helsinki" value={form.city} onChange={(e) => set("city", e.target.value)} required />
            </div>
          </div>

          <div>
            <label className="label">Country</label>
            <input className="input" value={form.country} onChange={(e) => set("country", e.target.value)} />
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <label className="label">Charger type</label>
              <select className="input" value={form.charger_type} onChange={(e) => set("charger_type", e.target.value)}>
                {CHARGER_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Max power (kW)</label>
              <input className="input" type="number" min={1.4} max={350} step={0.1} value={form.max_power_kw} onChange={(e) => set("max_power_kw", parseFloat(e.target.value))} />
            </div>
          </div>

          <div>
            <label className="label">Price per kWh (€)</label>
            <input className="input" type="number" min={0.05} max={2} step={0.01} value={form.price_per_kwh} onChange={(e) => set("price_per_kwh", parseFloat(e.target.value))} />
            <p className="text-ash text-xs mt-1.5">
              Estimated monthly earnings (10 × 40 kWh sessions, 80% cut): <span className="text-volt font-medium">€{estimatedMonthly}</span>
            </p>
          </div>

          <div>
            <label className="label">Access instructions</label>
            <textarea className="input min-h-[80px] resize-none" placeholder="e.g. Gate code is 1234. Press doorbell and I'll share the PIN." value={form.instructions} onChange={(e) => set("instructions", e.target.value)} />
          </div>

          <div>
            <label className="label mb-3 block">Availability</label>
            <AvailabilityGrid value={availability} onChange={setAvailability} />
          </div>

          <button type="submit" disabled={loading} className="btn-volt w-full text-center">
            {loading ? "Creating listing…" : "Publish charger"}
          </button>
        </form>
      </div>
    </div>
  );
}

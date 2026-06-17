import type { Metadata } from "next";

const BASE_API = process.env.NEXT_PUBLIC_API_BASE ?? "https://chargedev-production.up.railway.app";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  try {
    const res = await fetch(`${BASE_API}/api/listings/${id}`, { next: { revalidate: 3600 } });
    if (!res.ok) throw new Error();
    const l = await res.json();
    const title = `${l.title} — ${l.city}`;
    const description = `${l.charger_type} · ${l.max_power_kw} kW · €${l.price_per_kwh.toFixed(2)}/kWh in ${l.city}. Book 20–80 kWh instantly on ChargedEV.`;
    return {
      title,
      description,
      alternates: { canonical: `https://chargedev.io/charge/${id}` },
      openGraph: { title, description, url: `https://chargedev.io/charge/${id}` },
    };
  } catch {
    return { title: "EV Charger | ChargedEV" };
  }
}

export default function ChargerLayout({ children }: { children: React.ReactNode }) {
  return children;
}

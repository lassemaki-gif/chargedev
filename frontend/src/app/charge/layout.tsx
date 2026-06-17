import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Find a charger near you",
  description: "Browse available home EV chargers on a map. Book a 20–80 kWh package and get a PIN instantly.",
  alternates: { canonical: "https://chargedev.io/charge" },
  openGraph: {
    title: "Find a charger near you | ChargedEV",
    description: "Browse available home EV chargers on a map. Book a 20–80 kWh package and get a PIN instantly.",
    url: "https://chargedev.io/charge",
  },
};

export default function ChargeLayout({ children }: { children: React.ReactNode }) {
  return children;
}

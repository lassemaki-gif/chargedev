import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Become a host — earn from your charger",
  description: "List your home EV charger on ChargedEV. Set your price, accept bookings, and earn money every time a driver charges up.",
  alternates: { canonical: "https://chargedev.io/sell" },
  openGraph: {
    title: "Become a host — earn from your charger | ChargedEV",
    description: "List your home EV charger on ChargedEV. Set your price, accept bookings, and earn money every time a driver charges up.",
    url: "https://chargedev.io/sell",
  },
};

export default function SellLayout({ children }: { children: React.ReactNode }) {
  return children;
}

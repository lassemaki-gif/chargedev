import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Latauslasse — EV charging marketplace",
  description: "Rent out your home EV charger and earn money. Or find a nearby charger for your electric car.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}

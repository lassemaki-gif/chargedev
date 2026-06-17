import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ChargedEV — The home charging network", template: "%s | ChargedEV" },
  description: "Find EV chargers at homes near you, or earn money by sharing your own. The home charging network — chargedev.io.",
  metadataBase: new URL("https://chargedev.io"),
  openGraph: {
    siteName: "ChargedEV",
    type: "website",
    url: "https://chargedev.io",
    title: "ChargedEV — The home charging network",
    description: "Find EV chargers at homes near you, or earn money by sharing your own.",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "ChargedEV" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "ChargedEV — The home charging network",
    description: "Find EV chargers at homes near you, or earn money by sharing your own.",
  },
  alternates: { canonical: "https://chargedev.io" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}

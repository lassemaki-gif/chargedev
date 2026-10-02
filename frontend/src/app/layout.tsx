import type { Metadata } from "next";
import { headers } from 'next/headers';
import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
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

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const nonce = (await headers()).get('x-nonce') ?? '';
  return (
    <html lang="en">
      <head>
        {nonce && <meta name="csp-nonce" content={nonce} />}
      </head>
      <body className="min-h-screen">
        {children}
        <Analytics nonce={nonce} />
        <SpeedInsights nonce={nonce} />
      </body>
    </html>
  );
}

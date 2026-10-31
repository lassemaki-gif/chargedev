import type { MetadataRoute } from "next";
import { markets } from "@/lib/markets";

const BASE = "https://chargedev.io";

// hreflang map used on every country entry: tells Google which URL serves
// which locale, and sets English root as x-default.
const COUNTRY_LANG_ALTERNATES: Record<string, string> = {
  ...Object.fromEntries(markets.map((m) => [m.locale, `${BASE}/${m.id}`])),
  "x-default": BASE,
};

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const static_pages: MetadataRoute.Sitemap = [
    { url: BASE, changeFrequency: "weekly", priority: 1.0, alternates: { languages: COUNTRY_LANG_ALTERNATES } },
    { url: `${BASE}/charge`, changeFrequency: "daily", priority: 0.9 },
    { url: `${BASE}/sell`, changeFrequency: "monthly", priority: 0.7 },
    { url: `${BASE}/legal/terms`, changeFrequency: "yearly", priority: 0.3 },
    { url: `${BASE}/legal/privacy`, changeFrequency: "yearly", priority: 0.3 },
  ];

  const country_pages: MetadataRoute.Sitemap = markets.map((m) => ({
    url: `${BASE}/${m.id}`,
    changeFrequency: "weekly" as const,
    priority: 0.9,
    alternates: { languages: COUNTRY_LANG_ALTERNATES },
  }));

  try {
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE ?? "https://chargedev-production.up.railway.app"}/api/listings`, {
      next: { revalidate: 3600 },
    });
    const listings = await res.json();
    const listing_pages: MetadataRoute.Sitemap = listings.map((l: { id: number }) => ({
      url: `${BASE}/charge/${l.id}`,
      changeFrequency: "weekly" as const,
      priority: 0.8,
    }));
    return [...static_pages, ...country_pages, ...listing_pages];
  } catch {
    return [...static_pages, ...country_pages];
  }
}

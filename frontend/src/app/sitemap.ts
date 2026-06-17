import type { MetadataRoute } from "next";

const BASE = "https://chargedev.io";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const static_pages: MetadataRoute.Sitemap = [
    { url: BASE, changeFrequency: "weekly", priority: 1.0 },
    { url: `${BASE}/charge`, changeFrequency: "daily", priority: 0.9 },
    { url: `${BASE}/sell`, changeFrequency: "monthly", priority: 0.7 },
  ];

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
    return [...static_pages, ...listing_pages];
  } catch {
    return static_pages;
  }
}

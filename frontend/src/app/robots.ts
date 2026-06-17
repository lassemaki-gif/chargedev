import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/admin", "/sell/dashboard"] },
    sitemap: "https://chargedev.io/sitemap.xml",
  };
}

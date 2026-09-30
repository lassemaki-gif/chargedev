/** @type {import('next').NextConfig} */

const csp = [
  "default-src 'self'",
  // Next.js requires unsafe-inline for hydration
  "script-src 'self' 'unsafe-inline' https://maps.googleapis.com https://maps.gstatic.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "img-src 'self' data: blob: https://*.googleapis.com https://*.gstatic.com",
  "font-src 'self' https://fonts.gstatic.com",
  // Vercel Analytics + Speed Insights beacon endpoints included
  "connect-src 'self' https://chargedev-production.up.railway.app https://*.googleapis.com https://vitals.vercel-insights.com https://va.vercel-scripts.com",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy",        value: csp },
  { key: "X-Frame-Options",               value: "DENY" },
  { key: "X-Content-Type-Options",        value: "nosniff" },
  { key: "Referrer-Policy",               value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy",            value: "camera=(), microphone=(), geolocation=(self)" },
  { key: "Strict-Transport-Security",     value: "max-age=63072000; includeSubDomains; preload" },
  { key: "Cross-Origin-Opener-Policy",    value: "same-origin-allow-popups" },
  { key: "Cross-Origin-Resource-Policy",  value: "same-origin" },
];

module.exports = {
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

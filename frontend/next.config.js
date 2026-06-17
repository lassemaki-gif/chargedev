/** @type {import('next').NextConfig} */

const csp = [
  "default-src 'self'",
  // Next.js requires unsafe-inline for hydration; unsafe-eval for Turbopack dev
  "script-src 'self' 'unsafe-inline' https://maps.googleapis.com https://maps.gstatic.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "img-src 'self' data: blob: https://*.googleapis.com https://*.gstatic.com https://unpkg.com",
  "font-src 'self' https://fonts.gstatic.com",
  "connect-src 'self' https://chargedev-production.up.railway.app https://*.googleapis.com",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy",        value: csp },
  { key: "X-Frame-Options",               value: "DENY" },
  { key: "X-Content-Type-Options",        value: "nosniff" },
  { key: "Referrer-Policy",               value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy",            value: "camera=(), microphone=(), geolocation=(self)" },
  { key: "Strict-Transport-Security",     value: "max-age=63072000; includeSubDomains; preload" },
];

module.exports = {
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

import { NextRequest, NextResponse } from 'next/server';

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // ── Admin route protection ────────────────────────────────────────────────
  if (pathname.startsWith('/admin')) {
    const token = req.cookies.get('ll_token')?.value;
    if (!token) {
      return NextResponse.redirect(new URL('/', req.url));
    }
    // Allow through — server components can do a deeper role check if needed
  }

  // ── CSP nonce ─────────────────────────────────────────────────────────────
  // Generate a fresh nonce for every request
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');

  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' https://maps.googleapis.com https://maps.gstatic.com`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "img-src 'self' data: blob: https://*.googleapis.com https://*.gstatic.com",
    "font-src 'self' https://fonts.gstatic.com",
    "connect-src 'self' https://chargedev-production.up.railway.app https://*.googleapis.com https://vitals.vercel-insights.com https://va.vercel-scripts.com",
    "frame-ancestors 'none'",
  ].join('; ');

  const requestHeaders = new Headers(req.headers);
  requestHeaders.set('x-nonce', nonce);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', csp);
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=(self)');
  response.headers.set('Strict-Transport-Security', 'max-age=63072000; includeSubDomains; preload');
  response.headers.set('Cross-Origin-Opener-Policy', 'same-origin-allow-popups');
  response.headers.set('Cross-Origin-Resource-Policy', 'same-origin');

  return response;
}

export const config = {
  matcher: [
    // Match all routes except static files and Next.js internals
    '/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml).*)',
  ],
};

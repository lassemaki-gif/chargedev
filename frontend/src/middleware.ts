import { NextRequest, NextResponse } from 'next/server';

function jwtRole(token: string): string | null {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return typeof payload?.role === 'string' ? payload.role : null;
  } catch {
    return null;
  }
}

// Mirror of the market IDs in src/lib/markets.ts. Inlined here to keep the
// middleware bundle small — update this set when a new market is added.
const MARKET_IDS = new Set<string>([
  // EU
  'at','be','bg','hr','cy','cz','dk','ee','fi','fr','de','gr','hu','ie','it',
  'lv','lt','lu','mt','nl','pl','pt','ro','sk','si','es','se',
  // Non-EU Europe
  'gb','ch','no','is',
  // Americas & Oceania
  'us','ca','au','nz',
  // Asia
  'jp','kr','hk','tw','sg','th','my','ph','id',
  // Latin America
  'mx','br','co','pe','cl','ar','pr',
]);

// Rough bot sniff — we skip geo-redirect so crawlers index the canonical
// root and the per-country canonical URLs independently.
const BOT_UA_RE = /bot|crawler|spider|slurp|facebookexternalhit|slackbot|whatsapp|telegram/i;
const GEO_REDIRECT_COOKIE = 'cedev_geo';

export function middleware(req: NextRequest) {
  const { pathname, searchParams } = req.nextUrl;

  // ── Geo-redirect on bare `/` ──────────────────────────────────────────────
  // First-time visitors land on their country page (e.g. Finland → /fi).
  // The cedev_geo cookie makes it a once-per-browser action so "All markets"
  // (and any later visit to /) stays on the hub; `?all=1` also opts out and
  // sets the cookie on the spot.
  if (pathname === '/') {
    const optOut = searchParams.has('all');
    const alreadyMarked = req.cookies.get(GEO_REDIRECT_COOKIE)?.value === '1';
    const ua = req.headers.get('user-agent') || '';
    const isBot = BOT_UA_RE.test(ua);

    if (optOut) {
      // User explicitly chose the hub; remember it and pass through.
      const res = NextResponse.next();
      res.cookies.set(GEO_REDIRECT_COOKIE, '1', {
        path: '/', maxAge: 60 * 60 * 24 * 365, sameSite: 'lax',
      });
      return res;
    }

    if (!alreadyMarked && !isBot) {
      const country = (req.headers.get('x-vercel-ip-country') || '').toLowerCase();
      if (country && MARKET_IDS.has(country)) {
        const url = req.nextUrl.clone();
        url.pathname = `/${country}`;
        url.search = '';
        const res = NextResponse.redirect(url, 307);
        res.cookies.set(GEO_REDIRECT_COOKIE, '1', {
          path: '/', maxAge: 60 * 60 * 24 * 365, sameSite: 'lax',
        });
        return res;
      }
    }
  }

  // ── Admin route protection ────────────────────────────────────────────────
  if (pathname.startsWith('/admin')) {
    const token = req.cookies.get('ll_token')?.value;
    if (!token || jwtRole(token) !== 'admin') {
      return NextResponse.redirect(new URL('/', req.url));
    }
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

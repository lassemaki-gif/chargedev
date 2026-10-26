import { cookies } from 'next/headers';
import type { NextRequest } from 'next/server';

const BACKEND = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

async function handler(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const joinedPath = '/' + path.join('/');
  if (joinedPath.startsWith('/api/webhooks')) {
    return new Response('Forbidden', { status: 403 });
  }
  const url = `${BACKEND}/${path.join('/')}${req.nextUrl.search}`;

  const cookieStore = await cookies();
  const token = cookieStore.get('ll_token')?.value;

  const headers = new Headers();
  const ct = req.headers.get('content-type');
  if (ct) headers.set('content-type', ct);
  if (token) headers.set('authorization', `Bearer ${token}`);

  // Forward the real client IP so backend rate limits aren't bucketed by the
  // Vercel edge egress IP (which would let one attacker lock out everyone).
  const xff = req.headers.get('x-forwarded-for');
  const xri = req.headers.get('x-real-ip');
  if (xff) headers.set('x-forwarded-for', xff);
  if (xri) headers.set('x-real-ip', xri);

  const body = req.method !== 'GET' && req.method !== 'HEAD' ? await req.arrayBuffer() : undefined;

  const res = await fetch(url, { method: req.method, headers, body });

  const responseHeaders = new Headers();
  const resCt = res.headers.get('content-type');
  if (resCt) responseHeaders.set('content-type', resCt);

  return new Response(res.body, { status: res.status, headers: responseHeaders });
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const DELETE = handler;
export const PATCH = handler;

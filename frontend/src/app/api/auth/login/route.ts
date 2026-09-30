const BACKEND = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

export async function POST(req: Request) {
  const body = await req.json();
  const res = await fetch(`${BACKEND}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) return Response.json(data, { status: res.status });

  const response = Response.json({ role: data.role, full_name: data.full_name });
  const isSecure = BACKEND.startsWith('https');
  response.headers.set(
    'Set-Cookie',
    `ll_token=${data.access_token}; HttpOnly; ${isSecure ? 'Secure; ' : ''}SameSite=Strict; Path=/; Max-Age=3600`
  );
  return response;
}

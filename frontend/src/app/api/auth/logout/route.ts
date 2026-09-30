export async function POST() {
  const response = Response.json({ ok: true });
  response.headers.set('Set-Cookie', 'll_token=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0');
  return response;
}

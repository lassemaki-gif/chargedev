"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { BACKEND } from "@/lib/api";

function VerifyContent() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token");
  const [status, setStatus] = useState<"pending" | "success" | "error">("pending");
  const [email, setEmail] = useState("");

  useEffect(() => {
    if (!token) { setStatus("error"); return; }
    fetch(`${BACKEND}/api/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then((r) => r.json())
      .then((d) => {
        if (d.ok) { setEmail(d.email); setStatus("success"); }
        else setStatus("error");
      })
      .catch(() => setStatus("error"));
  }, [token]);

  if (status === "pending") return (
    <>
      <div className="text-4xl mb-4 animate-pulse">⚡</div>
      <p className="text-ash">Verifying your email…</p>
    </>
  );

  if (status === "success") return (
    <>
      <div className="text-5xl mb-4">✅</div>
      <h2 className="text-2xl font-bold text-white mb-2">Email verified</h2>
      <p className="text-ash text-sm mb-6">{email} is now verified. You can browse and book chargers.</p>
      <Link href="/charge" className="btn-volt w-full text-center text-sm">Find a charger</Link>
    </>
  );

  return (
    <>
      <div className="text-4xl mb-4">⚠️</div>
      <h2 className="text-xl font-bold text-white mb-2">Invalid link</h2>
      <p className="text-ash text-sm mb-6">This verification link is invalid or has already been used.</p>
      <Link href="/" className="btn-outline w-full text-center text-sm">Go home</Link>
    </>
  );
}

export default function VerifyEmailPage() {
  return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-56px)] px-6">
        <div className="card max-w-md w-full text-center">
          <Suspense fallback={<p className="text-ash text-sm">Loading…</p>}>
            <VerifyContent />
          </Suspense>
        </div>
      </div>
    </div>
  );
}

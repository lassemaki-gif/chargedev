"use client";
import { useState } from "react";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      await api.forgotPassword(email);
    } catch {
      // Backend returns 200 regardless of whether the email exists; we match that.
    }
    setSent(true);
    setLoading(false);
  }

  return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-56px)] px-6">
        <div className="card max-w-md w-full">
          {sent ? (
            <>
              <div className="text-4xl mb-4">📬</div>
              <h2 className="text-xl font-bold text-white mb-2">Check your inbox</h2>
              <p className="text-ash text-sm mb-6">
                If an account exists for {email}, we&apos;ve sent a password reset link. It expires in one hour.
              </p>
              <Link href="/sell" className="btn-outline w-full text-center text-sm">Back to sign in</Link>
            </>
          ) : (
            <>
              <h2 className="text-xl font-bold text-white mb-2">Reset your password</h2>
              <p className="text-ash text-sm mb-6">Enter the email you signed up with. We&apos;ll send you a link to pick a new password.</p>
              <form onSubmit={onSubmit} className="space-y-4">
                <div>
                  <label className="label">Email</label>
                  <input
                    className="input"
                    type="email"
                    placeholder="you@example.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                  />
                </div>
                <button type="submit" disabled={loading} className="btn-volt w-full text-center">
                  {loading ? "Sending…" : "Send reset link"}
                </button>
              </form>
              <Link href="/sell" className="mt-4 block text-ash text-sm hover:text-white transition-colors text-center">
                Back to sign in
              </Link>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

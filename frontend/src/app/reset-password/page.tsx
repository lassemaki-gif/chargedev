"use client";
import { Suspense, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api } from "@/lib/api";

function ResetContent() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token) {
    return (
      <>
        <div className="text-4xl mb-4">⚠️</div>
        <h2 className="text-xl font-bold text-white mb-2">Invalid link</h2>
        <p className="text-ash text-sm mb-6">This reset link is missing its token. Request a new one.</p>
        <Link href="/forgot-password" className="btn-outline w-full text-center text-sm">Request a new link</Link>
      </>
    );
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("Passwords don't match");
      return;
    }
    setLoading(true);
    try {
      await api.resetPassword(token, password);
      setDone(true);
      setTimeout(() => router.push("/sell"), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset password");
    } finally {
      setLoading(false);
    }
  }

  if (done) return (
    <>
      <div className="text-5xl mb-4">✅</div>
      <h2 className="text-2xl font-bold text-white mb-2">Password updated</h2>
      <p className="text-ash text-sm">Redirecting you to sign in…</p>
    </>
  );

  return (
    <>
      <h2 className="text-xl font-bold text-white mb-2">Pick a new password</h2>
      <p className="text-ash text-sm mb-6">Enter and confirm your new ChargedEV password.</p>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="label">New password</label>
          <input
            className="input"
            type="password"
            placeholder="Min. 8 characters, with a number"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            maxLength={72}
            required
          />
        </div>
        <div>
          <label className="label">Confirm password</label>
          <input
            className="input"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            minLength={8}
            maxLength={72}
            required
          />
        </div>
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button type="submit" disabled={loading} className="btn-volt w-full text-center">
          {loading ? "Saving…" : "Update password"}
        </button>
      </form>
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-56px)] px-6">
        <div className="card max-w-md w-full">
          <Suspense fallback={<p className="text-ash text-sm">Loading…</p>}>
            <ResetContent />
          </Suspense>
        </div>
      </div>
    </div>
  );
}

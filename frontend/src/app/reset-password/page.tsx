"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api } from "@/lib/api";
import { pickUIT2 } from "@/lib/ui";

function ResetContent() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";
  const queryLang = params.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT2(uiLang), [uiLang]);
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token) {
    return (
      <>
        <div className="text-4xl mb-4">⚠️</div>
        <h2 className="text-xl font-bold text-white mb-2">{t.verifyFailedTitle}</h2>
        <p className="text-ash text-sm mb-6">{t.verifyFailedBody}</p>
        <Link href={`/forgot-password${langSuffix}`} className="btn-outline w-full text-center text-sm">{t.forgotTitle}</Link>
      </>
    );
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError(t.resetMismatch);
      return;
    }
    setLoading(true);
    try {
      await api.resetPassword(token, password);
      setDone(true);
      setTimeout(() => router.push(`/sell${langSuffix}`), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.verifyFailedBody);
    } finally {
      setLoading(false);
    }
  }

  if (done) return (
    <>
      <div className="text-5xl mb-4">✅</div>
      <h2 className="text-2xl font-bold text-white mb-2">{t.resetDoneTitle}</h2>
      <p className="text-ash text-sm">{t.resetDoneBody}</p>
    </>
  );

  return (
    <>
      <h2 className="text-xl font-bold text-white mb-2">{t.resetTitle}</h2>
      <p className="text-ash text-sm mb-6">{t.resetBody}</p>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="label">{t.resetNewPassword}</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            maxLength={72}
            required
          />
        </div>
        <div>
          <label className="label">{t.resetConfirmPassword}</label>
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
          {loading ? t.resetSubmitting : t.resetSubmit}
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
          <Suspense fallback={<p className="text-ash text-sm">…</p>}>
            <ResetContent />
          </Suspense>
        </div>
      </div>
    </div>
  );
}

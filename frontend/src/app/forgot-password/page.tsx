"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Nav } from "@/components/Nav";
import { api } from "@/lib/api";
import { pickUIT2 } from "@/lib/ui";

export default function ForgotPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ForgotPasswordInner />
    </Suspense>
  );
}

function ForgotPasswordInner() {
  const search = useSearchParams();
  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT2(uiLang), [uiLang]);
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";
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
              <h2 className="text-xl font-bold text-white mb-2">{t.forgotSentTitle}</h2>
              <p className="text-ash text-sm mb-6">{t.forgotSentBody.replace("{email}", email)}</p>
              <Link href={`/sell${langSuffix}`} className="btn-outline w-full text-center text-sm">{t.forgotBackToSignIn}</Link>
            </>
          ) : (
            <>
              <h2 className="text-xl font-bold text-white mb-2">{t.forgotTitle}</h2>
              <p className="text-ash text-sm mb-6">{t.forgotBody}</p>
              <form onSubmit={onSubmit} className="space-y-4">
                <div>
                  <label className="label">{t.forgotEmail}</label>
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
                  {loading ? t.forgotSending : t.forgotSendLink}
                </button>
              </form>
              <Link href={`/sell${langSuffix}`} className="mt-4 block text-ash text-sm hover:text-white transition-colors text-center">
                {t.forgotBackToSignIn}
              </Link>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

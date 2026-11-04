"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { BACKEND } from "@/lib/api";
import { pickUIT, pickUIT2 } from "@/lib/ui";

function VerifyContent() {
  const params = useSearchParams();
  const token = params.get("token");
  const queryLang = params.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t = useMemo(() => pickUIT2(uiLang), [uiLang]);
  const t1 = useMemo(() => pickUIT(uiLang), [uiLang]);
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";
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
      <p className="text-ash">{t.verifyTitle}</p>
    </>
  );

  if (status === "success") return (
    <>
      <div className="text-5xl mb-4">✅</div>
      <h2 className="text-2xl font-bold text-white mb-2">{t.verifyDoneTitle}</h2>
      <p className="text-ash text-sm mb-6">{email} — {t.verifyDoneBody}</p>
      <Link href={`/charge${langSuffix}`} className="btn-volt w-full text-center text-sm">{t1.navFindCharger}</Link>
    </>
  );

  return (
    <>
      <div className="text-4xl mb-4">⚠️</div>
      <h2 className="text-xl font-bold text-white mb-2">{t.verifyFailedTitle}</h2>
      <p className="text-ash text-sm mb-6">{t.verifyFailedBody}</p>
      <Link href={`/?all=1${langSuffix ? "&" + langSuffix.slice(1) : ""}`} className="btn-outline w-full text-center text-sm">{t.forgotBackToSignIn}</Link>
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

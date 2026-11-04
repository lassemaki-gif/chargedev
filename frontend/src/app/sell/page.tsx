"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Nav } from "@/components/Nav";
import { api, saveRole, getRole, clearToken } from "@/lib/api";
import { useRouter, useSearchParams } from "next/navigation";
import { SELL_LANG, pickSellT } from "@/lib/markets";

export default function SellLanding() {
  return (
    <Suspense fallback={null}>
      <SellLandingInner />
    </Suspense>
  );
}

function SellLandingInner() {
  const router = useRouter();
  const search = useSearchParams();
  const [mode, setMode] = useState<"info" | "login" | "register">("info");

  // Language pick: ?lang=<market lang> from the country-page CTA takes
  // precedence (SSR-stable); otherwise read navigator.language on mount.
  // Starts in English on first paint when there's no hint; swapping once
  // on mount is cheap and only affects visitors with no explicit hint.
  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) {
      setUiLang(navigator.language);
    }
  }, [uiLang]);
  const t = useMemo(() => pickSellT(uiLang), [uiLang]);
  // Reference to appease the unused-import linter when SELL_LANG is only
  // consumed indirectly via pickSellT (keeps IDE jump-to-definition useful).
  void SELL_LANG;

  // If already logged in as seller/admin, verify cookie then go to dashboard
  useEffect(() => {
    const r = getRole();
    if (r === "seller" || r === "admin") {
      api.me().then(() => {
        router.replace("/sell/dashboard");
      }).catch(() => {
        // Token expired or invalid — clear role and show login
        clearToken();
      });
    }
  }, [router]);
  const [form, setForm] = useState({ email: "", password: "", full_name: "", phone: "" });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = mode === "register"
        ? await api.register({ ...form, role: "seller" })
        : await api.login(form.email, form.password);
      saveRole(res.role);
      router.push("/sell/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  if (mode === "info") return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-20 max-w-3xl">
        <p className="text-volt text-sm font-semibold uppercase tracking-widest mb-4">{t.kicker}</p>
        <h1 className="text-5xl font-bold text-white mb-6">{t.headline}</h1>
        <p className="text-ash text-lg leading-relaxed mb-10">{t.body}</p>
        <div className="grid sm:grid-cols-3 gap-6 mb-12">
          {[
            { icon: "⚡", title: t.benefitOutletTitle, body: t.benefitOutletBody },
            { icon: "💰", title: t.benefitShareTitle, body: t.benefitShareBody },
            { icon: "🚗", title: t.benefitOwnTitle, body: t.benefitOwnBody },
          ].map((f) => (
            <div key={f.title} className="card">
              <div className="text-2xl mb-3">{f.icon}</div>
              <div className="font-semibold text-white mb-1">{f.title}</div>
              <div className="text-ash text-sm">{f.body}</div>
            </div>
          ))}
        </div>
        <div className="flex gap-4">
          <button onClick={() => setMode("register")} className="btn-volt">{t.ctaRegister}</button>
          <button onClick={() => setMode("login")} className="btn-outline">{t.ctaLogin}</button>
        </div>
      </div>
    </div>
  );

  return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)] px-6">
        <div className="w-full max-w-md">
          <div className="card">
            <h2 className="text-2xl font-bold text-white mb-1">
              {mode === "register" ? t.registerTitle : t.loginTitle}
            </h2>
            <p className="text-ash text-sm mb-6">
              {mode === "register" ? t.registerSubtitle : t.loginSubtitle}
            </p>
            {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-5">{error}</div>}
            <form onSubmit={submit} className="space-y-4">
              {mode === "register" && (
                <>
                  <div>
                    <label className="label">{t.fullName}</label>
                    <input className="input" placeholder={t.fullNamePlaceholder} value={form.full_name} onChange={(e) => set("full_name", e.target.value)} required />
                  </div>
                  <div>
                    <label className="label">{t.phone}</label>
                    <input className="input" placeholder={t.phoneLabel} value={form.phone} onChange={(e) => set("phone", e.target.value)} />
                  </div>
                </>
              )}
              <div>
                <label className="label">{t.email}</label>
                <input className="input" type="email" placeholder={t.emailPlaceholder} value={form.email} onChange={(e) => set("email", e.target.value)} required />
              </div>
              <div>
                <label className="label">{t.password}</label>
                <input className="input" type="password" placeholder={t.passwordPlaceholder} value={form.password} onChange={(e) => set("password", e.target.value)} required />
              </div>
              <button type="submit" disabled={loading} className="btn-volt w-full text-center">
                {loading ? t.pleaseWait : mode === "register" ? t.createAccount : t.signIn}
              </button>
            </form>
            {mode === "login" && (
              <a href="/forgot-password" className="mt-3 block text-ash text-sm hover:text-white transition-colors text-center">
                {t.forgotPassword}
              </a>
            )}
            <button
              className="mt-4 text-ash text-sm hover:text-white transition-colors w-full text-center"
              onClick={() => setMode(mode === "register" ? "login" : "register")}
            >
              {mode === "register" ? t.switchToLogin : t.switchToRegister}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

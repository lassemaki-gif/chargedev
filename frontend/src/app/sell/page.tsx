"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { api, saveToken, saveRole, clearToken } from "@/lib/api";
import { useRouter } from "next/navigation";

export default function SellLanding() {
  const router = useRouter();
  const [mode, setMode] = useState<"info" | "login" | "register">("info");

  // If already logged in as seller/admin, verify token then go to dashboard
  useEffect(() => {
    const t = localStorage.getItem("ll_token");
    const r = localStorage.getItem("ll_role");
    if (t && (r === "seller" || r === "admin")) {
      api.me().then(() => {
        router.replace("/sell/dashboard");
      }).catch(() => {
        // Token expired or invalid — clear it and show login
        clearToken();
        localStorage.removeItem("ll_role");
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
      saveToken(res.access_token);
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
        <p className="text-volt text-sm font-semibold uppercase tracking-widest mb-4">For hosts</p>
        <h1 className="text-5xl font-bold text-white mb-6">Turn your charger into income</h1>
        <p className="text-ash text-lg leading-relaxed mb-10">
          If you have a three-phase socket or an existing EV charger at home, you can list it on ChargedEV
          and earn money every time a driver books a session. You set your price, your availability — we handle the rest.
        </p>
        <div className="grid sm:grid-cols-3 gap-6 mb-12">
          {[
            { icon: "⚡", t: "Any 3-phase outlet", b: "Standard home three-phase socket is enough to get started." },
            { icon: "💰", t: "You keep 80%", b: "ChargedEV takes a 20% platform fee. The rest is yours." },
            { icon: "🔒", t: "Secure PIN access", b: "Each booking gets a unique PIN. You stay in control." },
          ].map((f) => (
            <div key={f.t} className="card">
              <div className="text-2xl mb-3">{f.icon}</div>
              <div className="font-semibold text-white mb-1">{f.t}</div>
              <div className="text-ash text-sm">{f.b}</div>
            </div>
          ))}
        </div>
        <div className="flex gap-4">
          <button onClick={() => setMode("register")} className="btn-volt">Register as a host</button>
          <button onClick={() => setMode("login")} className="btn-outline">I already have an account</button>
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
              {mode === "register" ? "Create host account" : "Sign in as host"}
            </h2>
            <p className="text-ash text-sm mb-6">
              {mode === "register" ? "Start earning in minutes." : "Welcome back."}
            </p>
            {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-5">{error}</div>}
            <form onSubmit={submit} className="space-y-4">
              {mode === "register" && (
                <>
                  <div>
                    <label className="label">Full name</label>
                    <input className="input" placeholder="Anna Mäkinen" value={form.full_name} onChange={(e) => set("full_name", e.target.value)} required />
                  </div>
                  <div>
                    <label className="label">Phone (optional)</label>
                    <input className="input" placeholder="+358 40 123 4567" value={form.phone} onChange={(e) => set("phone", e.target.value)} />
                  </div>
                </>
              )}
              <div>
                <label className="label">Email</label>
                <input className="input" type="email" placeholder="you@example.com" value={form.email} onChange={(e) => set("email", e.target.value)} required />
              </div>
              <div>
                <label className="label">Password</label>
                <input className="input" type="password" placeholder="Min. 8 characters" value={form.password} onChange={(e) => set("password", e.target.value)} required />
              </div>
              <button type="submit" disabled={loading} className="btn-volt w-full text-center">
                {loading ? "Please wait…" : mode === "register" ? "Create account" : "Sign in"}
              </button>
            </form>
            <button
              className="mt-4 text-ash text-sm hover:text-white transition-colors w-full text-center"
              onClick={() => setMode(mode === "register" ? "login" : "register")}
            >
              {mode === "register" ? "Already have an account? Sign in" : "No account? Register"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

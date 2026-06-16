"use client";
import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { api, Listing, PACKAGES, saveToken, saveRole } from "@/lib/api";
import { useRouter, useParams } from "next/navigation";

export default function ChargerDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [listing, setListing] = useState<Listing | null>(null);
  const [selectedPkg, setSelectedPkg] = useState<number | null>(null);
  const [mode, setMode] = useState<"view" | "login" | "register">("view");
  const [authForm, setAuthForm] = useState({ email: "", password: "", full_name: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");

  useEffect(() => {
    api.listing(parseInt(id)).then(setListing).catch(() => router.push("/charge"));
  }, [id, router]);

  const pkg = selectedPkg ? PACKAGES.find((p) => p.kwh === selectedPkg) : null;
  const price = pkg && listing ? (selectedPkg! * listing.price_per_kwh).toFixed(2) : null;

  async function handleAuth(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = mode === "register"
        ? await api.register({ ...authForm, role: "buyer" })
        : await api.login(authForm.email, authForm.password);
      saveToken(res.access_token);
      saveRole(res.role);
      setMode("view");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleBook() {
    if (!selectedPkg || !listing) return;
    setError(null);
    setLoading(true);
    try {
      const { checkout_url } = await api.checkout({
        listing_id: listing.id,
        package_kwh: selectedPkg,
        notes,
      });
      window.location.href = checkout_url;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.includes("401") || msg.includes("authenticated")) {
        setMode("login");
      } else {
        setError(msg || "Booking failed");
      }
    } finally {
      setLoading(false);
    }
  }

  if (!listing) return <div className="min-h-screen flex items-center justify-center text-ash">Loading…</div>;


  if (mode === "login" || mode === "register") return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)] px-6">
        <div className="card max-w-md w-full">
          <h2 className="text-xl font-bold text-white mb-1">{mode === "login" ? "Sign in to book" : "Create account to book"}</h2>
          <p className="text-ash text-sm mb-5">You need a ChargedEV account to complete your booking.</p>
          {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-4 py-3 text-sm mb-4">{error}</div>}
          <form onSubmit={handleAuth} className="space-y-4">
            {mode === "register" && (
              <div>
                <label className="label">Full name</label>
                <input className="input" placeholder="Your name" value={authForm.full_name} onChange={(e) => setAuthForm((f) => ({ ...f, full_name: e.target.value }))} required />
              </div>
            )}
            <div>
              <label className="label">Email</label>
              <input className="input" type="email" value={authForm.email} onChange={(e) => setAuthForm((f) => ({ ...f, email: e.target.value }))} required />
            </div>
            <div>
              <label className="label">Password</label>
              <input className="input" type="password" value={authForm.password} onChange={(e) => setAuthForm((f) => ({ ...f, password: e.target.value }))} required />
            </div>
            <button type="submit" disabled={loading} className="btn-volt w-full text-center">
              {loading ? "Please wait…" : mode === "login" ? "Sign in & book" : "Create account & book"}
            </button>
          </form>
          <button className="mt-3 text-ash text-sm hover:text-white w-full text-center" onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "No account? Register" : "Already have one? Sign in"}
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div>
      <Nav />
      <div className="px-6 lg:px-16 py-12 max-w-4xl">
        <button onClick={() => router.back()} className="text-ash text-sm hover:text-white mb-6 flex items-center gap-2">← Back</button>

        <div className="grid lg:grid-cols-[1fr_360px] gap-8">
          {/* Left: Info */}
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">{listing.title}</h1>
            <p className="text-ash mb-1">{listing.address}, {listing.city}, {listing.country}</p>
            <p className="text-ash text-sm mb-6">Hosted by {listing.seller_name}</p>

            <div className="grid grid-cols-3 gap-4 mb-8">
              {[
                { label: "Charger type", value: listing.charger_type },
                { label: "Max power", value: `${listing.max_power_kw} kW` },
                { label: "Price", value: `€${listing.price_per_kwh.toFixed(2)}/kWh` },
              ].map((i) => (
                <div key={i.label} className="card text-center">
                  <div className="text-ash text-xs mb-1">{i.label}</div>
                  <div className="font-semibold text-white text-sm">{i.value}</div>
                </div>
              ))}
            </div>

            {listing.description && (
              <div className="mb-6">
                <h2 className="font-semibold text-white mb-2">About this charger</h2>
                <p className="text-ash text-sm leading-relaxed">{listing.description}</p>
              </div>
            )}
            {listing.instructions && (
              <div className="card bg-volt/5 border-volt/20">
                <h2 className="font-semibold text-white mb-2">Access instructions</h2>
                <p className="text-ash text-sm leading-relaxed">{listing.instructions}</p>
              </div>
            )}
          </div>

          {/* Right: Booking */}
          <div className="card self-start sticky top-6">
            <h2 className="font-semibold text-white mb-4">Choose a package</h2>
            <div className="space-y-2 mb-5">
              {PACKAGES.map((pkg) => {
                const p = (pkg.kwh * listing.price_per_kwh).toFixed(2);
                const sel = selectedPkg === pkg.kwh;
                return (
                  <button
                    key={pkg.kwh}
                    onClick={() => setSelectedPkg(pkg.kwh)}
                    className={`w-full flex items-center justify-between px-4 py-3 rounded-lg border transition-colors text-left ${sel ? "border-volt bg-volt/10" : "border-border hover:border-volt/40"}`}
                  >
                    <div>
                      <div className="font-medium text-white">{pkg.kwh} kWh <span className="text-ash text-sm font-normal">· {pkg.label}</span></div>
                      <div className="text-ash text-xs">{pkg.km}</div>
                    </div>
                    <div className={`font-bold text-lg ${sel ? "text-volt" : "text-white"}`}>€{p}</div>
                  </button>
                );
              })}
            </div>

            <div className="mb-4">
              <label className="label">Notes for host (optional)</label>
              <input className="input text-sm" placeholder="e.g. I'll arrive around 14:00" value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>

            {price && (
              <div className="flex justify-between text-sm mb-4 py-3 border-t border-border">
                <span className="text-ash">Total</span>
                <span className="font-bold text-white">€{price}</span>
              </div>
            )}

            {error && <div className="bg-red-900/30 border border-red-800 text-red-400 rounded-lg px-3 py-2 text-sm mb-3">{error}</div>}

            <button
              onClick={handleBook}
              disabled={!selectedPkg || loading || !listing.is_available}
              className="btn-volt w-full text-center disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {!listing.is_available ? "Not available" : loading ? "Processing…" : selectedPkg ? `Book ${selectedPkg} kWh for €${price}` : "Select a package"}
            </button>
            <p className="text-ash text-xs text-center mt-3">PIN code sent instantly after booking</p>
          </div>
        </div>
      </div>
    </div>
  );
}

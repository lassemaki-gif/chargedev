"use client";
import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { StarRating } from "@/components/StarRating";
import { AvailabilityGrid, WeeklyAvailability } from "@/components/AvailabilityGrid";
import { api, Listing, PACKAGES, Review_, saveToken, saveRole } from "@/lib/api";
import { useRouter, useParams } from "next/navigation";

export default function ChargerDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [listing, setListing] = useState<Listing | null>(null);
  const [selectedPkg, setSelectedPkg] = useState<number | null>(null);
  const [mode, setMode] = useState<"view" | "login" | "register">("view");
  const [reviews, setReviews] = useState<Review_[]>([]);
  const [reviewRating, setReviewRating] = useState(0);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [authForm, setAuthForm] = useState({ email: "", password: "", full_name: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");

  useEffect(() => {
    api.listing(parseInt(id)).then(setListing).catch(() => router.push("/charge"));
    api.listingReviews(parseInt(id)).then(setReviews).catch(() => {});
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

            {/* Satellite + Street View */}
            {listing.lat && listing.lng && process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY && (
              <div className="mb-6">
                <h2 className="font-semibold text-white mb-3">Location</h2>
                <div className="grid grid-cols-2 gap-3">
                  <div className="overflow-hidden rounded-xl border border-border">
                    <p className="text-ash text-xs uppercase tracking-widest px-3 py-2 border-b border-border">Aerial view</p>
                    <img
                      src={`https://maps.googleapis.com/maps/api/staticmap?center=${listing.lat},${listing.lng}&zoom=19&size=600x300&maptype=satellite&markers=color:0x22C55E%7C${listing.lat},${listing.lng}&key=${process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY}`}
                      alt="Aerial view"
                      className="w-full h-40 object-cover"
                      onError={(e) => { (e.target as HTMLImageElement).parentElement!.style.display = "none"; }}
                    />
                  </div>
                  <div className="overflow-hidden rounded-xl border border-border">
                    <p className="text-ash text-xs uppercase tracking-widest px-3 py-2 border-b border-border">Street view</p>
                    <img
                      src={`https://maps.googleapis.com/maps/api/streetview?size=600x300&location=${listing.lat},${listing.lng}&fov=90&pitch=0&key=${process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY}`}
                      alt="Street view"
                      className="w-full h-40 object-cover"
                      onError={(e) => { (e.target as HTMLImageElement).parentElement!.style.display = "none"; }}
                    />
                  </div>
                </div>
              </div>
            )}

            {listing.description && (
              <div className="mb-6">
                <h2 className="font-semibold text-white mb-2">About this charger</h2>
                <p className="text-ash text-sm leading-relaxed">{listing.description}</p>
              </div>
            )}
            {listing.instructions && (
              <div className="card bg-volt/5 border-volt/20 mb-6">
                <h2 className="font-semibold text-white mb-2">Access instructions</h2>
                <p className="text-ash text-sm leading-relaxed">{listing.instructions}</p>
              </div>
            )}

            {/* Availability */}
            {listing.availability_json && (() => {
              try {
                const av: WeeklyAvailability = JSON.parse(listing.availability_json);
                return (
                  <div className="mb-6">
                    <h2 className="font-semibold text-white mb-3">Availability</h2>
                    <AvailabilityGrid value={av} onChange={() => {}} readonly />
                  </div>
                );
              } catch { return null; }
            })()}

            {/* Reviews */}
            <div className="mb-6">
              <div className="flex items-center gap-3 mb-4">
                <h2 className="font-semibold text-white">Reviews</h2>
                {listing.avg_rating && (
                  <span className="flex items-center gap-1">
                    <StarRating value={Math.round(listing.avg_rating)} readonly size="sm" />
                    <span className="text-ash text-sm">{listing.avg_rating} ({listing.review_count})</span>
                  </span>
                )}
              </div>
              {reviews.length === 0 && <p className="text-ash text-sm">No reviews yet.</p>}
              <div className="space-y-3">
                {reviews.map((r) => (
                  <div key={r.id} className="card">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-medium text-white text-sm">{r.reviewer_name}</span>
                      <StarRating value={r.rating} readonly size="sm" />
                    </div>
                    {r.comment && <p className="text-ash text-sm">{r.comment}</p>}
                  </div>
                ))}
              </div>

              {/* Leave a review (only for completed bookings — checked server-side) */}
              <div className="mt-4 card">
                <h3 className="font-medium text-white text-sm mb-3">Leave a review</h3>
                <StarRating value={reviewRating} onChange={setReviewRating} size="lg" />
                <textarea
                  className="input mt-3 min-h-[60px] resize-none text-sm"
                  placeholder="Share your experience…"
                  value={reviewComment}
                  onChange={(e) => setReviewComment(e.target.value)}
                />
                <button
                  disabled={reviewRating === 0 || reviewSubmitting}
                  onClick={async () => {
                    // Find completed booking for this listing
                    const myBookings = await api.myBookings().catch(() => []);
                    const b = myBookings.find((bk) => bk.listing_id === listing!.id && bk.status === "completed");
                    if (!b) { alert("You can only review listings you've used."); return; }
                    setReviewSubmitting(true);
                    try {
                      const rv = await api.createReview({ booking_id: b.id, rating: reviewRating, comment: reviewComment });
                      setReviews((prev) => [rv, ...prev]);
                      setReviewRating(0);
                      setReviewComment("");
                    } catch (e: unknown) {
                      alert(e instanceof Error ? e.message : "Could not submit review");
                    } finally { setReviewSubmitting(false); }
                  }}
                  className="btn-volt mt-3 text-sm disabled:opacity-40"
                >
                  {reviewSubmitting ? "Submitting…" : "Submit review"}
                </button>
              </div>
            </div>
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

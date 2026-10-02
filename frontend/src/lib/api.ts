const PROXY = '/api/proxy';
export const BACKEND = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

export function getRole(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("ll_role");
}
export function saveRole(r: string) { localStorage.setItem("ll_role", r); }
export function clearRole() { localStorage.removeItem("ll_role"); }

// Kept for backward compat but no longer stores sensitive token
export function saveToken(_t: string) { /* token is now in httpOnly cookie */ }
export function clearToken() {
  clearRole();
  fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const res = await fetch(`${PROXY}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'include',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
  }
  return res.json();
}

async function authReq<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'include',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
  }
  return res.json();
}

export const api = {
  // Auth (go directly to Next.js auth routes, not through proxy)
  register: (body: { email: string; password: string; full_name: string; phone?: string; role: string }) =>
    authReq<{ role: string; full_name: string }>("POST", "/api/auth/register", body),
  login: (email: string, password: string) =>
    authReq<{ role: string; full_name: string }>("POST", "/api/auth/login", { email, password }),
  logout: () => fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }),
  me: () => req<{ id: number; email: string; full_name: string; role: string }>("GET", "/api/auth/me"),

  // Public listings
  listings: (city?: string) => req<Listing[]>("GET", `/api/listings${city ? `?city=${encodeURIComponent(city)}` : ""}`),
  listing: (id: number) => req<Listing>("GET", `/api/listings/${id}`),

  // Seller
  updateProfile: (body: { full_name?: string; phone?: string }) =>
    req<User>("PUT", "/api/seller/profile", body),
  sellerEarnings: () => req<SellerEarnings>("GET", "/api/seller/earnings"),
  stripeOnboard: () => req<{ url: string }>("POST", "/api/seller/stripe/onboard"),
  stripeStatus: () => req<{ onboarded: boolean; charges_enabled: boolean; payouts_enabled: boolean; account_id: string | null }>("GET", "/api/seller/stripe/status"),
  createListing: (body: Partial<Listing>) => req<Listing>("POST", "/api/seller/listings", body),
  myListings: () => req<Listing[]>("GET", "/api/seller/listings"),
  toggleListing: (id: number) => req<{ is_available: boolean }>("PUT", `/api/seller/listings/${id}/toggle`),
  sellerBookings: () => req<Booking[]>("GET", "/api/seller/bookings"),
  completeBooking: (id: number) => req<{ status: string }>("PUT", `/api/seller/bookings/${id}/complete`),
  adminRetryTransfer: (bookingId: number) => req<{ booking_id: number; transfer_id: string; amount_eur: number; paid_out: boolean }>("POST", `/api/admin/bookings/${bookingId}/retry-transfer`),

  // Reviews
  createReview: (body: { booking_id: number; rating: number; comment?: string }) =>
    req<Review_>("POST", "/api/reviews", body),
  listingReviews: (listingId: number) => req<Review_[]>("GET", `/api/listings/${listingId}/reviews`),
  sellerReviews: () => req<Review_[]>("GET", "/api/seller/reviews"),

  // Availability
  setAvailability: (listingId: number, body: object) =>
    req<{ ok: boolean }>("PUT", `/api/seller/listings/${listingId}/availability`, body),

  // Notifications polling
  newBookingsSince: (since: number) =>
    req<{ count: number; bookings: { id: number; listing_id: number }[] }>("GET", `/api/seller/new-bookings?since=${since}`),

  // Buyer
  checkout: (body: { listing_id: number; package_kwh: number; notes?: string }) =>
    req<{ checkout_url: string; booking_id: number }>("POST", "/api/checkout", body),
  myBookings: () => req<Booking[]>("GET", "/api/buyer/bookings"),
  bookingBySession: (sessionId: string) =>
    req<Booking>("GET", `/api/bookings/by-session/${sessionId}`),
  verifyCheckout: (sessionId: string) =>
    req<Booking>("GET", `/api/checkout/verify/${sessionId}`),

  // Shelly (Premium hosts)
  shellyConnect: (listingId: number, body: { device_id: string; auth_key: string; server: string }) =>
    req<ShellyStatus>("POST", `/api/seller/shelly/${listingId}`, body),
  shellyStatus: (listingId: number) => req<ShellyStatus>("GET", `/api/seller/shelly/${listingId}`),
  shellyDisconnect: (listingId: number) => req<{ ok: boolean }>("DELETE", `/api/seller/shelly/${listingId}`),

  // OCPP (Premium hosts — alternative to Shelly)
  ocppRegister: (listingId: number, chargePointId: string) =>
    req<OcppChargePointStatus>("POST", `/api/seller/ocpp/${listingId}`, { charge_point_id: chargePointId }),
  ocppStatus: (listingId: number) => req<OcppChargePointStatus>("GET", `/api/seller/ocpp/${listingId}`),
  ocppUnregister: (listingId: number) => req<{ ok: boolean }>("DELETE", `/api/seller/ocpp/${listingId}`),

  // Admin
  adminStats: () => req<PlatformStats>("GET", "/api/admin/stats"),
  adminUsers: () => req<User[]>("GET", "/api/admin/users"),
  adminListings: () => req<Listing[]>("GET", "/api/admin/listings"),
  adminBookings: () => req<Booking[]>("GET", "/api/admin/bookings"),
  toggleUser: (id: number) => req<{ is_active: boolean }>("PUT", `/api/admin/users/${id}/toggle`),
};

export interface Listing {
  id: number;
  seller_id: number;
  seller_name: string;
  title: string;
  description?: string;
  address: string;
  city: string;
  country: string;
  lat?: number;
  lng?: number;
  charger_type: string;
  max_power_kw: number;
  price_per_kwh: number;
  is_available: boolean;
  instructions?: string;
  availability_json?: string;
  avg_rating?: number;
  review_count?: number;
  shelly_enabled?: boolean;
  ocpp_enabled?: boolean;
  created_at: string;
}

export interface OcppChargePointStatus {
  charge_point_id: string;
  vendor?: string;
  model?: string;
  status: string;  // available | charging | offline | faulted
  last_heartbeat?: string;
  ws_url: string;
}

export interface ShellyStatus {
  connected: boolean;
  device_id?: string;
  relay_on?: boolean;
  power_w?: number;
  energy_total_wh?: number;
  error?: string;
}

export interface Booking {
  id: number;
  listing_id: number;
  listing_title: string;
  listing_address: string;
  buyer_id: number;
  buyer_name: string;
  package_kwh: number;
  price_per_kwh: number;
  total_eur: number;
  seller_earnings_eur: number;
  platform_fee_eur: number;
  status: string;
  pin_code?: string;
  paid_out: boolean;
  paid_out_at?: string;
  scheduled_at?: string;
  completed_at?: string;
  notes?: string;
  created_at: string;
}

export interface User {
  id: number;
  email: string;
  full_name: string;
  phone?: string;
  stripe_account_id?: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface Review_ {
  id: number;
  booking_id: number;
  listing_id: number;
  reviewer_id: number;
  reviewer_name: string;
  rating: number;
  comment?: string;
  created_at: string;
}

export interface SellerEarnings {
  pending_eur: number;
  paid_out_eur: number;
  total_eur: number;
  stripe_onboarded: boolean;
  stripe_account_id: string | null;
}

export interface PlatformStats {
  total_users: number;
  total_sellers: number;
  total_buyers: number;
  total_listings: number;
  active_listings: number;
  total_bookings: number;
  completed_bookings: number;
  total_kwh_delivered: number;
  total_revenue_eur: number;
  platform_earnings_eur: number;
}

export const PACKAGES = [
  { kwh: 20, label: "City hop", km: "~80 km", price: (p: number) => (20 * p).toFixed(2) },
  { kwh: 40, label: "Day trip", km: "~160 km", price: (p: number) => (40 * p).toFixed(2) },
  { kwh: 60, label: "Long run", km: "~250 km", price: (p: number) => (60 * p).toFixed(2) },
  { kwh: 80, label: "Full tank", km: "~320 km", price: (p: number) => (80 * p).toFixed(2) },
];

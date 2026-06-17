const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

function token(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("ll_token");
}

export function saveToken(t: string) { localStorage.setItem("ll_token", t); }
export function clearToken() { localStorage.removeItem("ll_token"); }
export function getRole(): string | null { return localStorage.getItem("ll_role"); }
export function saveRole(r: string) { localStorage.setItem("ll_role", r); }

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const t = token();
  if (t) headers["Authorization"] = `Bearer ${t}`;
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
  }
  return res.json();
}

export const api = {
  // Auth
  register: (body: { email: string; password: string; full_name: string; phone?: string; role: string }) =>
    req<{ access_token: string; role: string; full_name: string }>("POST", "/api/auth/register", body),
  login: (email: string, password: string) =>
    req<{ access_token: string; role: string; full_name: string }>("POST", "/api/auth/login", { email, password }),
  me: () => req<{ id: number; email: string; full_name: string; role: string }>("GET", "/api/auth/me"),

  // Public listings
  listings: (city?: string) => req<Listing[]>("GET", `/api/listings${city ? `?city=${city}` : ""}`),
  listing: (id: number) => req<Listing>("GET", `/api/listings/${id}`),

  // Seller
  updateProfile: (body: { full_name?: string; phone?: string; iban?: string }) =>
    req<User>("PUT", "/api/seller/profile", body),
  sellerEarnings: () => req<SellerEarnings>("GET", "/api/seller/earnings"),
  createListing: (body: Partial<Listing>) => req<Listing>("POST", "/api/seller/listings", body),
  myListings: () => req<Listing[]>("GET", "/api/seller/listings"),
  toggleListing: (id: number) => req<{ is_available: boolean }>("PUT", `/api/seller/listings/${id}/toggle`),
  sellerBookings: () => req<Booking[]>("GET", "/api/seller/bookings"),
  completeBooking: (id: number) => req<{ status: string }>("PUT", `/api/seller/bookings/${id}/complete`),
  adminPayout: (sellerId: number) => req<{ seller: string; iban: string; bookings_paid: number; amount_eur: number }>("PUT", `/api/admin/sellers/${sellerId}/payout`),

  // Buyer
  checkout: (body: { listing_id: number; package_kwh: number; notes?: string }) =>
    req<{ checkout_url: string; booking_id: number }>("POST", "/api/checkout", body),
  myBookings: () => req<Booking[]>("GET", "/api/buyer/bookings"),
  bookingBySession: (sessionId: string) =>
    req<Booking>("GET", `/api/bookings/by-session/${sessionId}`),
  verifyCheckout: (sessionId: string) =>
    req<Booking>("GET", `/api/checkout/verify/${sessionId}`),

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
  charger_type: string;
  max_power_kw: number;
  price_per_kwh: number;
  is_available: boolean;
  instructions?: string;
  created_at: string;
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
  iban?: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface SellerEarnings {
  pending_eur: number;
  paid_out_eur: number;
  total_eur: number;
  next_payout_date: string;
  iban: string | null;
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

"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { getRole } from "@/lib/api";

export function Nav() {
  const path = usePathname();
  const isSell = path.startsWith("/sell");
  const isCharge = path.startsWith("/charge") && !path.startsWith("/charge/success");
  const isAdmin = path.startsWith("/admin");
  const isBookings = path.startsWith("/bookings");
  const role = typeof window !== "undefined" ? getRole() : null;

  return (
    <nav className="border-b border-border px-4 sm:px-6 lg:px-10 h-14 sm:h-16 flex items-center justify-between gap-3">
      <Link href="/" className="flex items-center gap-2">
        <span className="text-volt font-mono font-medium text-lg">⚡</span>
        <span className="font-semibold text-white tracking-tight">ChargedEV</span>
      </Link>
      <div className="flex items-center gap-1">
        <Link
          href="/charge"
          className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isCharge ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
        >
          <span className="hidden sm:inline">Find a charger</span>
          <span className="sm:hidden">Chargers</span>
        </Link>
        {role === "buyer" && (
          <Link
            href="/bookings"
            className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isBookings ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
          >
            <span className="hidden sm:inline">My bookings</span>
            <span className="sm:hidden">Bookings</span>
          </Link>
        )}
        {(role === "seller" || role === "admin") && (
          <Link
            href="/sell/dashboard"
            className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isSell ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
          >
            <span className="hidden sm:inline">Dashboard</span>
            <span className="sm:hidden">Dash</span>
          </Link>
        )}
        {!role && (
          <Link
            href="/sell"
            className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isSell ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
          >
            <span className="hidden sm:inline">Become a host</span>
            <span className="sm:hidden">Host</span>
          </Link>
        )}
        {isAdmin && (
          <Link href="/admin" className="px-4 py-2 rounded-lg text-sm font-medium bg-red-900/30 text-red-400">
            Admin
          </Link>
        )}
      </div>
    </nav>
  );
}

"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

export function Nav() {
  const path = usePathname();
  const isSell = path.startsWith("/sell");
  const isCharge = path.startsWith("/charge");
  const isAdmin = path.startsWith("/admin");

  return (
    <nav className="border-b border-border px-6 lg:px-10 h-16 flex items-center justify-between">
      <Link href="/" className="flex items-center gap-2">
        <span className="text-volt font-mono font-medium text-lg">⚡</span>
        <span className="font-semibold text-white tracking-tight">Latauslasse</span>
      </Link>
      <div className="flex items-center gap-1">
        <Link
          href="/charge"
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${isCharge ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
        >
          Find a charger
        </Link>
        <Link
          href="/sell"
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${isSell ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
        >
          Become a host
        </Link>
        {isAdmin && (
          <Link href="/admin" className="px-4 py-2 rounded-lg text-sm font-medium bg-red-900/30 text-red-400">
            Admin
          </Link>
        )}
      </div>
    </nav>
  );
}

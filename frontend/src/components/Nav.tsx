"use client";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { getRole } from "@/lib/api";
import { pickUIT } from "@/lib/ui";

export function Nav() {
  const path = usePathname();
  const search = useSearchParams();
  const isSell = path.startsWith("/sell");
  const isCharge = path.startsWith("/charge") && !path.startsWith("/charge/success");
  const isAdmin = path.startsWith("/admin");
  const isBookings = path.startsWith("/bookings");
  const role = typeof window !== "undefined" ? getRole() : null;

  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) {
      setUiLang(navigator.language);
    }
  }, [uiLang]);
  const t = useMemo(() => pickUIT(uiLang), [uiLang]);

  // Preserve the ?lang= hint when navigating via Nav so the destination
  // page renders in the same language on first paint.
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";

  return (
    <nav className="border-b border-border px-4 sm:px-6 lg:px-10 h-14 sm:h-16 flex items-center justify-between gap-3">
      <Link href={`/${langSuffix ? langSuffix + "&all=1" : "?all=1"}`} className="flex items-center gap-2">
        <span className="text-volt font-mono font-medium text-lg">⚡</span>
        <span className="font-semibold text-white tracking-tight">ChargedEV</span>
      </Link>
      <div className="flex items-center gap-1">
        <Link
          href={`/charge${langSuffix}`}
          className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isCharge ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
        >
          <span className="hidden sm:inline">{t.navFindCharger}</span>
          <span className="sm:hidden">{t.navFindChargerShort}</span>
        </Link>
        {role === "buyer" && (
          <Link
            href={`/bookings${langSuffix}`}
            className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isBookings ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
          >
            <span className="hidden sm:inline">{t.navMyBookings}</span>
            <span className="sm:hidden">{t.navMyBookingsShort}</span>
          </Link>
        )}
        {(role === "seller" || role === "admin") && (
          <Link
            href={`/sell/dashboard${langSuffix}`}
            className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isSell ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
          >
            <span className="hidden sm:inline">{t.navDashboard}</span>
            <span className="sm:hidden">{t.navDashboardShort}</span>
          </Link>
        )}
        {!role && (
          <Link
            href={`/sell${langSuffix}`}
            className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${isSell ? "bg-white/10 text-white" : "text-ash hover:text-white"}`}
          >
            <span className="hidden sm:inline">{t.navBecomeHost}</span>
            <span className="sm:hidden">{t.navBecomeHostShort}</span>
          </Link>
        )}
        {isAdmin && (
          <Link href="/admin" className="px-4 py-2 rounded-lg text-sm font-medium bg-red-900/30 text-red-400">
            Admin
          </Link>
        )}
        <a
          href="mailto:support@chargedev.io"
          className="px-3 py-2 rounded-lg text-sm font-medium text-ash hover:text-white transition-colors"
        >
          {t.navSupport}
        </a>
      </div>
    </nav>
  );
}

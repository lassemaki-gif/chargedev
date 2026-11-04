"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Nav } from "@/components/Nav";
import { pickUIT2 } from "@/lib/ui";

export default function CancelPage() {
  return (
    <Suspense fallback={null}>
      <CancelPageInner />
    </Suspense>
  );
}

function CancelPageInner() {
  const router = useRouter();
  const search = useSearchParams();
  const queryLang = search?.get("lang");
  const [uiLang, setUiLang] = useState<string | null>(queryLang);
  useEffect(() => {
    if (uiLang) return;
    if (typeof navigator !== "undefined" && navigator.language) setUiLang(navigator.language);
  }, [uiLang]);
  const t2 = useMemo(() => pickUIT2(uiLang), [uiLang]);
  const langSuffix = queryLang ? `?lang=${queryLang}` : "";
  return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)] px-6">
        <div className="card max-w-sm w-full text-center">
          <div className="text-4xl mb-4">↩️</div>
          <h2 className="text-xl font-bold text-white mb-2">{t2.cancelTitle}</h2>
          <p className="text-ash text-sm mb-6">{t2.cancelBody}</p>
          <button onClick={() => router.back()} className="btn-volt w-full text-center mb-3">{t2.cancelGoBack}</button>
          <button onClick={() => router.push(`/charge${langSuffix}`)} className="btn-outline w-full text-center text-sm">{t2.cancelBrowseChargers}</button>
        </div>
      </div>
    </div>
  );
}

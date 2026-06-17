"use client";
import { useEffect, useRef } from "react";
import { importLibrary, setOptions } from "@googlemaps/js-api-loader";
import type { Listing } from "@/lib/api";

export default function ChargerMap({ listings }: { listings: Listing[] }) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<google.maps.Map | null>(null);

  useEffect(() => {
    const mapped = listings.filter((l) => l.lat && l.lng);
    if (!mapRef.current || mapped.length === 0) return;

    setOptions({
      key: process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? "",
      v: "weekly",
    });

    (async () => {
      const { Map, InfoWindow } = await importLibrary("maps") as google.maps.MapsLibrary;
      const { AdvancedMarkerElement } = await importLibrary("marker") as google.maps.MarkerLibrary;

      if (!mapInstance.current) {
        mapInstance.current = new Map(mapRef.current!, {
          mapId: "chargedev",
          center: { lat: mapped[0].lat!, lng: mapped[0].lng! },
          zoom: 12,
          zoomControl: true,
          streetViewControl: false,
          mapTypeControl: false,
          fullscreenControl: false,
        });
      }

      const map = mapInstance.current!;
      const infoWindow = new InfoWindow();

      if (mapped.length > 1) {
        const bounds = new google.maps.LatLngBounds();
        mapped.forEach((l) => bounds.extend({ lat: l.lat!, lng: l.lng! }));
        map.fitBounds(bounds, 60);
      } else {
        map.setCenter({ lat: mapped[0].lat!, lng: mapped[0].lng! });
      }

      mapped.forEach((l) => {
        const pin = document.createElement("div");
        pin.textContent = "⚡";
        pin.style.cssText = "background:#22C55E;color:#0A0F1E;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-size:16px;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.4)";

        const marker = new AdvancedMarkerElement({
          position: { lat: l.lat!, lng: l.lng! },
          map,
          title: l.title,
          content: pin,
        });

        marker.addEventListener("gmp-click", () => {
          // Build DOM nodes — never use innerHTML with user-supplied strings
          const wrap = document.createElement("div");
          wrap.style.cssText = "min-width:190px;padding:4px 2px";

          const title = document.createElement("div");
          title.textContent = l.title;
          title.style.cssText = "font-weight:700;font-size:14px;margin-bottom:4px";

          const detail = document.createElement("div");
          detail.textContent = `${l.charger_type} · ${l.max_power_kw} kW · €${l.price_per_kwh.toFixed(2)}/kWh`;
          detail.style.cssText = "color:#555;font-size:12px;margin-bottom:8px";

          const link = document.createElement("a");
          link.href = `/charge/${l.id}`;
          link.textContent = "Book →";
          link.style.cssText = "display:inline-block;background:#22C55E;color:#0A0F1E;padding:6px 14px;border-radius:6px;font-size:13px;font-weight:700;text-decoration:none";

          wrap.append(title, detail, link);
          infoWindow.setContent(wrap);
          infoWindow.open({ anchor: marker, map });
        });
      });
    })();
  }, [listings]);

  const hasCoords = listings.some((l) => l.lat && l.lng);
  if (!hasCoords) {
    return (
      <div className="h-full flex items-center justify-center text-ash text-sm">
        No chargers with location data yet.
      </div>
    );
  }

  return <div ref={mapRef} style={{ width: "100%", height: "100%" }} />;
}

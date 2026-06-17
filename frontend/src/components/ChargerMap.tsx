"use client";
import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import L from "leaflet";
import Link from "next/link";
import type { Listing } from "@/lib/api";

// Fix default marker icons broken by webpack
delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

const voltIcon = new L.Icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

function FitBounds({ listings }: { listings: Listing[] }) {
  const map = useMap();
  useEffect(() => {
    const points = listings.filter((l) => l.lat && l.lng).map((l) => [l.lat!, l.lng!] as [number, number]);
    if (points.length === 1) {
      map.setView(points[0], 13);
    } else if (points.length > 1) {
      map.fitBounds(points, { padding: [40, 40] });
    }
  }, [listings, map]);
  return null;
}

interface Props {
  listings: Listing[];
}

export default function ChargerMap({ listings }: Props) {
  const mapped = listings.filter((l) => l.lat && l.lng);

  if (mapped.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-ash text-sm">
        No chargers with location data yet.
      </div>
    );
  }

  const center: [number, number] = [mapped[0].lat!, mapped[0].lng!];

  return (
    <MapContainer
      center={center}
      zoom={12}
      style={{ height: "100%", width: "100%" }}
      className="z-0"
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds listings={mapped} />
      {mapped.map((l) => (
        <Marker key={l.id} position={[l.lat!, l.lng!]} icon={voltIcon}>
          <Popup>
            <div style={{ minWidth: 180 }}>
              <strong style={{ fontSize: 14 }}>{l.title}</strong>
              <p style={{ margin: "4px 0", color: "#555", fontSize: 12 }}>
                {l.charger_type} · {l.max_power_kw} kW · €{l.price_per_kwh}/kWh
              </p>
              <Link
                href={`/charge/${l.id}`}
                style={{ color: "#22C55E", fontSize: 13, fontWeight: 600 }}
              >
                Book →
              </Link>
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}

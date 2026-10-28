import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export interface MapPin {
  latlng: [number, number];
  card_index: number;
}

interface TransplantMapProps {
  center: [number, number];
  pins: MapPin[];
  names: string[];
  onPinClick: (index: number) => void;
}

/** Leaflet transplant map: warm terracotta divIcon pins over OSM tiles.
 *  One L.marker per pin (each renders a .leaflet-marker-icon node);
 *  marker clicks route to onPinClick(card_index) for the card highlight. */
export default function TransplantMap({
  center,
  pins,
  names,
  onPinClick,
}: TransplantMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const clickRef = useRef(onPinClick);
  clickRef.current = onPinClick;

  // Create the map once; StrictMode-safe via cleanup.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || mapRef.current) return;
    const map = L.map(el, { scrollWheelZoom: false }).setView(center, 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // Initialized once with the first payload's center; later centers apply below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Rebuild pins whenever the transplant payload changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.setView(center, 12);
    const markers = pins.map((pin, i) => {
      const index = pin.card_index ?? i;
      const icon = L.divIcon({
        className: "warm-pin",
        html: `<span aria-hidden="true">${index + 1}</span>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15],
      });
      const marker = L.marker([pin.latlng[0], pin.latlng[1]], {
        icon,
        title: names[index] ?? `Transplant stop ${index + 1}`,
      }).addTo(map);
      marker.on("click", () => clickRef.current(index));
      return marker;
    });
    return () => {
      markers.forEach((marker) => marker.remove());
    };
  }, [center, pins, names]);

  return (
    <div
      id="map"
      ref={containerRef}
      role="application"
      aria-label="Transplant map"
    />
  );
}

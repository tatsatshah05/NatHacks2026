"use client";

import { useEffect, useRef } from "react";
import type { Map, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { PriorityItem } from "@/lib/city";
import { readingShort } from "@/lib/city";
import maplibregl from "@/lib/maplibre";
import { effectLabel } from "@/lib/report-format";
import { effectShape, hazardShapeSvg } from "../ui/hazard-shapes";
import { cameraGlyphSvg } from "./camera-glyph";
import type { CameraGroup } from "./cameras";
import styles from "./city-dashboard.module.css";

const EDMONTON: [number, number] = [-113.4938, 53.5461];

type Props = {
  items: readonly PriorityItem[];
  cameras: readonly CameraGroup[];
  selectedId: string | null;
  onSelect: (id: string) => void;
};

// Markers are pointer shortcuts only (docs/design/DESIGN.md, City map markers). The
// list and the "Road cameras" section are the keyboard and screen-reader path and
// select the same things, so markers are hidden from assistive technology and stay
// out of the tab order.
export default function CityMap({ items, cameras, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const markersRef = useRef<globalThis.Map<string, HTMLElement>>(new globalThis.Map());
  const fittedRef = useRef(false);
  const onSelectRef = useRef(onSelect);
  const jumpedRef = useRef<string | null>(null);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: EDMONTON,
      zoom: 10.5,
      // Respect reduced motion in the library too, not only in CSS.
      fadeDuration: reduce ? 0 : 300
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Rebuild the markers when the data changes; selection only toggles a class.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const markers: Marker[] = [];
    const elements = new globalThis.Map<string, HTMLElement>();
    items.forEach((item, index) => {
      const element = document.createElement("div");
      element.className = "map-marker";
      element.setAttribute("aria-hidden", "true");
      element.title = `${item.rank}. ${effectLabel(item.effect)}: ${item.title}`;
      // A constant SVG from hazard-shapes.ts; the item's text never goes into HTML.
      element.innerHTML = hazardShapeSvg(effectShape(item.effect), 24, `city-${index}`);
      element.addEventListener("click", () => onSelectRef.current(item.id));
      elements.set(item.id, element);
      markers.push(new maplibregl.Marker({ element, anchor: "center" }).setLngLat([item.lng, item.lat]).addTo(map));
    });
    cameras.forEach((camera) => {
      const element = document.createElement("div");
      element.className = styles.cameraMarker;
      element.setAttribute("aria-hidden", "true");
      element.title = `Road camera: ${readingShort(camera.headline.label)}`;
      element.innerHTML = cameraGlyphSvg(20);
      const word = document.createElement("span");
      word.textContent = readingShort(camera.headline.label); // text, never HTML
      element.appendChild(word);
      element.addEventListener("click", () => onSelectRef.current(camera.id));
      elements.set(camera.id, element);
      markers.push(new maplibregl.Marker({ element, anchor: "center" }).setLngLat([camera.lng, camera.lat]).addTo(map));
    });
    markersRef.current = elements;
    if (!fittedRef.current && (items.length || cameras.length)) {
      // Once: frame everything we know about, without animation.
      const bounds = new maplibregl.LngLatBounds();
      for (const item of items) bounds.extend([item.lng, item.lat]);
      for (const camera of cameras) bounds.extend([camera.lng, camera.lat]);
      map.fitBounds(bounds, { padding: 48, maxZoom: 14, duration: 0 });
      fittedRef.current = true;
    }
    return () => {
      markers.forEach((marker) => marker.remove());
      markersRef.current = new globalThis.Map();
    };
  }, [items, cameras]);

  // Selection: mark the marker and bring it into view, with no motion.
  useEffect(() => {
    markersRef.current.forEach((element, id) => element.classList.toggle("is-selected", id === selectedId));
    const map = mapRef.current;
    if (!map || !selectedId || jumpedRef.current === selectedId) {
      if (!selectedId) jumpedRef.current = null;
      return;
    }
    const item = items.find((entry) => entry.id === selectedId);
    const camera = item ? undefined : cameras.find((entry) => entry.id === selectedId);
    const target = item ? [item.lng, item.lat] : camera ? [camera.lng, camera.lat] : null;
    if (!target) return;
    map.jumpTo({ center: target as [number, number], zoom: Math.max(map.getZoom(), 14) });
    jumpedRef.current = selectedId;
  }, [selectedId, items, cameras]);

  return (
    <div className="map-frame">
      <div
        ref={containerRef}
        className={`map-canvas ${styles.mapCanvas}`}
        role="region"
        aria-label="Map of barriers and road cameras. The lists on this page hold everything it shows."
      />
    </div>
  );
}

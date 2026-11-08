"use client";

import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import { Map, Marker, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Obstruction } from "@/lib/types";
import { getKindMeta } from "./obstruction-meta";
import { effectShape, hazardShapeSvg } from "./ui/hazard-shapes";

const EDMONTON: [number, number] = [-113.4938, 53.5461];

export default function MapView({
  obstructions,
  selectedId,
  onSelect
}: {
  obstructions: Obstruction[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const markersRef = useRef<Marker[]>([]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: EDMONTON,
      zoom: 11,
      // Respect reduced motion in the library too, not only in CSS.
      fadeDuration: reduce ? 0 : 300
    });
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;
    return () => {
      markersRef.current.forEach((marker) => marker.remove());
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markersRef.current.forEach((marker) => marker.remove());
    // Markers are a pointer shortcut only. The list beside the map is the keyboard and
    // screen-reader path and selects the same items, so markers stay out of the tab
    // order and the accessibility tree (no 50+ duplicate tab stops; WCAG 2.5.8's
    // equivalent-control exception covers markers that overlap when zoomed out).
    markersRef.current = obstructions.map((item, index) => {
      const element = document.createElement("div");
      element.className = `map-marker${selectedId === item.id ? " is-selected" : ""}`;
      element.setAttribute("aria-hidden", "true");
      element.title = `${getKindMeta(item.kind).label}: ${item.title}`;
      // A constant SVG from hazard-shapes.ts; the item's text never goes into HTML.
      element.innerHTML = hazardShapeSvg(effectShape(item.effect), 24, `marker-${index}`);
      element.addEventListener("click", () => onSelect(item.id));
      return new Marker({ element, anchor: "center" }).setLngLat(item.geometry.coordinates).addTo(map);
    });
    return () => {
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current = [];
    };
  }, [obstructions, onSelect, selectedId]);

  return (
    <div className="map-frame">
      <div ref={containerRef} className="map-canvas" role="region" aria-label="Map of current Edmonton conditions" />
      <p className="map-status" aria-hidden="true">
        {obstructions.length
          ? `${obstructions.length} shown`
          : "No conditions to show"}
      </p>
    </div>
  );
}

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
    markersRef.current = obstructions.map((item, index) => {
      const label = getKindMeta(item.kind).label;
      const element = document.createElement("button");
      element.type = "button";
      element.className = `map-marker${selectedId === item.id ? " is-selected" : ""}`;
      element.setAttribute("aria-label", `${label}: ${item.title}`);
      element.setAttribute("aria-pressed", String(selectedId === item.id));
      // A constant SVG from hazard-shapes.ts; the title goes in aria-label, never in HTML.
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

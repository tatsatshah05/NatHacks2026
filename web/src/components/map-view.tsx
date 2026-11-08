"use client";

import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import { Map, Marker, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Obstruction } from "@/lib/types";
import { getKindMeta } from "./obstruction-meta";

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
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: EDMONTON,
      zoom: 11
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
    markersRef.current = obstructions.map((item) => {
      const meta = getKindMeta(item.kind);
      const element = document.createElement("button");
      element.type = "button";
      element.className = `map-marker ${meta.pattern}${selectedId === item.id ? " is-selected" : ""}`;
      element.setAttribute("aria-label", `${meta.label}: ${item.title}`);
      element.innerHTML = `<span aria-hidden="true">${meta.icon}</span><strong>${meta.label}</strong>`;
      element.addEventListener("click", () => onSelect(item.id));
      return new Marker({ element, anchor: "bottom" })
        .setLngLat(item.geometry.coordinates)
        .addTo(map);
    });
    return () => {
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current = [];
    };
  }, [obstructions, onSelect, selectedId]);

  return (
    <div className="map-frame">
      <div ref={containerRef} className="map-canvas" aria-label="Map of current Edmonton obstructions" />
      <div className="map-status" aria-live="polite">
        {obstructions.length ? `${obstructions.length} current obstruction${obstructions.length === 1 ? "" : "s"} shown` : "No current obstructions"}
      </div>
    </div>
  );
}

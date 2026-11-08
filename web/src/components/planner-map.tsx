"use client";

// The planner's map (OP-301): the API's routes on the OpenFreeMap liberty basemap.
//
// - The selected route is the thick line (6 px, action blue, on a white casing); the
//   other routes are thin muted lines. A walk and LRT trip draws its rides as a double
//   line, as on the route line beside it.
// - Start and end are filled circles with a "Start" / "End" tag.
// - Conditions on the selected route use the HazardMark shapes. They are pointer
//   shortcuts only: hidden from assistive technology and out of the tab order. A click
//   jumps to the same stop in the list, which is the keyboard and screen-reader path.
// - Selecting a route fits the map to it; under reduced motion it jumps, never flies.
//
// The map only illustrates the list. If WebGL or the basemap fails, a sentence says so
// and the list still holds everything.

import { MutableRefObject, useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, LngLatBoundsLike, Map as MapLibreMap, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { effectShape, hazardShapeSvg } from "./ui/hazard-shapes";
import type { PlannerPlace, RouteOut } from "@/lib/planner-types";
import styles from "./planner-map.module.css";

const EDMONTON: [number, number] = [-113.4938, 53.5461];
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

// The liberty basemap is light in both themes, so the lines use the light theme's
// colours: action #1450A3 is 7.8:1 on white and about 7:1 on the map's land colour;
// muted #4A5B6A is 7.0:1 on white. The white casing keeps the line off the roads.
const ACTION = "#1450A3";
const MUTED = "#4A5B6A";
const CASING = "#FFFFFF";

export type MapCondition = { id: string; effect: string; title: string; lng: number; lat: number };
export type MapCentre = { lat: number; lng: number };

type Props = {
  routes: RouteOut[];
  selectedRank: number | null;
  from: PlannerPlace | null;
  to: PlannerPlace | null;
  conditions: MapCondition[];
  onCondition: (id: string) => void;
  /** Filled with a function that reads the map centre, for "Use the map centre". */
  centreRef: MutableRefObject<(() => MapCentre | null) | null>;
};

type Coordinates = [number, number][];

function lineCoordinates(geometry: Record<string, unknown>): Coordinates {
  const coordinates = geometry.coordinates;
  if (geometry.type !== "LineString" || !Array.isArray(coordinates)) return [];
  return coordinates.filter(
    (point): point is [number, number] =>
      Array.isArray(point) && typeof point[0] === "number" && typeof point[1] === "number"
  );
}

function routeFeatures(routes: RouteOut[], selectedRank: number | null): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  // The others first, so the selected route draws on top.
  const ordered = [...routes].sort((a, b) => Number(a.rank === selectedRank) - Number(b.rank === selectedRank));
  for (const route of ordered) {
    const selected = route.rank === selectedRank;
    if (selected && route.legs?.length) {
      for (const leg of route.legs) {
        features.push({
          type: "Feature",
          properties: { selected: true, ride: leg.kind === "ride" },
          geometry: { type: "LineString", coordinates: lineCoordinates(leg.geometry) }
        });
      }
    } else {
      features.push({
        type: "Feature",
        properties: { selected, ride: false },
        geometry: { type: "LineString", coordinates: lineCoordinates(route.geometry) }
      });
    }
  }
  return { type: "FeatureCollection", features };
}

function boundsOf(points: Coordinates): LngLatBoundsLike | null {
  if (!points.length) return null;
  let [minLng, minLat] = points[0];
  let [maxLng, maxLat] = points[0];
  for (const [lng, lat] of points) {
    minLng = Math.min(minLng, lng);
    maxLng = Math.max(maxLng, lng);
    minLat = Math.min(minLat, lat);
    maxLat = Math.max(maxLat, lat);
  }
  return [
    [minLng, minLat],
    [maxLng, maxLat]
  ];
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function placeMarker(label: "Start" | "End"): HTMLElement {
  const element = document.createElement("div");
  element.className = styles.place;
  element.setAttribute("aria-hidden", "true");
  // Constant markup; no user text goes into HTML.
  element.innerHTML = `<span class="${styles.placeDot}"></span><span class="${styles.placeTag}">${label}</span>`;
  return element;
}

export default function PlannerMap({ routes, selectedRank, from, to, conditions, onCondition, centreRef }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const placeMarkers = useRef<Marker[]>([]);
  const conditionMarkers = useRef<Marker[]>([]);
  const onConditionRef = useRef(onCondition);
  const [loaded, setLoaded] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    onConditionRef.current = onCondition;
  }, [onCondition]);

  // Create the map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const reduce = prefersReducedMotion();
    let map: MapLibreMap;
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: STYLE_URL,
        center: EDMONTON,
        zoom: 11,
        fadeDuration: reduce ? 0 : 300,
        // On a phone the map sits in a scrolling page: one finger scrolls the page,
        // two move the map.
        cooperativeGestures: window.matchMedia("(pointer: coarse)").matches,
        attributionControl: { compact: true }
      });
    } catch {
      setProblem("The map cannot be drawn in this browser. The route list has everything the map would show.");
      return;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.getCanvas().setAttribute("aria-label", "Map of the routes. The route list has the same information.");
    map.on("load", () => {
      map.addSource("routes", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      const layout = { "line-join": "round", "line-cap": "round" } as const;
      map.addLayer({
        id: "route-others",
        type: "line",
        source: "routes",
        filter: ["==", ["get", "selected"], false],
        layout,
        paint: { "line-color": MUTED, "line-width": 3 }
      });
      map.addLayer({
        id: "route-casing",
        type: "line",
        source: "routes",
        filter: ["==", ["get", "selected"], true],
        layout,
        paint: { "line-color": CASING, "line-width": ["case", ["get", "ride"], 14, 10] }
      });
      map.addLayer({
        id: "route-selected",
        type: "line",
        source: "routes",
        filter: ["all", ["==", ["get", "selected"], true], ["==", ["get", "ride"], false]],
        layout,
        paint: { "line-color": ACTION, "line-width": 6 }
      });
      map.addLayer({
        id: "route-ride",
        type: "line",
        source: "routes",
        filter: ["all", ["==", ["get", "selected"], true], ["==", ["get", "ride"], true]],
        layout,
        // Two 3 px rails with a 5 px gap: the double line of an LRT ride.
        paint: { "line-color": ACTION, "line-width": 3, "line-gap-width": 5 }
      });
      setLoaded(true);
    });
    map.on("error", () => {
      if (!map.isStyleLoaded()) {
        setProblem("The map background did not load. The route list has everything the map would show.");
      }
    });
    mapRef.current = map;
    centreRef.current = () => {
      const centre = mapRef.current?.getCenter();
      return centre ? { lat: centre.lat, lng: centre.lng } : null;
    };
    return () => {
      centreRef.current = null;
      placeMarkers.current.forEach((marker) => marker.remove());
      conditionMarkers.current.forEach((marker) => marker.remove());
      map.remove();
      mapRef.current = null;
    };
  }, [centreRef]);

  // The route lines.
  useEffect(() => {
    const source = mapRef.current?.getSource("routes") as GeoJSONSource | undefined;
    if (loaded && source) source.setData(routeFeatures(routes, selectedRank));
  }, [loaded, routes, selectedRank]);

  // Fit the map to the selected route, or to the chosen places before there are routes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const selected = routes.find((route) => route.rank === selectedRank);
    const points: Coordinates = selected
      ? lineCoordinates(selected.geometry)
      : [from?.coordinates, to?.coordinates].filter((point): point is [number, number] => Boolean(point));
    const animate = !prefersReducedMotion();
    if (points.length === 1) {
      map.jumpTo({ center: points[0], zoom: Math.max(map.getZoom(), 14) });
      return;
    }
    const bounds = boundsOf(points);
    if (bounds) map.fitBounds(bounds, { padding: 56, maxZoom: 16, animate, duration: animate ? 500 : 0 });
  }, [routes, selectedRank, from, to]);

  // Start and end.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    placeMarkers.current.forEach((marker) => marker.remove());
    placeMarkers.current = [];
    for (const [place, label] of [
      [from, "Start"],
      [to, "End"]
    ] as const) {
      if (!place) continue;
      placeMarkers.current.push(
        new maplibregl.Marker({ element: placeMarker(label), anchor: "center" }).setLngLat(place.coordinates).addTo(map)
      );
    }
  }, [from, to]);

  // Conditions on the selected route.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    conditionMarkers.current.forEach((marker) => marker.remove());
    conditionMarkers.current = conditions.map((condition, index) => {
      const element = document.createElement("div");
      element.className = "map-marker";
      element.setAttribute("aria-hidden", "true");
      element.title = condition.title;
      // A constant SVG from hazard-shapes.ts; the item's text never goes into HTML.
      element.innerHTML = hazardShapeSvg(effectShape(condition.effect), 24, `route-marker-${index}`);
      element.addEventListener("click", () => onConditionRef.current(condition.id));
      return new maplibregl.Marker({ element, anchor: "center" }).setLngLat([condition.lng, condition.lat]).addTo(map);
    });
  }, [conditions]);

  const selected = routes.find((route) => route.rank === selectedRank);
  return (
    <div className={styles.frame}>
      <div ref={containerRef} className={styles.canvas} />
      {problem && <p className={styles.problem}>{problem}</p>}
      {selected && (
        <ul className={styles.key} aria-label="Map lines">
          <li>
            <svg className={styles.swatch} width="36" height="16" viewBox="0 0 36 16" aria-hidden="true" focusable="false">
              <path d="M6 8h24" stroke={ACTION} strokeWidth="6" strokeLinecap="round" />
            </svg>
            Thick line: route {selected.rank}
          </li>
          {selected.legs?.some((leg) => leg.kind === "ride") && (
            <li>
              <svg className={styles.swatch} width="36" height="16" viewBox="0 0 36 16" aria-hidden="true" focusable="false">
                <path d="M6 4h24M6 12h24" stroke={ACTION} strokeWidth="3" strokeLinecap="round" />
              </svg>
              Double line: LRT ride
            </li>
          )}
          {routes.length > 1 && (
            <li>
              <svg className={styles.swatch} width="36" height="16" viewBox="0 0 36 16" aria-hidden="true" focusable="false">
                <path d="M6 8h24" stroke={MUTED} strokeWidth="3" strokeLinecap="round" />
              </svg>
              Thin lines: the other routes
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

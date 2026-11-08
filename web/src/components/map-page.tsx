"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getObstructions } from "@/lib/api";
import { Obstruction } from "@/lib/types";
import MapView from "./map-view";
import { getKindMeta } from "./obstruction-meta";
import Button from "./ui/button";
import HazardMark, { HAZARD_SHAPES, SHAPE_TEXT, effectShape, effectText } from "./ui/hazard-mark";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import TimeAgo from "./ui/time-ago";

// The four shapes a city obstruction can take, for the legend under the map.
const LEGEND = HAZARD_SHAPES.filter(
  (shape): shape is "blocked" | "ice" | "snow" | "caution" => shape !== "start" && shape !== "clear"
);

export default function MapPage() {
  const [obstructions, setObstructions] = useState<Obstruction[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [now, setNow] = useState<Date>(() => new Date());

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      setObstructions(await getObstructions(signal));
      const at = new Date();
      setLastUpdated(at);
      setNow(at);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "The condition service did not answer.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  const selected = useMemo(() => obstructions.find((item) => item.id === selectedId), [obstructions, selectedId]);
  const count = loading
    ? "Loading current conditions."
    : error
      ? ""
      : obstructions.length
        ? `${obstructions.length} current ${obstructions.length === 1 ? "condition" : "conditions"} across Edmonton.`
        : "";

  return (
    <div className="shell">
      <SiteHeader current="city" />

      <main id="main" className="page">
        <div className="page-main">
          <h1 className="page-title">City conditions</h1>
          <div className="freshness">
            <p className="muted">
              {lastUpdated ? <TimeAgo iso={lastUpdated.toISOString()} now={now} verb="Updated" /> : "Not loaded yet."}
            </p>
            <Button variant="secondary" onClick={() => void load()} disabled={loading}>
              Refresh conditions
            </Button>
          </div>

          {error && (
            <div className="alert" role="alert">
              <p>
                <strong>Conditions could not be loaded.</strong> {error} Try again, or plan a route with the last known
                conditions.
              </p>
              <Button variant="secondary" onClick={() => void load()}>
                Try again
              </Button>
            </div>
          )}
          <div className="count-row">
            <StatusLine>{count}</StatusLine>
            {obstructions.length > 0 && <a href="#city-map">Go to the map</a>}
          </div>

          {!loading && !error && !obstructions.length && (
            <p>No current obstructions are reported. Plan a route to see conditions along it.</p>
          )}
          {obstructions.length > 0 && (
            <ul className="city-list" role="list" aria-label="Current conditions">
              {obstructions.map((item) => {
                const kind = getKindMeta(item.kind).label;
                const pressed = selectedId === item.id;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="list-button"
                      aria-pressed={pressed}
                      onClick={() => setSelectedId(pressed ? null : item.id)}
                    >
                      <HazardMark shape={effectShape(item.effect)} label={item.title} />
                      {item.detail && <p>{item.detail}</p>}
                      <p className="muted">
                        {kind}, {effectText(item.effect).toLowerCase()}. <TimeAgo iso={item.observed_at} now={now} />
                      </p>
                      <span className="sr-only">{pressed ? ", shown on the map" : ", show on the map"}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {selected && (
            <div className="selected-detail" role="status">
              <h2>{selected.title}</h2>
              <p>
                {effectText(selected.effect)}. {selected.detail || "No more detail from the source."}
              </p>
              {selected.attribution && <p className="muted">Source: {selected.attribution}</p>}
            </div>
          )}
        </div>

        <div className="page-side" id="city-map" tabIndex={-1}>
          <h2 className="sr-only">Map</h2>
          <MapView obstructions={obstructions} selectedId={selectedId} onSelect={setSelectedId} />
          <ul className="legend" aria-label="Map shapes">
            {LEGEND.map((shape) => (
              <li key={shape}>
                <HazardMark shape={shape} label={SHAPE_TEXT[shape]} size={18} />
              </li>
            ))}
          </ul>
          <p className="muted">
            The list holds everything the map shows; choosing an item there marks it on the map. Use the + and −
            buttons to zoom.
          </p>
        </div>
      </main>

      <SiteFooter>
        <p>Map data © OpenStreetMap contributors.</p>
      </SiteFooter>
    </div>
  );
}

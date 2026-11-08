"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getObstructions } from "@/lib/api";
import { Obstruction } from "@/lib/types";
import MapView from "./map-view";
import { formatObserved, getKindMeta, kindMeta } from "./obstruction-meta";
import Link from "next/link";

export default function MapPage() {
  const [obstructions, setObstructions] = useState<Obstruction[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      setObstructions(await getObstructions(signal));
      setLastUpdated(new Date());
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Unable to load current conditions.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const selected = useMemo(() => obstructions.find((item) => item.id === selectedId), [obstructions, selectedId]);

  return (
    <main className="app-shell">
      <header className="site-header">
        <Link className="brand" href="/" aria-label="OpenPath home"><span className="brand-mark" aria-hidden="true">⌁</span><span>OpenPath</span></Link>
        <nav aria-label="Primary navigation"><a href="#conditions">Conditions</a><a href="#legend">Legend</a><a href="#about">About</a></nav>
        <button className="outline-button" type="button" onClick={() => void load()}>Refresh</button>
      </header>

      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">Edmonton · live accessibility conditions</p>
        <h1 id="page-title">Know before you go.</h1>
        <p className="hero-copy">A clear view of current barriers, closures, snow and ice risks across the city.</p>
      </section>

      <section id="conditions" className="workspace" aria-label="Current conditions">
        <div className="map-panel">
          <div className="panel-heading"><div><p className="eyebrow">City overview</p><h2>Current obstructions</h2></div><span className="live-pill"><span aria-hidden="true" /> Live data</span></div>
          {error && <div className="alert" role="alert"><strong>Conditions are unavailable.</strong><span>{error}</span><button type="button" onClick={() => void load()}>Try again</button></div>}
          {loading && <div className="loading-bar" aria-label="Loading current conditions" />}
          <MapView obstructions={obstructions} selectedId={selectedId} onSelect={setSelectedId} />
          <p className="map-help">Use the + and − controls to zoom. Select a marker or use the list to inspect an obstruction.</p>
        </div>

        <aside className="side-panel" aria-labelledby="list-title">
          <div className="panel-heading"><div><p className="eyebrow">At a glance</p><h2 id="list-title">{obstructions.length} reported</h2></div>{lastUpdated && <time dateTime={lastUpdated.toISOString()}>Updated {lastUpdated.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>}</div>
          <div className="obstruction-list">
            {!loading && !error && !obstructions.length && <p className="empty-state">No current obstructions were reported by the data service.</p>}
            {obstructions.map((item) => {
              const meta = getKindMeta(item.kind);
              return <button key={item.id} type="button" className={`obstruction-card${selectedId === item.id ? " selected" : ""}`} onClick={() => setSelectedId(item.id)} aria-pressed={selectedId === item.id}>
                <span className={`card-icon ${meta.pattern}`} aria-hidden="true">{meta.icon}</span>
                <span className="card-content"><strong>{item.title}</strong><span>{item.detail || meta.label}</span><small>{formatObserved(item.observed_at)}</small></span>
                <span className="sr-only">Select on map</span>
              </button>;
            })}
          </div>
          {selected && <div className="selected-detail" role="status"><strong>{selected.title}</strong><p>{selected.detail || "No additional detail available."}</p>{selected.attribution && <small>Source: {selected.attribution}</small>}</div>}
        </aside>
      </section>

      <section id="legend" className="legend-section" aria-labelledby="legend-title"><div><p className="eyebrow">Read the map</p><h2 id="legend-title">Every condition has a sign</h2></div><div className="legend-grid">{Object.entries(kindMeta).map(([kind, meta]) => <div className="legend-item" key={kind}><span className={`card-icon ${meta.pattern}`} aria-hidden="true">{meta.icon}</span><span>{meta.label}</span></div>)}</div></section>
      <footer id="about"><strong>OpenPath</strong><span>Accessible information for every Edmonton journey.</span><span>Map data © OpenStreetMap contributors.</span></footer>
    </main>
  );
}

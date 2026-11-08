"use client";

import Link from "next/link";
import { useState } from "react";
import { mockRouteReportItems, mockRouteSteps, getMockRoutes } from "@/lib/planner-types";

const categories = ["Indoor", "Sidewalks and paths", "Roads and crossings", "Weather and ice"] as const;

export default function RouteReportPage() {
  const [showEmpty, setShowEmpty] = useState(false);
  const route = getMockRoutes({
    from: { id: "central-station", name: "Central Station", description: "10025 Jasper Avenue", coordinates: [-113.4939, 53.5447] },
    to: { id: "royal-alexandra", name: "Royal Alexandra Hospital", description: "10240 Kingsway NW", coordinates: [-113.4972, 53.5588] },
    profile: "wheelchair"
  })[0];

  return (
    <main className="app-shell report-shell">
      <header className="site-header">
        <Link className="brand" href="/" aria-label="PathSense home"><span className="brand-mark" aria-hidden="true">⌁</span><span>PathSense</span></Link>
        <nav aria-label="Primary navigation"><Link href="/">Plan</Link><a className="active-nav" href="#report">Route report</a><Link href="/city">City</Link></nav>
        <Link className="outline-button report-back" href="/">Back to planner</Link>
      </header>

      <section id="report" className="report-hero" aria-labelledby="report-title">
        <p className="eyebrow">Route report · Wheelchair</p>
        <h1 id="report-title">Central Station <span aria-hidden="true">→</span> Royal Alexandra Hospital</h1>
        <p className="hero-copy"><strong>{route.minutes} minutes</strong> · {route.distanceKm.toFixed(1)} km · {route.label}</p>
      </section>

      <div className="report-actions">
        <button className="primary-button report-save" type="button">Save this route</button>
        <button className="outline-button" type="button" onClick={() => setShowEmpty((current) => !current)}>{showEmpty ? "Show route conditions" : "Preview empty state"}</button>
      </div>

      <section className="report-summary" aria-labelledby="summary-title">
        <div><p className="eyebrow">Safety summary</p><h2 id="summary-title">{showEmpty ? "No known obstructions on this route" : `${mockRouteReportItems.length} conditions to know about`}</h2></div>
        <p>{showEmpty ? "Sources checked just now. Conditions can change, so check again before departure." : "The route avoids blocked paths and accounts for current conditions listed below."}</p>
      </section>

      <section className="report-content" aria-labelledby="conditions-title">
        <div className="report-list">
          <div className="section-heading"><div><p className="eyebrow">Route conditions</p><h2 id="conditions-title">What to expect</h2></div><span className="freshness">Sources checked 1 min ago</span></div>
          {showEmpty ? <div className="empty-report" role="status"><span className="empty-check" aria-hidden="true">✓</span><h3>No known obstructions on this route</h3><p>We checked indoor access, sidewalks and paths, roads and crossings, and weather and ice sources.</p><p className="freshness">Source freshness: City data 3 min ago · Weather 16 min ago</p></div> : categories.map((category) => <ReportGroup key={category} category={category} />)}
        </div>
        <aside className="report-map" aria-label="Route overview">
          <div className="report-map-placeholder"><span className="route-line" aria-hidden="true" /><span className="route-pin pin-start" aria-hidden="true">A</span><span className="route-pin pin-end" aria-hidden="true">B</span><p>Map is supplementary. Every step and condition is listed in text.</p></div>
        </aside>
      </section>

      <section className="steps-section" aria-labelledby="steps-title">
        <div className="section-heading"><div><p className="eyebrow">Turn-by-turn</p><h2 id="steps-title">Route steps</h2></div><span className="freshness">4 steps</span></div>
        <ol className="route-steps">{mockRouteSteps.map((step) => <li key={step.number}><span className="step-number" aria-hidden="true">{step.number}</span><div><strong>{step.instruction}</strong><p>{step.detail}</p></div></li>)}</ol>
      </section>

      <footer><strong>PathSense</strong><span>Conditions are sourced from public data and may change.</span><Link href="/city">View all city conditions</Link></footer>
    </main>
  );
}

function ReportGroup({ category }: { category: (typeof categories)[number] }) {
  const items = mockRouteReportItems.filter((item) => item.category === category);
  return (
    <section className="report-group" aria-labelledby={`group-${category}`}>
      <h3 id={`group-${category}`}>{category}</h3>
      {items.length ? items.map((item) => <article className="report-item" key={item.id}><span className="report-icon pattern-stripe" aria-hidden="true">{item.icon}</span><div className="report-item-content"><h4>{item.title}</h4><p>{item.detail}</p><div className="report-meta"><span>{item.source}</span><span>Updated {item.updatedMinutesAgo} min ago</span><a href={item.sourceUrl} target="_blank" rel="noreferrer">View source<span className="sr-only"> for {item.title}</span></a></div></div></article>) : <p className="group-empty">No known obstructions in this category.</p>}
    </section>
  );
}

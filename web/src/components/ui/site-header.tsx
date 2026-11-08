import Link from "next/link";
import type { ReactNode } from "react";
import ModeBadge from "./mode-badge";
import OfflineLine from "./offline-line";

/** The page a header is on. Sources and privacy belong to the About section. */
export type Section = "plan" | "city" | "alerts" | "settings" | "about" | "sources" | "privacy";

const LINKS: ReadonlyArray<{ id: Section; href: string; label: string; also?: readonly Section[] }> = [
  { id: "plan", href: "/", label: "Plan" },
  { id: "city", href: "/city", label: "City" },
  { id: "alerts", href: "/alerts", label: "Alerts" },
  { id: "settings", href: "/settings", label: "Settings" },
  { id: "about", href: "/about", label: "About", also: ["sources", "privacy"] }
];

type Props = { current?: Section; badge?: ReactNode };

// aria-current="page" on the link to this page; "true" on the section's link when the
// page sits under it (sources and privacy under About). Both are underlined.
function currentFor(link: (typeof LINKS)[number], current: Section | undefined): "page" | "true" | undefined {
  if (!current) return undefined;
  if (link.id === current) return "page";
  return link.also?.includes(current) ? "true" : undefined;
}

/** Skip link, wordmark, the main links, the mode badge and the offline line. Same on every page. */
export default function SiteHeader({ current, badge = <ModeBadge /> }: Props) {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="site-header">
        <Link className="wordmark" href="/">
          <svg className="wordmark-glyph" viewBox="0 0 30 14" aria-hidden="true" focusable="false">
            <path d="M5 7h20" stroke="currentColor" strokeWidth="3" />
            <circle cx="5" cy="7" r="4.5" fill="currentColor" />
            <circle cx="25" cy="7" r="4.5" fill="currentColor" />
          </svg>
          OpenPath
        </Link>
        {badge}
        <nav className="site-nav" aria-label="Main">
          {LINKS.map((link) => (
            <Link key={link.id} href={link.href} aria-current={currentFor(link, current)}>
              {link.label}
            </Link>
          ))}
        </nav>
      </header>
      <OfflineLine />
    </>
  );
}

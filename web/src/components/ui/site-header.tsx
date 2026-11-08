import Link from "next/link";
import type { ReactNode } from "react";
import ModeBadge from "./mode-badge";

export type Section = "plan" | "city" | "alerts" | "about";

const LINKS: ReadonlyArray<{ id: Section; href: string; label: string }> = [
  { id: "plan", href: "/", label: "Plan" },
  { id: "city", href: "/city", label: "City" },
  { id: "alerts", href: "/alerts", label: "Alerts" },
  { id: "about", href: "/about", label: "About" }
];

type Props = { current?: Section; badge?: ReactNode };

/** Skip link, wordmark, the four main links and the mode badge. Same on every page. */
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
            <Link key={link.id} href={link.href} aria-current={current === link.id ? "page" : undefined}>
              {link.label}
            </Link>
          ))}
        </nav>
      </header>
    </>
  );
}

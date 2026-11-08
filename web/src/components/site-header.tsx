import Link from "next/link";

type Section = "plan" | "report";

// Header for the planner and route report: a skip link, the brand and the main links.
// Unlike the city page's in-page links, these stay visible on narrow screens.
export default function SiteHeader({ current }: { current: Section }) {
  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <header className="site-header">
        <Link className="brand" href="/" aria-label="OpenPath home">
          <span className="brand-mark" aria-hidden="true">⌁</span>
          <span>OpenPath</span>
        </Link>
        <nav className="site-nav" aria-label="Primary navigation">
          <Link href="/" aria-current={current === "plan" ? "page" : undefined}>Plan</Link>
          <Link href="/city">City</Link>
        </nav>
      </header>
    </>
  );
}

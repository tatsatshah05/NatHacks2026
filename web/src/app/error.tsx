"use client";

import Button, { ButtonLink } from "@/components/ui/button";
import SiteFooter from "@/components/ui/site-footer";
import SiteHeader from "@/components/ui/site-header";

type Props = { error: Error & { digest?: string }; reset: () => void };

// OP-410: a page that threw while rendering. Next.js shows this instead of the page,
// inside the root layout. It says what happened and what to do, and "Try again"
// renders the page once more (DESIGN.md section 10).
export default function ErrorPage({ error, reset }: Props) {
  return (
    <div className="shell">
      <SiteHeader />
      <main id="main" className="page-problem">
        <div className="problem-block" role="alert">
          <h1>This page stopped working.</h1>
          <p className="lead">Try again. If it happens again, start from the planner.</p>
          {error.digest && <p className="muted">Error reference {error.digest}.</p>}
          <div className="problem-actions">
            <Button onClick={reset}>Try again</Button>
            <ButtonLink variant="secondary" href="/">
              Plan a route
            </ButtonLink>
          </div>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}

import type { Metadata } from "next";
import { ButtonLink } from "@/components/ui/button";
import SiteFooter from "@/components/ui/site-footer";
import SiteHeader from "@/components/ui/site-header";

export const metadata: Metadata = { title: "Page not found | OpenPath" };

// OP-410: a missing page says what happened and where to go, in the design system
// (DESIGN.md section 10). The header and footer stay, so the Replay badge and the
// offline line are still there.
export default function NotFound() {
  return (
    <div className="shell">
      <SiteHeader />
      <main id="main" className="page-problem">
        <div className="problem-block">
          <h1>This page does not exist.</h1>
          <p className="lead">Check the address, or start from the planner.</p>
          <div className="problem-actions">
            <ButtonLink href="/">Plan a route</ButtonLink>
            <ButtonLink variant="secondary" href="/city">
              See city conditions
            </ButtonLink>
          </div>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}

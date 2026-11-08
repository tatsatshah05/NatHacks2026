import type { Metadata } from "next";
import { Suspense } from "react";
import RouteReportPage from "@/components/route-report-page";

export const metadata: Metadata = { title: "Route report | OpenPath" };

// The report reads its trip from the URL, so it renders in the browser.
export default function RoutePage() {
  return (
    <Suspense fallback={<p className="shell status-line">Loading the route report.</p>}>
      <RouteReportPage />
    </Suspense>
  );
}

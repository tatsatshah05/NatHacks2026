import type { Metadata } from "next";
import AlertsPage from "@/components/alerts-page";

export const metadata: Metadata = { title: "Alerts | OpenPath" };

export default function Page() {
  return <AlertsPage />;
}

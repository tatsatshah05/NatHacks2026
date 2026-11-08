import type { Metadata } from "next";
import PlannerPage from "@/components/planner-page";

export const metadata: Metadata = { title: "Plan a route | OpenPath" };

export default function Page() {
  return <PlannerPage />;
}

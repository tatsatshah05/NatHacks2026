import type { Metadata } from "next";
import CityDashboard from "@/components/city/city-dashboard";

export const metadata: Metadata = { title: "Barriers to clear first | OpenPath" };

export default function CityPage() {
  return <CityDashboard />;
}

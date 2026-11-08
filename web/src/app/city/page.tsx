import type { Metadata } from "next";
import MapPage from "@/components/map-page";

export const metadata: Metadata = { title: "City conditions | OpenPath" };

export default function CityPage() {
  return <MapPage />;
}

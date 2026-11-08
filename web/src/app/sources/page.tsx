import type { Metadata } from "next";
import SourcesPage from "@/components/sources-page";

export const metadata: Metadata = { title: "Data sources | OpenPath" };

export default function Page() {
  return <SourcesPage />;
}

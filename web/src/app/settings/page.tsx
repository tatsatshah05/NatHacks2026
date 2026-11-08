import type { Metadata } from "next";
import SettingsPage from "@/components/settings-page";

export const metadata: Metadata = { title: "Settings | OpenPath" };

export default function Page() {
  return <SettingsPage />;
}

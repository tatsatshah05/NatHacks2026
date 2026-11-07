import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenPath",
  description:
    "Every barrier between A and B, in one place. Accessible routing for Edmonton.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}

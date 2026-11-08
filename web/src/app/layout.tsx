import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenPath | Edmonton accessibility map",
  description: "See current obstructions and accessible travel conditions across Edmonton."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

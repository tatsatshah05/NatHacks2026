import type { Metadata } from "next";
import { Atkinson_Hyperlegible } from "next/font/google";
import SettingsApplier from "@/components/settings-applier";
import { SETTINGS_BOOT_SCRIPT } from "@/lib/settings-boot";
import "./globals.css";

// One family, two weights. Atkinson Hyperlegible was drawn for low-vision readers;
// its distinct letterforms are the only typographic voice the app has.
const atkinson = Atkinson_Hyperlegible({
  subsets: ["latin"],
  weight: ["400", "700"],
  display: "swap",
  variable: "--font-body"
});

export const metadata: Metadata = {
  title: "OpenPath",
  description: "Accessible routes across Edmonton, with every known barrier between A and B."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // The boot script sets data-text, data-motion and data-theme on <html> before
    // React hydrates, so the attributes differ from the server render on purpose.
    <html lang="en" className={atkinson.variable} suppressHydrationWarning>
      <head>
        {/* OP-307: apply text size, motion and theme before the first paint. A plain
            inline script, because next/script's beforeInteractive only queues inline
            code in the App Router and it would run after the page has painted. It is
            a constant, so a future CSP can allow it by hash (settings-boot.ts). */}
        <script id="openpath-settings" dangerouslySetInnerHTML={{ __html: SETTINGS_BOOT_SCRIPT }} />
      </head>
      <body>
        <SettingsApplier />
        {children}
      </body>
    </html>
  );
}

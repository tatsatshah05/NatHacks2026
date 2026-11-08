import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible } from "next/font/google";
import ServiceWorkerRegistration from "@/components/service-worker";
import SettingsApplier from "@/components/settings-applier";
import { SETTINGS_BOOT_SCRIPT } from "@/lib/settings-boot";
import { PITCH, PRODUCTION_URL, SITE_NAME } from "@/lib/site";
import "./globals.css";

// One family, two weights. Atkinson Hyperlegible was drawn for low-vision readers;
// its distinct letterforms are the only typographic voice the app has.
const atkinson = Atkinson_Hyperlegible({
  subsets: ["latin"],
  weight: ["400", "700"],
  display: "swap",
  variable: "--font-body"
});

// OP-411: every page shares this; a page sets its own full title ("Plan a route | OpenPath").
// Absolute links in the social tags resolve against the production address, so a shared
// link always previews the real app. The icons are files beside this layout, which Next
// links on its own (an `icons` field here would replace them): icon.svg (light and dark
// variants), icon1.png for browsers without SVG favicons, apple-icon.png for iOS. All
// three come from scripts/make_icons.py.
export const metadata: Metadata = {
  metadataBase: new URL(PRODUCTION_URL),
  title: SITE_NAME,
  description: PITCH,
  applicationName: SITE_NAME,
  // OP-306: the installable app (Add to Home Screen) that push alerts need on iOS.
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: SITE_NAME, statusBarStyle: "default" },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    title: SITE_NAME,
    description: PITCH,
    url: "/",
    locale: "en_CA"
  },
  twitter: { card: "summary_large_image", title: SITE_NAME, description: PITCH }
};

// The browser bar matches the page (--snow) in each theme.
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F5F8FA" },
    { media: "(prefers-color-scheme: dark)", color: "#0D1F2D" }
  ]
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
        <ServiceWorkerRegistration />
        {children}
      </body>
    </html>
  );
}

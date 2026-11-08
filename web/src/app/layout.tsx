import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible } from "next/font/google";
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
  description: "Accessible routes across Edmonton, with every known barrier between A and B.",
  // OP-306: the installable app (Add to Home Screen) that push alerts need on iOS.
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [
      { url: "/icons/icon.svg", type: "image/svg+xml" },
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" }
    ],
    apple: { url: "/apple-touch-icon.png", sizes: "180x180" }
  },
  appleWebApp: { capable: true, title: "OpenPath", statusBarStyle: "default" }
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
    <html lang="en" className={atkinson.variable}>
      <body>{children}</body>
    </html>
  );
}

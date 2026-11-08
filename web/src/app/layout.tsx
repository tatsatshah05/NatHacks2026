import type { Metadata } from "next";
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
  description: "Accessible routes across Edmonton, with every known barrier between A and B."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={atkinson.variable}>
      <body>{children}</body>
    </html>
  );
}

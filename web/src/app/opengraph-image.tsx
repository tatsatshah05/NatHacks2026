import { ImageResponse } from "next/og";
import { PITCH, PRODUCTION_URL, SITE_NAME, displayUrl } from "@/lib/site";

// The social preview (OP-411): the mark, the name, the pitch and the address, in
// the light theme's tokens (docs/design/DESIGN.md section 3). Next renders it once
// at build time and serves it at /opengraph-image; the metadata in layout.tsx
// links it as the Open Graph image, and Twitter falls back to the same image.

export const alt = `${SITE_NAME}. ${PITCH} ${displayUrl(PRODUCTION_URL)}`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const SNOW = "#F5F8FA";
const INK = "#0D1F2D";
const MUTED = "#4A5B6A";
const ACTION = "#1450A3";
const ON_ACTION = "#FFFFFF";

// Atkinson Hyperlegible, the app's only typeface, fetched from Google Fonts when the
// image is built. If that fails, the image still renders in the renderer's default
// sans serif: a preview with the wrong face beats a failed build.
async function loadFont(weight: 400 | 700): Promise<ArrayBuffer | null> {
  try {
    const css = await fetch(
      `https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@${weight}&text=${encodeURIComponent(
        `${SITE_NAME}${PITCH}${displayUrl(PRODUCTION_URL)}`
      )}`,
      // Without a modern user agent Google serves TrueType, which the renderer reads.
      { headers: { "User-Agent": "Mozilla/5.0" } }
    ).then((response) => (response.ok ? response.text() : ""));
    const match = css.match(/src: url\((.+?)\) format\('(?:opentype|truetype)'\)/);
    if (!match) return null;
    const file = await fetch(match[1]);
    return file.ok ? file.arrayBuffer() : null;
  } catch {
    return null;
  }
}

/** The app icon's mark: the route glyph with its 45 degree jog (scripts/build_icons.py). */
function Mark() {
  return (
    <svg width="248" height="248" viewBox="0 0 512 512" aria-hidden="true">
      <rect width="512" height="512" rx="96" fill={ACTION} />
      <path
        d="M208 400 L208 304 L304 208 L304 112"
        fill="none"
        stroke={ON_ACTION}
        strokeWidth="30"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx="208" cy="400" r="44" fill={ON_ACTION} />
      <circle cx="304" cy="112" r="44" fill={ON_ACTION} />
    </svg>
  );
}

export default async function OpenGraphImage() {
  const [regular, bold] = await Promise.all([loadFont(400), loadFont(700)]);
  const fonts = [
    regular ? { name: "Atkinson Hyperlegible", data: regular, weight: 400 as const, style: "normal" as const } : null,
    bold ? { name: "Atkinson Hyperlegible", data: bold, weight: 700 as const, style: "normal" as const } : null
  ].filter((font) => font !== null);
  const family = fonts.length ? "Atkinson Hyperlegible" : "sans-serif";

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          gap: 72,
          padding: "0 96px",
          background: SNOW,
          color: INK,
          fontFamily: family
        }}
      >
        <Mark />
        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div style={{ fontSize: 88, fontWeight: 700, lineHeight: 1 }}>{SITE_NAME}</div>
          <div style={{ fontSize: 38, lineHeight: 1.3, marginTop: 28, color: INK }}>{PITCH}</div>
          <div style={{ fontSize: 34, lineHeight: 1.2, marginTop: 36, color: MUTED }}>{displayUrl(PRODUCTION_URL)}</div>
        </div>
      </div>
    ),
    { ...size, fonts: fonts.length ? fonts : undefined }
  );
}

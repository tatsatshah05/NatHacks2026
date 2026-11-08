// The road camera glyph: a camera body with its lens hood, drawn in ink. Its outline
// differs from every hazard shape (square, diamond, hexagon, triangle, circle), so a
// camera never reads as a barrier.
const PATHS = '<rect x="2.5" y="7" width="13" height="10" rx="2"/><path d="M15.5 10.5 21.5 7v10l-6-3.5z"/>';

/** The glyph as an SVG string, for MapLibre markers. A constant: no data goes into it. */
export function cameraGlyphSvg(size: number): string {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">${PATHS}<circle cx="7" cy="12" r="1.6" fill="currentColor" stroke="none"/></svg>`;
}

/** The glyph alone, hidden from assistive technology. Put the words next to it. */
export default function CameraGlyph({ size = 20, className }: { size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinejoin="round"
    >
      <rect x="2.5" y="7" width="13" height="10" rx="2" />
      <path d="M15.5 10.5 21.5 7v10l-6-3.5z" />
      <circle cx="7" cy="12" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

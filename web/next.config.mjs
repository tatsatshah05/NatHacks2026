// Security headers (OP-401). Every response from this app carries them. The
// Content-Security-Policy is built once, at build time, from the public API variables.
//
// - MapLibre GL needs `worker-src blob:` and `child-src blob:` for its worker and
//   `img-src data: blob:` for the images it draws. Tiles, glyphs and sprites come from
//   OpenFreeMap (src/lib/map-style.ts) through fetch, so `connect-src` names that host.
// - `connect-src` also names the API and its WebSocket (NEXT_PUBLIC_API_URL,
//   NEXT_PUBLIC_API_BASE_URL, NEXT_PUBLIC_WS_URL). Empty means this host, which
//   'self' already covers.
// - Next.js injects inline style attributes and, in production, inline scripts that
//   carry each page's data (`self.__next_f.push(...)`). Those scripts differ per
//   request, so they cannot be allowed by hash, and a nonce would make every page
//   render on each request. `script-src` therefore allows inline scripts; the rest
//   of the policy is strict. docs/SECURITY.md lists the nonce as a follow-up.
// - next/font serves its font files from this host, so `font-src 'self'`.
// - `next dev` needs eval (source maps) and a WebSocket for reloads: development only.
// - Permissions-Policy: the planner and the Report button may use the location
//   ("Use my location"). The camera and microphone are never used; the report photo
//   is a file input with `capture`, which needs no permission policy.

const TILE_HOST = "https://tiles.openfreemap.org";
const dev = process.env.NODE_ENV === "development";

function originOf(value) {
  if (!value) return null;
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

/** The API and WebSocket origins the pages talk to, from the build-time variables. */
function apiOrigins() {
  const origins = new Set();
  for (const value of [process.env.NEXT_PUBLIC_API_BASE_URL, process.env.NEXT_PUBLIC_API_URL]) {
    const api = originOf(value);
    if (api) {
      origins.add(api);
      origins.add(api.replace(/^http/, "ws")); // src/lib/live.ts derives the socket URL this way
    }
  }
  const ws = originOf(process.env.NEXT_PUBLIC_WS_URL);
  if (ws) origins.add(ws);
  return [...origins];
}

const connectSources = ["'self'", TILE_HOST, ...apiOrigins(), ...(dev ? ["ws:", "wss:"] : [])];

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${TILE_HOST}`,
  "font-src 'self'",
  `connect-src ${connectSources.join(" ")}`,
  "worker-src 'self' blob:",
  "child-src 'self' blob:",
  "manifest-src 'self'"
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(self)" },
  { key: "X-Frame-Options", value: "DENY" }
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  }
};

export default nextConfig;

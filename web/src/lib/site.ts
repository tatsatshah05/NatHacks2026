// The site's name, pitch and production address, shared by the page metadata
// (app/layout.tsx), the social preview (app/opengraph-image.tsx) and the judge
// page (app/demo). One place, so the three never disagree.

export const SITE_NAME = "OpenPath";

/** The production web app (docs/DEPLOY.md section 7). The judge page's QR code points here. */
export const PRODUCTION_URL = "https://openpath-lyart.vercel.app";

/** The one-line pitch (PLAN.md section 1, also the manifest's description). */
export const PITCH = "Accessible routes across Edmonton, with every known barrier between A and B.";

/** The production address as people read it, without the scheme. */
export function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "");
}

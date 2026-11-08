import type { TravelProfile } from "@/lib/planner-types";

const STROKE = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" } as const;

/** Simple line glyphs for the four profiles, drawn in the text colour. Decorative. */
export default function ProfileIcon({ profile }: { profile: TravelProfile }) {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false" {...STROKE}>
      {profile === "wheelchair" && (
        <>
          <circle cx="10" cy="4" r="2" />
          <path d="M10 7v7h5l3 6" />
          <path d="M6.5 11.5a5.5 5.5 0 1 0 7.5 7.5" />
        </>
      )}
      {profile === "walker_cane" && (
        <>
          <circle cx="10" cy="4" r="2" />
          <path d="M10 7v7l-3 7M10 14l3 7M14 10l3 11" />
        </>
      )}
      {profile === "low_vision" && (
        <>
          <path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z" />
          <circle cx="12" cy="12" r="3" />
        </>
      )}
      {profile === "standard" && (
        <>
          <circle cx="13" cy="4" r="2" />
          <path d="M13 7l-3 5 3 2 1 6M10 12l-3 2-1 5M13 14l3 3 3 1M10 12l-1-2 4-3 3 3" />
        </>
      )}
    </svg>
  );
}

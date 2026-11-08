import { ObstructionKind } from "@/lib/types";

export const kindMeta: Record<string, { label: string; icon: string; pattern: string }> = {
  elevator_out: { label: "Elevator out", icon: "⇅", pattern: "pattern-cross" },
  escalator_out: { label: "Escalator out", icon: "↗", pattern: "pattern-diagonal" },
  sidewalk_closed: { label: "Sidewalk closed", icon: "▣", pattern: "pattern-stripe" },
  road_closed: { label: "Road closed", icon: "⊘", pattern: "pattern-cross" },
  trail_closed: { label: "Trail closed", icon: "⌁", pattern: "pattern-stripe" },
  trail_caution: { label: "Trail caution", icon: "!", pattern: "pattern-dot" },
  plow_passed: { label: "Plow passed", icon: "✦", pattern: "pattern-dot" },
  snow_complaints: { label: "Snow complaint", icon: "❄", pattern: "pattern-dot" },
  snow: { label: "Snow", icon: "❄", pattern: "pattern-stripe" },
  ice_risk: { label: "Ice risk", icon: "◇", pattern: "pattern-diagonal" },
  blocked: { label: "Blocked", icon: "!", pattern: "pattern-cross" }
};

export function getKindMeta(kind: ObstructionKind) {
  return kindMeta[kind] ?? { label: "Obstruction", icon: "!", pattern: "pattern-cross" };
}

export function formatObserved(value?: string | null) {
  if (!value) return "Time unknown";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Time unknown" : `Observed ${date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
}

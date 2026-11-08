import { ObstructionKind } from "@/lib/types";

export { effectShape, effectText } from "./ui/hazard-shapes";

// The kind gives an obstruction its words; its effect gives it its shape (see
// components/ui/hazard-shapes.ts).
export const kindMeta: Record<string, { label: string }> = {
  elevator_out: { label: "Elevator out" },
  escalator_out: { label: "Escalator out" },
  sidewalk_closed: { label: "Sidewalk closed" },
  road_closed: { label: "Road closed" },
  trail_closed: { label: "Trail closed" },
  trail_caution: { label: "Trail caution" },
  plow_passed: { label: "Plow passed" },
  snow_complaints: { label: "Snow complaint" },
  snow: { label: "Snow" },
  ice_risk: { label: "Ice risk" },
  blocked: { label: "Blocked" }
};

export function getKindMeta(kind: ObstructionKind): { label: string } {
  return kindMeta[kind] ?? { label: "Obstruction" };
}

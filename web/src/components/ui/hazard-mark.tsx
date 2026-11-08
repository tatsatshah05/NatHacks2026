import { createElement, useId } from "react";
import { HazardShape, SHAPE_SPECS } from "./hazard-shapes";

export type { HazardShape } from "./hazard-shapes";
export { effectShape, effectText, hazardShapeSvg, HAZARD_SHAPES, SHAPE_TEXT } from "./hazard-shapes";

type ShapeProps = { shape: HazardShape; size?: number; className?: string };

/** The shape alone, hidden from assistive technology. Put the words next to it. */
export function HazardShapeIcon({ shape, size = 20, className }: ShapeProps) {
  // useId output can hold characters that do not belong in a url(#id) reference.
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const spec = SHAPE_SPECS[shape];
  const patternId = `hz${id}-${shape}`;
  const fill = spec.fill === "pattern" ? `url(#${patternId})` : spec.fill === "solid" ? "currentColor" : "none";
  return (
    <svg
      className={className ? `hazard-shape ${className}` : "hazard-shape"}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      style={{ color: spec.colour }}
      aria-hidden="true"
      focusable="false"
    >
      {spec.pattern && (
        <defs>
          <pattern
            id={patternId}
            patternUnits="userSpaceOnUse"
            width={spec.pattern.width}
            height={spec.pattern.height}
            patternTransform={`rotate(${spec.pattern.rotate})`}
            // A constant from hazard-shapes.ts, never user content.
            dangerouslySetInnerHTML={{ __html: spec.pattern.tile }}
          />
        </defs>
      )}
      {createElement(spec.outline.tag, {
        ...spec.outline.attrs,
        fill,
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinejoin: "round"
      })}
      {spec.extra && <g dangerouslySetInnerHTML={{ __html: spec.extra }} />}
    </svg>
  );
}

type MarkProps = ShapeProps & { label: string };

/** Shape plus its visible text label: the unit every list and legend uses. */
export default function HazardMark({ shape, label, size = 20, className }: MarkProps) {
  return (
    <span className={className ? `hazard-mark ${className}` : "hazard-mark"}>
      <HazardShapeIcon shape={shape} size={size} />
      <span className="hazard-label">{label}</span>
    </span>
  );
}

import type { HTMLAttributes, ReactNode } from "react";

type Props = {
  as?: "section" | "article" | "div" | "li";
  className?: string;
  children: ReactNode;
} & Omit<HTMLAttributes<HTMLElement>, "className" | "children">;

/** A card surface with one 2 px ink rule on top. No shadow, no frame. */
export default function Panel({ as: Tag = "section", className, children, ...rest }: Props) {
  return (
    <Tag className={className ? `panel ${className}` : "panel"} {...rest}>
      {children}
    </Tag>
  );
}

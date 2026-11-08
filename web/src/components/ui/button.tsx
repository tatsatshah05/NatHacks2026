import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "text";
type Size = "default" | "large";

type Styling = {
  variant?: Variant;
  size?: Size;
  /** Full width of its container. */
  block?: boolean;
  className?: string;
  children: ReactNode;
};

function classes({ variant = "primary", size = "default", block, className }: Omit<Styling, "children">): string {
  return [
    "button",
    variant !== "primary" ? `button-${variant}` : "",
    size === "large" ? "button-large" : "",
    block ? "button-block" : "",
    className ?? ""
  ]
    .filter(Boolean)
    .join(" ");
}

type ButtonProps = Styling & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children">;

/** A button says what happens: "Find routes", not "Submit". 44 px minimum, 56 px large. */
export default function Button({ variant, size, block, className, type = "button", children, ...rest }: ButtonProps) {
  return (
    <button className={classes({ variant, size, block, className })} type={type} {...rest}>
      {children}
    </button>
  );
}

type LinkProps = Styling & { href: string } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className" | "children" | "href">;

/** A link styled as a button. External hrefs get a plain anchor with rel="noreferrer". */
export function ButtonLink({ variant, size, block, className, href, children, ...rest }: LinkProps) {
  const name = classes({ variant, size, block, className });
  if (/^https?:/.test(href)) {
    return (
      <a className={name} href={href} rel="noreferrer" {...rest}>
        {children}
      </a>
    );
  }
  return (
    <Link className={name} href={href} {...rest}>
      {children}
    </Link>
  );
}

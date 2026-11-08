import type { ReactNode } from "react";

type Props = { children?: ReactNode };

export default function SiteFooter({ children }: Props) {
  return (
    <footer className="site-footer">
      <p>Conditions come from public data and can change. Check again before you leave.</p>
      {children}
    </footer>
  );
}

import Link from "next/link";
import type { ReactNode } from "react";
import styles from "./site-footer.module.css";

type Props = { children?: ReactNode };

export default function SiteFooter({ children }: Props) {
  return (
    <footer className="site-footer">
      <p>Conditions come from public data and can change. Check again before you leave.</p>
      {children}
      <nav className={styles.links} aria-label="About OpenPath">
        <Link href="/sources">Data sources</Link>
        <Link href="/privacy">Privacy</Link>
        <Link href="/demo">Demo</Link>
      </nav>
    </footer>
  );
}

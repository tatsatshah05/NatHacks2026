import type { ReactNode } from "react";

type Props = { children: ReactNode; className?: string; id?: string };

/** One line of status, announced politely when it changes. Keep it in the DOM, even empty. */
export default function StatusLine({ children, className, id }: Props) {
  return (
    <p className={className ? `status-line ${className}` : "status-line"} id={id} role="status">
      {children}
    </p>
  );
}

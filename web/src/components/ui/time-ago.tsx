import { formatAgo, formatDate } from "@/lib/report-format";

type Props = {
  iso: string | null | undefined;
  now: Date;
  /** "Checked" for a source reading, "Updated" for this page. */
  verb?: "Checked" | "Updated";
};

/** "Checked 4 min ago", "Updated just now". Words, not a timestamp; the exact time is the title. */
export default function TimeAgo({ iso, now, verb = "Checked" }: Props) {
  const ago = iso ? formatAgo(iso, now) : null;
  if (!ago || !iso) return <span>{verb === "Checked" ? "Check time unknown" : "Update time unknown"}</span>;
  return (
    <span>
      {verb}{" "}
      <time dateTime={iso} title={formatDate(iso)}>
        {ago}
      </time>
    </span>
  );
}

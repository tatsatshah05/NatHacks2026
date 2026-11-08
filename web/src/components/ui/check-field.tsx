import type { ReactNode } from "react";
import styles from "./check-field.module.css";

type Props = {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** One quiet line under the label, tied with aria-describedby. */
  help?: ReactNode;
  id: string;
};

/** A native checkbox with its label: a 44 px row, the box drawn in ink. */
export default function CheckField({ label, checked, onChange, help, id }: Props) {
  const helpId = `${id}-help`;
  return (
    <div className={styles.field}>
      <label className={styles.row} htmlFor={id}>
        <input
          id={id}
          className={styles.box}
          type="checkbox"
          checked={checked}
          aria-describedby={help ? helpId : undefined}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className={styles.label}>{label}</span>
      </label>
      {help && (
        <p className={styles.help} id={helpId}>
          {help}
        </p>
      )}
    </div>
  );
}

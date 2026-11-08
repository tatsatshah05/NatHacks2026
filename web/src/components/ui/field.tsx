import type { ComponentPropsWithRef, ReactNode } from "react";

type Props = {
  id: string;
  label: string;
  /** Help text, tied to the input with aria-describedby. */
  help?: string;
  /** Keep the help for screen readers only, when the label already says enough. */
  helpHidden?: boolean;
  /** What went wrong and what to do: "Choose a starting place first." */
  error?: string;
  /** A quiet line under the control, such as the chosen place. */
  note?: string;
  inputProps: Omit<ComponentPropsWithRef<"input">, "id" | "aria-describedby" | "aria-invalid" | "className">;
  /** A 44 px control inside the border, after the input. */
  trailing?: ReactNode;
  /** Content below the field: suggestions, related actions. */
  children?: ReactNode;
};

export default function Field({ id, label, help, helpHidden, error, note, inputProps, trailing, children }: Props) {
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [help ? helpId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className={`control${error ? " is-invalid" : ""}`}>
        <input className="control-input" id={id} aria-describedby={describedBy} aria-invalid={error ? true : undefined} {...inputProps} />
        {trailing}
      </div>
      {error && (
        <p className="field-error" id={errorId}>
          {error}
        </p>
      )}
      {help && (
        <p className={helpHidden ? "sr-only" : "field-help"} id={helpId}>
          {help}
        </p>
      )}
      {note && <p className="field-note">{note}</p>}
      {children}
    </div>
  );
}

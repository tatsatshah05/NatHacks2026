"use client";

// OP-308: a floating "Report a problem" button. It opens a native modal <dialog>
// (focus stays inside, Escape closes, focus returns to the button):
//   1. choose the problem and the place, with an optional note and photo
//   2. confirm in words ("You're reporting an icy sidewalk at ... Send it?")
//   3. the result: how long the report is live and whether the photo was checked
// Pages mount it (OP-301, OP-310) and pass getLocation for "Use the map centre".

import { FormEvent, useEffect, useId, useRef, useState } from "react";
import type { components, operations } from "@/lib/api-types";
import { getAnonymousClientId } from "@/lib/client-id";
import styles from "./report-button.module.css";

type ReportResult = components["schemas"]["ReportResult"];
type ValidationErrorBody = components["schemas"]["HTTPValidationError"];
type ReportForm =
  operations["post_report_api_reports_post"]["requestBody"]["content"]["multipart/form-data"];
export type ReportType = ReportForm["type"];

export type ReportLocation = {
  lat: number;
  lng: number;
  /** A place name for the confirm step, such as "104 Avenue and 101 Street". */
  label?: string;
};

type Props = {
  /** The map centre (or a point the page chose). Enables "Use the map centre". */
  getLocation?: () => ReportLocation | null | Promise<ReportLocation | null>;
  /** The API base URL. Defaults to the app's public API URL. */
  apiBase?: string;
  /** The device's anonymous id. Defaults to the shared one in localStorage. */
  clientId?: string;
  /** Called after a report is stored, so the page can refresh its map. */
  onReported?: (result: ReportResult) => void;
};

type Step = "edit" | "confirm" | "done";
type LocationSource = "device" | "map";
type Errors = Partial<Record<"type" | "location" | "photo" | "send", string>>;

const TYPES: Array<{ id: ReportType; label: string; icon: string; phrase: string }> = [
  { id: "snow", label: "Snow on the path", icon: "❄", phrase: "snow on the sidewalk" },
  { id: "ice", label: "Ice on the path", icon: "◇", phrase: "an icy sidewalk" },
  { id: "blocked", label: "Path blocked", icon: "!", phrase: "a blocked sidewalk" }
];

const MAX_NOTE_CHARS = 120;
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
const MAX_PHOTO_EDGE_PX = 1600;
// The API's box (cloud/app/reports.py): Edmonton and a little of its neighbours.
const EDMONTON = { minLat: 53.3957, maxLat: 53.716, minLng: -113.7136, maxLng: -113.2714 };
const STEP_TITLES: Record<Step, string> = {
  edit: "Report a problem",
  confirm: "Check your report",
  done: "Report sent"
};

function defaultApiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "";
}

function inEdmonton({ lat, lng }: ReportLocation): boolean {
  return lat >= EDMONTON.minLat && lat <= EDMONTON.maxLat && lng >= EDMONTON.minLng && lng <= EDMONTON.maxLng;
}

function placeText(location: ReportLocation, source: LocationSource): string {
  if (location.label) return location.label;
  const where = source === "device" ? "your location" : "the map centre";
  return `${where} (${location.lat.toFixed(5)}, ${location.lng.toFixed(5)})`;
}

function hoursText(hours: number): string {
  return hours === 1 ? "hour" : `${hours} hours`;
}

function devicePosition(): Promise<ReportLocation> {
  return new Promise((resolve, reject) => {
    if (!("geolocation" in navigator)) {
      reject(new Error("unsupported"));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({ lat: position.coords.latitude, lng: position.coords.longitude }),
      reject,
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  });
}

function locationErrorText(error: unknown, hasMap: boolean): string {
  const fallback = hasMap ? " Use the map centre instead." : "";
  // GeolocationPositionError codes; older browsers have no global class to test against.
  const code = error && typeof error === "object" && "code" in error ? (error as { code: unknown }).code : null;
  if (code === 1) return `Location access is off for this site. Allow it in your browser settings.${fallback}`;
  if (code === 3) return `Finding your location took too long. Try again.${fallback}`;
  return `Your location is not available on this device.${fallback}`;
}

/** Re-encode the photo as a JPEG at most 1600 px wide. This also drops its metadata
 * (such as GPS). Falls back to the original file when the browser cannot decode it. */
async function preparePhoto(file: File): Promise<Blob> {
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, MAX_PHOTO_EDGE_PX / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (context) {
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close();
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
      if (blob && blob.size <= MAX_PHOTO_BYTES) return blob;
    }
  } catch {
    // Decoding failed: try the original file below.
  }
  if ((file.type === "image/jpeg" || file.type === "image/png") && file.size <= MAX_PHOTO_BYTES) return file;
  throw new Error("photo not usable");
}

async function errorText(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && "detail" in data) {
      const { detail } = data as { detail: unknown };
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        const messages = ((data as ValidationErrorBody).detail ?? []).map((item) =>
          item.msg.replace(/^Value error, /, "")
        );
        if (messages.length) return `The report was not accepted: ${messages.join("; ")}.`;
      }
    }
  } catch {
    // Not JSON: use the generic message.
  }
  return `The report could not be sent (error ${response.status}). Please try again.`;
}

export default function ReportButton({ getLocation, apiBase, clientId, onReported }: Props) {
  const ids = useId();
  const id = (name: string) => `${ids}-${name}`;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const photoRef = useRef<HTMLInputElement>(null);

  const [step, setStep] = useState<Step>("edit");
  const [type, setType] = useState<ReportType | null>(null);
  const [location, setLocation] = useState<ReportLocation | null>(null);
  const [locationSource, setLocationSource] = useState<LocationSource>("device");
  const [locating, setLocating] = useState(false);
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [errors, setErrors] = useState<Errors>({});
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<ReportResult | null>(null);

  const chosen = TYPES.find((option) => option.id === type) ?? null;

  // Each step starts with focus on its heading, so a screen reader announces it.
  useEffect(() => {
    if (dialogRef.current?.open) headingRef.current?.focus();
  }, [step]);

  function reset() {
    setStep("edit");
    setType(null);
    setLocation(null);
    setNote("");
    setPhoto(null);
    setErrors({});
    setResult(null);
    if (photoRef.current) photoRef.current.value = "";
  }

  function open() {
    if (step === "done") reset();
    dialogRef.current?.showModal();
    headingRef.current?.focus();
  }

  function close() {
    dialogRef.current?.close();
  }

  // Runs for every way the dialog closes: Close, Cancel, Done and Escape.
  function handleClosed() {
    if (step === "done") reset();
    triggerRef.current?.focus();
  }

  async function findLocation(source: LocationSource) {
    setLocating(true);
    setErrors((current) => ({ ...current, location: undefined }));
    try {
      const found = source === "device" ? await devicePosition() : await getLocation?.();
      if (!found) throw new Error("no map centre");
      if (!inEdmonton(found)) {
        setLocation(null);
        setErrors((current) => ({
          ...current,
          location: "That place is outside Edmonton. OpenPath takes reports inside the city only."
        }));
        return;
      }
      setLocation(found);
      setLocationSource(source);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        location:
          source === "device"
            ? locationErrorText(error, Boolean(getLocation))
            : "The map centre is not available. Move the map, or use your location."
      }));
    } finally {
      setLocating(false);
    }
  }

  function choosePhoto(file: File | null) {
    if (file && !file.type.startsWith("image/")) {
      setPhoto(null);
      setErrors((current) => ({ ...current, photo: "Choose a photo (an image file)." }));
      if (photoRef.current) photoRef.current.value = "";
      return;
    }
    setPhoto(file);
    setErrors((current) => ({ ...current, photo: undefined }));
  }

  function removePhoto() {
    choosePhoto(null);
    if (photoRef.current) {
      photoRef.current.value = "";
      photoRef.current.focus();
    }
  }

  function toConfirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next: Errors = {};
    if (!type) next.type = "Choose what the problem is.";
    if (!location) next.location = "Set where the problem is.";
    if (errors.photo) next.photo = errors.photo;
    setErrors(next);
    if (next.type) document.getElementById(id("type-snow"))?.focus();
    else if (next.location) document.getElementById(id("use-device"))?.focus();
    else if (next.photo) photoRef.current?.focus();
    else setStep("confirm");
  }

  async function send() {
    if (sending || !type || !location) return;
    setSending(true);
    setErrors({});
    try {
      const body = new FormData();
      body.set("client_id", clientId ?? getAnonymousClientId());
      body.set("type", type);
      body.set("lat", String(location.lat));
      body.set("lng", String(location.lng));
      if (note.trim()) body.set("note", note.trim());
      if (photo) {
        try {
          body.set("photo", await preparePhoto(photo), "photo.jpg");
        } catch {
          setErrors({ send: "This photo cannot be used. Go back to remove it or choose another, then send again." });
          return;
        }
      }
      const response = await fetch(`${apiBase ?? defaultApiBase()}/api/reports`, { method: "POST", body });
      if (!response.ok) {
        setErrors({ send: await errorText(response) });
        return;
      }
      const stored = (await response.json()) as ReportResult;
      setResult(stored);
      setStep("done");
      onReported?.(stored);
    } catch {
      setErrors({ send: "The report could not be sent. Check your connection and try again." });
    } finally {
      setSending(false);
    }
  }

  const describedBy = (...names: Array<string | false>) =>
    names.filter((name): name is string => Boolean(name)).map(id).join(" ") || undefined;

  return (
    <>
      <button ref={triggerRef} type="button" className={styles.trigger} aria-haspopup="dialog" onClick={open}>
        <span className={styles.triggerIcon} aria-hidden="true">!</span>
        <span>Report a problem</span>
      </button>

      <dialog ref={dialogRef} className={styles.dialog} aria-labelledby={id("title")} onClose={handleClosed}>
        <div className={styles.header}>
          <h2 id={id("title")} ref={headingRef} tabIndex={-1}>
            {STEP_TITLES[step]}
          </h2>
          <button type="button" className={styles.secondary} onClick={close}>
            Close
          </button>
        </div>

        {step === "edit" && (
          <form className={styles.body} onSubmit={toConfirm} noValidate>
            <fieldset className={styles.fieldset}>
              <legend>What is the problem?</legend>
              <div className={styles.types}>
                {TYPES.map((option) => (
                  <label key={option.id} className={styles.typeOption} data-type={option.id}>
                    <input
                      id={id(`type-${option.id}`)}
                      type="radio"
                      name="report-type"
                      value={option.id}
                      checked={type === option.id}
                      onChange={() => {
                        setType(option.id);
                        setErrors((current) => ({ ...current, type: undefined }));
                      }}
                      aria-describedby={describedBy(Boolean(errors.type) && "type-error")}
                    />
                    <span className={styles.typeIcon} aria-hidden="true">{option.icon}</span>
                    <span className={styles.typeLabel}>{option.label}</span>
                  </label>
                ))}
              </div>
              {errors.type && (
                <p id={id("type-error")} className={styles.error}>
                  {errors.type}
                </p>
              )}
            </fieldset>

            <fieldset className={styles.fieldset}>
              <legend>Where is it?</legend>
              <div className={styles.locationButtons}>
                <button
                  id={id("use-device")}
                  type="button"
                  className={styles.secondary}
                  onClick={() => void findLocation("device")}
                  aria-describedby={describedBy("location-status", Boolean(errors.location) && "location-error")}
                >
                  Use my location
                </button>
                {getLocation && (
                  <button
                    type="button"
                    className={styles.secondary}
                    onClick={() => void findLocation("map")}
                    aria-describedby={describedBy("location-status", Boolean(errors.location) && "location-error")}
                  >
                    Use the map centre
                  </button>
                )}
              </div>
              <p id={id("location-status")} className={styles.status} role="status">
                {locating
                  ? "Finding the place…"
                  : location
                    ? `Place set: ${placeText(location, locationSource)}.`
                    : "No place set yet."}
              </p>
              {errors.location && (
                <p id={id("location-error")} className={styles.error}>
                  {errors.location}
                </p>
              )}
            </fieldset>

            <div>
              <label className={styles.label} htmlFor={id("note")}>
                Note (optional)
              </label>
              <textarea
                id={id("note")}
                className={styles.textarea}
                maxLength={MAX_NOTE_CHARS}
                rows={2}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                aria-describedby={describedBy("note-hint")}
              />
              <p id={id("note-hint")} className={styles.hint}>
                Up to {MAX_NOTE_CHARS} characters ({note.length} used). Please leave out names and personal details.
              </p>
            </div>

            <div>
              <label className={styles.label} htmlFor={id("photo")}>
                Photo (optional)
              </label>
              <input
                ref={photoRef}
                id={id("photo")}
                className={styles.file}
                type="file"
                accept="image/*"
                capture="environment"
                onChange={(event) => choosePhoto(event.target.files?.[0] ?? null)}
                aria-invalid={errors.photo ? true : undefined}
                aria-describedby={describedBy("photo-hint", Boolean(errors.photo) && "photo-error")}
              />
              <p id={id("photo-hint")} className={styles.hint}>
                A photo can confirm snow or ice. It is checked and then deleted: OpenPath never stores photos.
              </p>
              {photo && (
                <div className={styles.photoChosen}>
                  <span>Photo chosen: {photo.name || "camera photo"}</span>
                  <button type="button" className={styles.secondary} onClick={removePhoto}>
                    Remove photo
                  </button>
                </div>
              )}
              {errors.photo && (
                <p id={id("photo-error")} className={styles.error}>
                  {errors.photo}
                </p>
              )}
            </div>

            <div className={styles.actions}>
              <button type="submit" className={styles.primary}>
                Next: check and send
              </button>
              <button type="button" className={styles.secondary} onClick={close}>
                Cancel
              </button>
            </div>
          </form>
        )}

        {step === "confirm" && chosen && location && (
          <div className={styles.body}>
            <p className={styles.question}>
              You&apos;re reporting {chosen.phrase} at {placeText(location, locationSource)}. Send it?
            </p>
            <dl className={styles.summary}>
              <dt>Note</dt>
              <dd>{note.trim() || "No note"}</dd>
              <dt>Photo</dt>
              <dd>{photo ? "One photo, checked and then deleted" : "No photo"}</dd>
            </dl>
            {errors.send && (
              <p id={id("send-error")} className={styles.error} role="alert">
                {errors.send}
              </p>
            )}
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.primary}
                onClick={() => void send()}
                aria-disabled={sending ? true : undefined}
                aria-describedby={describedBy(Boolean(errors.send) && "send-error")}
              >
                {sending ? "Sending…" : "Send report"}
              </button>
              <button type="button" className={styles.secondary} onClick={() => setStep("edit")} disabled={sending}>
                Back
              </button>
            </div>
          </div>
        )}

        {step === "done" && result && (
          <div className={styles.body}>
            <p className={styles.success}>Thanks. Your report is live for the next {hoursText(result.expires_in_hours)}.</p>
            <p>{result.photo.note}</p>
            <p className={styles.hint}>
              Shown to others as &ldquo;{result.title}&rdquo; with {result.confidence} confidence.
            </p>
            <div className={styles.actions}>
              <button type="button" className={styles.primary} onClick={close}>
                Done
              </button>
              <button type="button" className={styles.secondary} onClick={reset}>
                Report another problem
              </button>
            </div>
          </div>
        )}
      </dialog>
    </>
  );
}

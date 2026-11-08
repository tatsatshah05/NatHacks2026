"use client";

// What went wrong with a route request, and what to do about it (OP-301). Used by the
// planner and the route report so both say the same thing in the same words.

import Button from "./ui/button";
import { ApiError } from "@/lib/api";
import type { RouteResponse, TravelProfile } from "@/lib/planner-types";
import { profilePhrase } from "@/lib/planner-types";
import styles from "./route-problem.module.css";

type FailureProps = {
  error: unknown;
  onRetry: () => void;
  /** Offer the labelled demo routes when the service cannot be reached. */
  onDemo?: () => void;
  headingLevel?: 2 | 3;
};

// `canRetry`: the same request could work later. A refused request needs new places.
type Wording = { title: string; body: string; canDemo: boolean; canRetry: boolean };

export function failureWording(error: unknown): Wording {
  if (error instanceof ApiError) {
    switch (error.failure) {
      case "offline":
        return {
          title: "This device is offline.",
          body: "Routes need a connection. Connect to the internet, then try again.",
          canDemo: true,
          canRetry: true
        };
      case "rejected":
        return {
          title: "These places cannot be routed.",
          body: `${error.message.endsWith(".") ? error.message : `${error.message}.`} Change the place, then find routes again.`,
          canDemo: false,
          canRetry: false
        };
      case "unavailable":
        if (error.status === 503) {
          return {
            title: "The route service is not ready.",
            body: "It may be starting up. Try again in a minute.",
            canDemo: true,
            canRetry: true
          };
        }
        break;
      default:
        break;
    }
  }
  return {
    title: "The route service did not answer.",
    body: "Check your connection and try again.",
    canDemo: true,
    canRetry: true
  };
}

/** A failed request: what failed, what to do, Try again, and the labelled demo. */
export function RouteFailure({ error, onRetry, onDemo, headingLevel = 2 }: FailureProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const wording = failureWording(error);
  return (
    <div className={styles.problem} role="alert">
      <Heading className={styles.title}>{wording.title}</Heading>
      <p>{wording.body}</p>
      {wording.canRetry && (
        <div className={styles.actions}>
          <Button onClick={onRetry}>Try again</Button>
          {onDemo && wording.canDemo && (
            <Button variant="secondary" onClick={onDemo}>
              Show demo routes instead
            </Button>
          )}
        </div>
      )}
      {onDemo && wording.canDemo && (
        <p className={styles.note}>Demo routes are samples for trying the app. They are not real routes or current conditions.</p>
      )}
    </div>
  );
}

// The API's own sentences for these two cases; the page says them in its own words.
const NO_ROUTE = /^No route is possible for the .* profile between these points\.$/;
const SAME_PLACE = "The start and end are at the same place.";

/** No route for this profile: say so plainly, keep any other notes from the API. */
export function NoRoute({ response, profile }: { response: RouteResponse; profile: TravelProfile }) {
  const same = response.warnings.includes(SAME_PLACE);
  const others = response.warnings.filter((warning) => warning !== SAME_PLACE && !NO_ROUTE.test(warning));
  const stepFree = profile === "wheelchair" || profile === "walker_cane";
  return (
    <div className={styles.problem} role="alert">
      <h2 className={styles.title}>No route found.</h2>
      {same ? (
        <p>The start and destination are the same place. Choose a different destination.</p>
      ) : (
        <>
          <p>
            No {stepFree ? "step-free " : ""}route was found {profilePhrase(profile)} between these places with the
            conditions known now.
          </p>
          <p>Try a destination nearby, or another way of getting around.</p>
        </>
      )}
      {others.length > 0 && (
        <ul className="warnings" aria-label="Notes from the route service">
          {others.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

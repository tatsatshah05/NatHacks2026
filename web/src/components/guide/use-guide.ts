"use client";

// OP-418 and OP-419: the trip's state, timers and browser APIs, kept out of the panel so
// the panel is only markup. One hook per route report.
//
// The trip (OP-419) runs with the camera off:
// - Start speaks the current step and watches the GPS; the fixes drive
//   lib/guide-route.ts, which says the next step within 25 m of a turn and "leaving
//   the route" after two fixes off it; Repeat step, Next step and the step list move
//   the steps by hand;
// - nothing is sent to the API while PIP is off;
// - Stop clears the GPS watch, the camera if it is on, and the speech.
//
// PIP, the camera guide (OP-418), is turned on inside the trip:
// - a frame is drawn, encoded and sent; the next one is scheduled FRAME_INTERVAL_MS
//   after the previous request completed (not after it started), so frames never
//   overlap and the real rate stays under the API's 30 a minute per device;
// - the sentence that comes back is spoken at once on PIP's own speech channel
//   (lib/announce.ts `speakAside`: it replaces PIP's older unfinished sentence and never
//   cuts a step sentence), vibrated per hazard per the settings, and listed as PIP's;
// - a 503, a denied camera or a network drop is said once, then the call is retried
//   after RETRY_AFTER_MS without a word; a 429 is quiet: PIP waits for Retry-After (or
//   RATE_LIMIT_WAIT_MS when the header is not readable) and carries on; the route steps
//   go on throughout;
// - Stop PIP releases the camera and clears its timer; the trip and its step
//   narration continue.
// A frame is a Blob for one request and is never kept.

import { RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getSpeakPreference, getVibratePreference, speak, speakAside, stopSpeaking, vibrate } from "@/lib/announce";
import { getAnonymousClientId } from "@/lib/client-id";
import { FRAME_TIMEOUT_MS, GuideError, GuideFrameResponse, postGuideFrame } from "@/lib/guide-api";
import { captureFrame, openRearCamera, stopStream } from "@/lib/guide-camera";
import { appendTranscript, buildGuideContext, vibrationFor } from "@/lib/guide-context";
import {
  FollowReading,
  FollowState,
  RouteFollower,
  arrivalSentence,
  initialFollowState,
  routePositions,
  stepSentence
} from "@/lib/guide-route";
import type { RouteOut, TravelProfile } from "@/lib/planner-types";

// 2 s between the end of one request and the start of the next (lead feedback,
// November 9: 2.5 s felt slow). Measured from completion, the rate is always under the
// API's limit of 30 frames a minute per device.
export const FRAME_INTERVAL_MS = 2000;
export const RETRY_AFTER_MS = 30_000;
/** After a 429 without a readable Retry-After. */
export const RATE_LIMIT_WAIT_MS = 5000;

// The spoken sentences stay as OP-418 wrote them: PIP never names itself in speech.
export const CAMERA_DENIED_SENTENCE = "Camera not available. I'll read the route steps.";
export const GUIDE_UNAVAILABLE_SENTENCE = "Camera guidance is not available right now. The route steps still work.";

export type TripPhase = "idle" | "running";
/** PIP: closed (the PIP button), the privacy notice, or on. */
export type PipPhase = "closed" | "notice" | "guiding";
export type CameraState = "off" | "starting" | "on" | "denied" | "unavailable";
export type LocationState = "unknown" | "following" | "off";
export type TranscriptLine = { id: number; text: string; source: "step" | "camera" | "note" };

type Options = { route: RouteOut; profile: TravelProfile; destination: string };

type Position = { lat: number; lng: number; heading_deg: number | null };

export type Guide = {
  trip: TripPhase;
  pip: PipPhase;
  camera: CameraState;
  location: LocationState;
  /** The last five sentences, oldest first. */
  lines: TranscriptLine[];
  stepIndex: number;
  /** The end of the route was reached (by GPS or by Next step). */
  arrived: boolean;
  videoRef: RefObject<HTMLVideoElement | null>;
  /** Begin the trip: speak the first step and follow the GPS. The camera stays off. */
  startTrip: () => void;
  /** End the trip, and PIP with it. */
  stopTrip: () => void;
  /** Press PIP: the privacy notice the first time, after that straight to the camera. Returns the phase reached. */
  openPip: () => PipPhase;
  /** Back from the notice without turning the camera on. */
  closePip: () => void;
  /** Accept the notice: open the rear camera and start sending frames. */
  startPip: () => void;
  /** Turn the camera off; the trip continues. */
  stopPip: () => void;
  repeatStep: () => void;
  nextStep: () => void;
  sayStep: (index: number) => void;
};

export function useGuide({ route, profile, destination }: Options): Guide {
  const [trip, setTrip] = useState<TripPhase>("idle");
  const [pip, setPip] = useState<PipPhase>("closed");
  const [camera, setCamera] = useState<CameraState>("off");
  const [location, setLocation] = useState<LocationState>("unknown");
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [stepIndex, setStepIndex] = useState(0);
  const [arrived, setArrived] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const tickRef = useRef<number | null>(null);
  const pendingRef = useRef<AbortController | null>(null);
  const watchRef = useRef<number | null>(null);
  // The trip is running (narration and GPS), and the camera is on (frames).
  const activeRef = useRef(false);
  const cameraRef = useRef(false);
  // Each camera start has a generation, so a stream that arrives after Stop PIP, or
  // after a second start, is released instead of being wired in.
  const generationRef = useRef(0);
  const noticeSeenRef = useRef(false);
  const followRef = useRef<FollowState>(initialFollowState());
  const positionRef = useRef<Position | null>(null);
  const readingRef = useRef<FollowReading | null>(null);
  const previousRef = useRef<string | null>(null);
  const hazardKeyRef = useRef("");
  const unavailableSaidRef = useRef(false);
  const lineIdRef = useRef(0);

  const steps = route.steps;
  const follower = useMemo(
    () => new RouteFollower(routePositions(route.geometry), steps, destination),
    [route.geometry, steps, destination]
  );
  // The tick and the GPS callback read the latest route through refs, so they never
  // have to be re-created (and the interval never restarted) while the trip runs.
  const followerRef = useRef(follower);
  const routeRef = useRef(route);
  const profileRef = useRef(profile);
  useEffect(() => {
    followerRef.current = follower;
    routeRef.current = route;
    profileRef.current = profile;
  }, [follower, route, profile]);

  /**
   * List a sentence and, per the settings, speak it and vibrate the pattern. A camera
   * sentence goes on PIP's channel, so it never cuts a step sentence short.
   */
  const say = useCallback((text: string, source: TranscriptLine["source"], pattern: number[] | null = null) => {
    if (!text) return;
    lineIdRef.current += 1;
    const line: TranscriptLine = { id: lineIdRef.current, text, source };
    setLines((current) => appendTranscript(current, line));
    if (getSpeakPreference()) {
      if (source === "camera") speakAside(text);
      else speak(text);
    }
    if (pattern && getVibratePreference()) vibrate(pattern);
  }, []);

  /** Release the camera and clear everything that belongs to PIP alone. */
  const releaseCamera = useCallback(() => {
    cameraRef.current = false;
    generationRef.current += 1;
    if (tickRef.current !== null) {
      window.clearTimeout(tickRef.current);
      tickRef.current = null;
    }
    pendingRef.current?.abort();
    pendingRef.current = null;
    stopStream(streamRef.current);
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    unavailableSaidRef.current = false;
    previousRef.current = null;
    hazardKeyRef.current = "";
  }, []);

  /** End everything: the camera, the GPS watch and the speech. */
  const teardown = useCallback(() => {
    activeRef.current = false;
    releaseCamera();
    if (watchRef.current !== null) {
      try {
        navigator.geolocation.clearWatch(watchRef.current);
      } catch {
        // No geolocation to clear.
      }
      watchRef.current = null;
    }
    positionRef.current = null;
    readingRef.current = null;
    stopSpeaking();
  }, [releaseCamera]);

  // Leaving the page releases the camera and the GPS watch too.
  useEffect(() => teardown, [teardown]);

  const onResult = useCallback(
    (result: GuideFrameResponse) => {
      setCamera("on");
      unavailableSaidRef.current = false;
      // Vibrate when the hazards change, not on every frame that still shows the same curb.
      const key = result.hazards
        .map((hazard) => hazard.kind)
        .sort()
        .join(",");
      const pattern = key !== hazardKeyRef.current ? vibrationFor(result.hazards) : null;
      hazardKeyRef.current = key;
      if (result.say) {
        previousRef.current = result.say;
        say(result.say, "camera", pattern);
      } else if (pattern && getVibratePreference()) {
        vibrate(pattern);
      }
    },
    [say]
  );

  /** A failed frame: how long to wait before the next one. A 429 is quiet; the rest is said once. */
  const onFailure = useCallback(
    (error: unknown): number => {
      if (error instanceof GuideError && error.status === 429) {
        return error.retryAfterMs ?? RATE_LIMIT_WAIT_MS;
      }
      setCamera("unavailable");
      if (!unavailableSaidRef.current) {
        unavailableSaidRef.current = true;
        say(GUIDE_UNAVAILABLE_SENTENCE, "note");
      }
      return RETRY_AFTER_MS;
    },
    [say]
  );

  // One frame, then the next one is scheduled `delay` after this one completed. The
  // generation check drops a tick that belongs to a camera since stopped or restarted.
  const tick = useCallback(async (generation: number) => {
    const schedule = (delayMs: number) => {
      if (!cameraRef.current || generation !== generationRef.current) return;
      tickRef.current = window.setTimeout(() => void tick(generation), delayMs);
    };
    tickRef.current = null;
    if (!cameraRef.current || generation !== generationRef.current || pendingRef.current) return;
    const video = videoRef.current;
    if (!video) {
      schedule(FRAME_INTERVAL_MS);
      return;
    }
    const controller = new AbortController();
    pendingRef.current = controller; // claimed before the first await, so requests never overlap
    const timer = window.setTimeout(() => controller.abort(), FRAME_TIMEOUT_MS);
    let delay = FRAME_INTERVAL_MS;
    try {
      canvasRef.current ??= document.createElement("canvas");
      const frame = await captureFrame(video, canvasRef.current);
      if (!frame || !cameraRef.current) return;
      const current = routeRef.current;
      const context = buildGuideContext({
        clientId: getAnonymousClientId(),
        profile: profileRef.current,
        steps: current.steps,
        index: followRef.current.index,
        position: positionRef.current,
        reading: readingRef.current,
        report: current.report,
        previous: previousRef.current
      });
      const result = await postGuideFrame(frame, context, controller.signal);
      if (cameraRef.current && generation === generationRef.current) onResult(result);
    } catch (error) {
      // Stop PIP aborts the request after clearing `camera`, so only a real failure lands here.
      if (cameraRef.current && generation === generationRef.current) delay = onFailure(error);
    } finally {
      window.clearTimeout(timer);
      if (pendingRef.current === controller) pendingRef.current = null;
      schedule(delay);
    }
  }, [onFailure, onResult]);

  const onPosition = useCallback(
    (position: GeolocationPosition) => {
      if (!activeRef.current) return;
      setLocation("following");
      const { latitude, longitude, heading } = position.coords;
      const headingDeg = typeof heading === "number" && Number.isFinite(heading) ? heading : null;
      positionRef.current = { lat: latitude, lng: longitude, heading_deg: headingDeg };
      const result = followerRef.current.onFix(followRef.current, {
        position: { lat: latitude, lng: longitude },
        heading_deg: headingDeg
      });
      followRef.current = result.state;
      readingRef.current = result.reading;
      setStepIndex(result.state.index);
      setArrived(result.state.arrived);
      // One utterance: speak() replaces whatever is still being said.
      if (result.say.length) say(result.say.join(" "), "step");
    },
    [say]
  );

  const startLocation = useCallback(() => {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      setLocation("off");
      return;
    }
    try {
      watchRef.current = navigator.geolocation.watchPosition(
        onPosition,
        (error) => {
          // 1 denied, 2 unavailable: the steps are moved by hand. 3 (timeout) keeps watching.
          if (error.code === 1 || error.code === 2) setLocation("off");
        },
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 }
      );
    } catch {
      setLocation("off");
    }
  }, [onPosition]);

  const startTrip = useCallback(() => {
    if (activeRef.current) return;
    activeRef.current = true;
    followRef.current = initialFollowState(0);
    setStepIndex(0);
    setArrived(false);
    setLines([]);
    setLocation("unknown");
    setTrip("running");
    say(stepSentence(steps, 0), "step");
    startLocation();
  }, [say, startLocation, steps]);

  const stopTrip = useCallback(() => {
    teardown();
    setCamera("off");
    setPip("closed");
    setTrip("idle");
  }, [teardown]);

  const startPip = useCallback(() => {
    if (!activeRef.current) return;
    noticeSeenRef.current = true;
    releaseCamera(); // a stale stream or timer never survives a second start
    cameraRef.current = true;
    const generation = generationRef.current;
    setCamera("starting");
    setPip("guiding");
    openRearCamera()
      .then((stream) => {
        if (!cameraRef.current || generation !== generationRef.current) {
          stopStream(stream);
          return;
        }
        streamRef.current = stream;
        setCamera("on");
        // The first frame waits for the video to have data; the chain goes on from there.
        tickRef.current = window.setTimeout(() => void tick(generation), FRAME_INTERVAL_MS);
      })
      .catch(() => {
        if (!cameraRef.current || generation !== generationRef.current) return;
        setCamera("denied");
        say(CAMERA_DENIED_SENTENCE, "note");
      });
  }, [releaseCamera, say, tick]);

  const openPip = useCallback((): PipPhase => {
    if (!activeRef.current) return "closed";
    if (noticeSeenRef.current) {
      startPip();
      return "guiding";
    }
    setPip("notice");
    return "notice";
  }, [startPip]);

  const closePip = useCallback(() => setPip("closed"), []);

  // The preview: the stream goes onto the video element once both exist.
  useEffect(() => {
    const video = videoRef.current;
    const stream = streamRef.current;
    if (camera !== "on" || !video || !stream || video.srcObject === stream) return;
    video.srcObject = stream;
    video.play().catch(() => undefined);
  }, [camera]);

  // The trip's own sentence, if one is being spoken, is left to finish.
  const stopPip = useCallback(() => {
    releaseCamera();
    setCamera("off");
    setPip("closed");
  }, [releaseCamera]);

  const repeatStep = useCallback(() => {
    if (followRef.current.arrived) say(arrivalSentence(destination), "step");
    else say(stepSentence(steps, followRef.current.index), "step");
  }, [destination, say, steps]);

  const nextStep = useCallback(() => {
    const index = followRef.current.index;
    if (index + 1 < steps.length) {
      followRef.current = { ...followRef.current, index: index + 1 };
      setStepIndex(index + 1);
      say(stepSentence(steps, index + 1), "step");
    } else {
      followRef.current = { ...followRef.current, arrived: true };
      setArrived(true);
      say(arrivalSentence(destination), "step");
    }
  }, [destination, say, steps]);

  const sayStep = useCallback(
    (index: number) => {
      if (!steps[index]) return;
      followRef.current = { ...followRef.current, index, arrived: false };
      setStepIndex(index);
      setArrived(false);
      say(stepSentence(steps, index), "step");
    },
    [say, steps]
  );

  return {
    trip,
    pip,
    camera,
    location,
    lines,
    stepIndex,
    arrived,
    videoRef,
    startTrip,
    stopTrip,
    openPip,
    closePip,
    startPip,
    stopPip,
    repeatStep,
    nextStep,
    sayStep
  };
}

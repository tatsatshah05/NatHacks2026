"use client";

// OP-418: the camera guide's state, timers and browser APIs, kept out of the panel so
// the panel is only markup. One hook per route report.
//
// While guiding:
// - every FRAME_INTERVAL_MS a frame is drawn, encoded and sent (never while one is
//   pending: that tick is skipped); the sentence that comes back is spoken through
//   lib/announce.ts (the speech and vibration settings apply) and listed;
// - a 503, a 429 or a network drop is said once, then the call is retried every
//   RETRY_AFTER_MS without a word; the route steps go on throughout;
// - the current step is spoken at Start; GPS fixes drive lib/guide-route.ts, which
//   says the next step near a turn and "leaving the route" after two fixes off it;
// - Stop releases the camera, clears the timers and the GPS watch and stops speech.
// A frame is a Blob for one request and is never kept.

import { RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getSpeakPreference, getVibratePreference, speak, stopSpeaking, vibrate } from "@/lib/announce";
import { getAnonymousClientId } from "@/lib/client-id";
import { FRAME_TIMEOUT_MS, GuideFrameResponse, postGuideFrame } from "@/lib/guide-api";
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

export const FRAME_INTERVAL_MS = 2500;
export const RETRY_AFTER_MS = 30_000;

export const CAMERA_DENIED_SENTENCE = "Camera not available. I'll read the route steps.";
export const GUIDE_UNAVAILABLE_SENTENCE = "Camera guidance is not available right now. The route steps still work.";

export type GuidePhase = "closed" | "notice" | "guiding";
export type CameraState = "off" | "starting" | "on" | "denied" | "unavailable";
export type LocationState = "unknown" | "following" | "off";
export type TranscriptLine = { id: number; text: string; source: "step" | "camera" | "note" };

type Options = { route: RouteOut; profile: TravelProfile; destination: string };

type Position = { lat: number; lng: number; heading_deg: number | null };

export type Guide = {
  phase: GuidePhase;
  camera: CameraState;
  location: LocationState;
  /** The last five sentences, oldest first. */
  lines: TranscriptLine[];
  stepIndex: number;
  videoRef: RefObject<HTMLVideoElement | null>;
  /** Show the privacy notice and Start. */
  open: () => void;
  /** Back to the Guide button without starting. */
  close: () => void;
  start: () => void;
  stop: () => void;
  repeatStep: () => void;
  nextStep: () => void;
  sayStep: (index: number) => void;
};

export function useGuide({ route, profile, destination }: Options): Guide {
  const [phase, setPhase] = useState<GuidePhase>("closed");
  const [camera, setCamera] = useState<CameraState>("off");
  const [location, setLocation] = useState<LocationState>("unknown");
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [stepIndex, setStepIndex] = useState(0);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const tickRef = useRef<number | null>(null);
  const pendingRef = useRef<AbortController | null>(null);
  const pausedUntilRef = useRef(0);
  const watchRef = useRef<number | null>(null);
  const activeRef = useRef(false);
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
  // have to be re-created (and the interval never restarted) while guiding.
  const followerRef = useRef(follower);
  const routeRef = useRef(route);
  const profileRef = useRef(profile);
  useEffect(() => {
    followerRef.current = follower;
    routeRef.current = route;
    profileRef.current = profile;
  }, [follower, route, profile]);

  /** List a sentence and, per the settings, speak it and vibrate the pattern. */
  const say = useCallback((text: string, source: TranscriptLine["source"], pattern: number[] | null = null) => {
    if (!text) return;
    lineIdRef.current += 1;
    const line: TranscriptLine = { id: lineIdRef.current, text, source };
    setLines((current) => appendTranscript(current, line));
    if (getSpeakPreference()) speak(text);
    if (pattern && getVibratePreference()) vibrate(pattern);
  }, []);

  const teardown = useCallback(() => {
    activeRef.current = false;
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
    pendingRef.current?.abort();
    pendingRef.current = null;
    if (watchRef.current !== null) {
      try {
        navigator.geolocation.clearWatch(watchRef.current);
      } catch {
        // No geolocation to clear.
      }
      watchRef.current = null;
    }
    stopStream(streamRef.current);
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    pausedUntilRef.current = 0;
    unavailableSaidRef.current = false;
    previousRef.current = null;
    hazardKeyRef.current = "";
    positionRef.current = null;
    readingRef.current = null;
    stopSpeaking();
  }, []);

  // Leaving the page releases the camera too.
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

  const onFailure = useCallback(() => {
    setCamera("unavailable");
    pausedUntilRef.current = Date.now() + RETRY_AFTER_MS;
    if (!unavailableSaidRef.current) {
      unavailableSaidRef.current = true;
      say(GUIDE_UNAVAILABLE_SENTENCE, "note");
    }
  }, [say]);

  const tick = useCallback(async () => {
    if (!activeRef.current || pendingRef.current || Date.now() < pausedUntilRef.current) return;
    const video = videoRef.current;
    if (!video) return;
    const controller = new AbortController();
    pendingRef.current = controller; // claimed before the first await, so ticks never overlap
    const timer = window.setTimeout(() => controller.abort(), FRAME_TIMEOUT_MS);
    try {
      canvasRef.current ??= document.createElement("canvas");
      const frame = await captureFrame(video, canvasRef.current);
      if (!frame || !activeRef.current) return;
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
      if (activeRef.current) onResult(result);
    } catch {
      // Stop aborts the request after clearing `active`, so only a real failure lands here.
      if (activeRef.current) onFailure();
    } finally {
      window.clearTimeout(timer);
      if (pendingRef.current === controller) pendingRef.current = null;
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

  const open = useCallback(() => setPhase("notice"), []);
  const close = useCallback(() => setPhase("closed"), []);

  const start = useCallback(() => {
    activeRef.current = true;
    followRef.current = initialFollowState(0);
    setStepIndex(0);
    setLines([]);
    setLocation("unknown");
    setCamera("starting");
    setPhase("guiding");
    say(stepSentence(steps, 0), "step");
    startLocation();
    openRearCamera()
      .then((stream) => {
        if (!activeRef.current) {
          stopStream(stream);
          return;
        }
        streamRef.current = stream;
        setCamera("on");
        tickRef.current = window.setInterval(() => void tick(), FRAME_INTERVAL_MS);
      })
      .catch(() => {
        if (!activeRef.current) return;
        setCamera("denied");
        say(CAMERA_DENIED_SENTENCE, "note");
      });
  }, [say, startLocation, steps, tick]);

  // The preview: the stream goes onto the video element once both exist.
  useEffect(() => {
    const video = videoRef.current;
    const stream = streamRef.current;
    if (camera !== "on" || !video || !stream || video.srcObject === stream) return;
    video.srcObject = stream;
    video.play().catch(() => undefined);
  }, [camera]);

  const stop = useCallback(() => {
    teardown();
    setCamera("off");
    setPhase("closed");
  }, [teardown]);

  const repeatStep = useCallback(() => say(stepSentence(steps, followRef.current.index), "step"), [say, steps]);

  const nextStep = useCallback(() => {
    const index = followRef.current.index;
    if (index + 1 < steps.length) {
      followRef.current = { ...followRef.current, index: index + 1 };
      setStepIndex(index + 1);
      say(stepSentence(steps, index + 1), "step");
    } else {
      followRef.current = { ...followRef.current, arrived: true };
      say(arrivalSentence(destination), "step");
    }
  }, [destination, say, steps]);

  const sayStep = useCallback(
    (index: number) => {
      if (!steps[index]) return;
      followRef.current = { ...followRef.current, index, arrived: false };
      setStepIndex(index);
      say(stepSentence(steps, index), "step");
    },
    [say, steps]
  );

  return { phase, camera, location, lines, stepIndex, videoRef, open, close, start, stop, repeatStep, nextStep, sayStep };
}

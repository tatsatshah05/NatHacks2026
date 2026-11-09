// OP-418: the rear camera and one JPEG frame from it. Browser only. A frame is a Blob
// that goes straight to lib/guide-api.ts and is then forgotten: nothing here keeps,
// stores or logs it. Every browser API can be missing or throw, so each call is
// wrapped and the guide falls back to the route steps.

import { frameSize } from "./guide-context";

export const FRAME_WIDTH_PX = 640;
export const FRAME_JPEG_QUALITY = 0.7;

export function canUseCamera(): boolean {
  try {
    return (
      typeof navigator !== "undefined" &&
      Boolean(navigator.mediaDevices) &&
      typeof navigator.mediaDevices.getUserMedia === "function"
    );
  } catch {
    return false;
  }
}

/** The rear camera. Rejects when it is denied, missing, or not allowed on this page. */
export function openRearCamera(): Promise<MediaStream> {
  if (!canUseCamera()) return Promise.reject(new Error("camera unsupported"));
  try {
    return navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
  } catch (error) {
    return Promise.reject(error);
  }
}

/** Release the camera: every track stopped, so the browser's camera light goes off. */
export function stopStream(stream: MediaStream | null): void {
  try {
    stream?.getTracks().forEach((track) => track.stop());
  } catch {
    // Already stopped.
  }
}

/**
 * Draw the video's current frame at 640 px wide and encode it as a JPEG (quality
 * 0.7). Null while the video has no frame yet, or when the canvas cannot encode.
 */
export function captureFrame(video: HTMLVideoElement, canvas: HTMLCanvasElement): Promise<Blob | null> {
  const { width, height } = frameSize(video.videoWidth, video.videoHeight, FRAME_WIDTH_PX);
  if (!width || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return Promise.resolve(null);
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return Promise.resolve(null);
  try {
    context.drawImage(video, 0, 0, width, height);
  } catch {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob), "image/jpeg", FRAME_JPEG_QUALITY);
    } catch {
      resolve(null);
    }
  });
}

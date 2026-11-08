// Where each road camera in the Edmonton area is, in 511 Alberta's own words.
//
// From the official camera metadata OP-204 recorded on November 8, 2026 at 04:19 UTC
// (vision/tests/fixtures/alberta511_cameras_recorded.json, field "Location"), for the
// 19 cameras inside the approximate Edmonton bounding box. GET /api/cameras carries
// only the camera and view ids, and 511 Alberta's camera list is searched by place,
// so the dashboard names each camera with this table. A camera that is not here is
// shown by its id. Refresh it when the worker's camera list changes.
const PLACES: Readonly<Record<number, string>> = {
  55: "Highway 216 & Highway 15 Interchange Near AHD",
  56: "Anthony Henday Drive at North Saskatchewan River (east)",
  57: "Hwy 216 at Hwy 15",
  58: "Hwy 216: Anthony Henday Drive and Highway 16 Interchange",
  59: "Hwy 216 & Hwy 16A Interchange (West)",
  60: "Hwy 216 & Hwy 16A Interchange (East)",
  61: "Hwy 216 & Whitemud Drive Interchange",
  62: "Hwy 216 & Hwy 16 Interchange (West)",
  110: "Hwy 16 at Rge Rd 231 / Clover Bar Rd",
  141: "Hwy 216: Anthony Henday Drive at North Sask. River Crossing SW",
  142: "Hwy 216 & Hwy 16 Interchange (West)",
  293: "Hwy 216: Anthony Henday Drive and 91 Street Overpass",
  294: "Hwy 2 South of Hwy 216",
  295: "Hwy 216: Anthony Henday Drive and 91 Street Overpass",
  296: "Hwy 216 W of Hwy 2",
  297: "Hwy 216: Anthony Henday Drive and 111 Street Overpass",
  298: "Hwy 216: Anthony Henday Drive and Whitemud Drive West Interchange",
  299: "Hwy 216: Anthony Henday Drive and Stony Plain Road West Interchange",
  300: "Hwy 216: Anthony Henday Drive and Yellowhead Trail West Interchange",
};

export function cameraPlace(cameraId: number): string | null {
  return PLACES[cameraId] ?? null;
}

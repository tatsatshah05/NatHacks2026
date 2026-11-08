# Test fixtures

- `alberta511_cameras_sample.json` is **hand-written sample data**, not a real 511 Alberta response. Its field names follow the 511 Alberta "GET Cameras" documentation; the IDs, view URLs and camera names are made up. It exists so tests run before the API key arrives.
- Once `ALBERTA_511_KEY` is available, record a real response (camera metadata only, no images) and add tests against it:

  ```
  python -m vision.cameras --record vision/tests/fixtures/alberta511_cameras_recorded.json
  ```

Tests never call the network.

- `alberta511_cameras_recorded.json` is official metadata recorded by Kaleb on
  November 8 at 04:19:13 UTC: 392 cameras, 19 inside the approximate Edmonton
  bbox and 47 enabled views there. Its manifest records provenance, LF SHA-256,
  counts and a one-view JPEG check. Only documented public metadata fields are
  included; request credentials/query strings and camera images are absent.
  The original hand-written sample remains useful for small edge-case tests.

- `worker_replay.json` (OP-204) is explicitly synthetic classifier output, not
  a recording or evidence of model accuracy. `python -m vision.worker` replays
  it without HTTP, image storage, ONNX loading or API posting. Tests generate
  their tiny model and image pixels in memory instead of storing image fixtures.

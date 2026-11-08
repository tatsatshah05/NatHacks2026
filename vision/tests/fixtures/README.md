# Test fixtures

- `alberta511_cameras_sample.json` is **hand-written sample data**, not a real 511 Alberta response. Its field names follow the 511 Alberta "GET Cameras" documentation; the IDs, view URLs and camera names are made up. It exists so tests run before the API key arrives.
- Once `ALBERTA_511_KEY` is available, record a real response (camera metadata only, no images) and add tests against it:

  ```
  python -m vision.cameras --record vision/tests/fixtures/alberta511_cameras_recorded.json
  ```

Tests never call the network.

- `worker_replay.json` (OP-204) is explicitly synthetic classifier output, not
  a recording or evidence of model accuracy. `python -m vision.worker` replays
  it without HTTP, image storage, ONNX loading or API posting. Tests generate
  their tiny model and image pixels in memory instead of storing image fixtures.

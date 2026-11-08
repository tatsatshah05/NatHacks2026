"""OP-204/205 integration check: synthetic frames through the real ingest/router.

Run from a checkout containing OP-204, OP-205, OP-206 and OP-213. Use a tiny
generated ONNX graph, fake 511 transport and in-memory SQLite. No camera image,
real credential, external request or trained-model accuracy claim is involved.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


def run_check() -> dict[str, object]:
    # Keep imports here so an incomplete dependency checkout fails with guidance.
    import httpx
    import numpy as np
    import onnx
    from fastapi.testclient import TestClient
    from PIL import Image

    from cloud.app import matching
    from cloud.app.camera_ice_routing import CameraIceHazardProvider
    from cloud.app.main import create_app
    from cloud.app.network import (
        EARTH_RADIUS_M,
        RawEdge,
        RawNode,
        WalkNetwork,
        export_network,
    )
    from cloud.app.profiles import load_routing_config
    from cloud.app.routing import Router
    from cloud.app.weather_service import WeatherService
    from vision import inference
    from vision import worker as camera_worker
    from vision.cameras import CameraAdapter, RateLimiter
    from vision.tools.label import LABELS

    lat0, lng0 = 53.544, -113.49

    def position(x_m: float, y_m: float) -> tuple[float, float]:
        return (
            lng0 + math.degrees(x_m / EARTH_RADIUS_M / math.cos(math.radians(lat0))),
            lat0 + math.degrees(y_m / EARTH_RADIUS_M),
        )

    points = [
        position(x, y) for x, y in [(-100, 0), (-40, 0), (40, 0), (100, 0), (0, 60)]
    ]
    nodes = [RawNode(i + 1, lat, lng) for i, (lng, lat) in enumerate(points)]
    edges = [
        RawEdge(u, v, length, highway="footway", footway=kind, osmid=100 + i)
        for i, (u, v, length, kind) in enumerate(
            [
                (1, 2, 60, "sidewalk"),
                (2, 3, 80, "crossing"),
                (3, 4, 60, "sidewalk"),
                (2, 5, 80, "sidewalk"),
                (5, 3, 80, "sidewalk"),
            ]
        )
    ]
    network = WalkNetwork.from_data(
        export_network(nodes, edges, source="synthetic camera pipeline check")
    )

    logits = onnx.helper.make_tensor(
        "value", onnx.TensorProto.FLOAT, [1, 6], [0, 20, 0, 0, 0, 0]
    )
    graph = onnx.helper.make_graph(
        [onnx.helper.make_node("Constant", [], ["logits"], value=logits)],
        "synthetic-wet",
        [
            onnx.helper.make_tensor_value_info(
                "image", onnx.TensorProto.FLOAT, [1, 3, 24, 32]
            )
        ],
        [onnx.helper.make_tensor_value_info("logits", onnx.TensorProto.FLOAT, [1, 6])],
    )
    model = onnx.helper.make_model(
        graph, opset_imports=[onnx.helper.make_opsetid("", 17)], ir_version=9
    ).SerializeToString()
    classifier = inference.OnnxClassifier.load(
        model,
        inference.ModelSpec(
            sha256=hashlib.sha256(model).hexdigest(),
            labels=LABELS,
            input_name="image",
            output_name="logits",
            width=32,
            height=24,
            color="RGB",
            layout="NCHW",
            resize="stretch_bilinear",
            pixel_scale=1 / 255,
            mean=(0, 0, 0),
            std=(1, 1, 1),
            output="logits",
            min_score=0.7,
        ),
    )
    state = {
        "offline": False,
        "weather_failed": False,
        "warm": False,
        "requests": 0,
        "frame": 0,
    }
    metadata = [
        {
            "Id": 9001,
            "Latitude": lat0,
            "Longitude": lng0,
            "Views": [
                {
                    "Id": 1,
                    "Url": "https://511.alberta.ca/map/Cctv/9001",
                    "Status": "Enabled",
                }
            ],
        }
    ]

    def transport(request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        if request.url.path == "/api/v2/get/cameras":
            return httpx.Response(200, json=metadata)
        if state["offline"]:
            return httpx.Response(503)
        state["frame"] += 1
        pixels = np.random.default_rng(state["frame"]).integers(
            20, 240, (80, 100, 3), dtype=np.uint8
        )
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="PNG")
        return httpx.Response(
            200, content=buffer.getvalue(), headers={"content-type": "image/png"}
        )

    async def weather_readings(now: datetime) -> dict[str, object]:
        if state["weather_failed"]:
            raise ValueError("synthetic weather outage")
        temperature = 5 if state["warm"] else -1
        return {
            "air_c": temperature,
            "ground_c": temperature,
            "dew_c": temperature - 1,
            "precipitation_last_6h_mm": 1,
            "max_air_last_12h_c": 1,
            "observed_at": now,
        }

    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite://",
                "SCHEDULER": "off",
                "DATA_MODE": "live",
                "WORKER_KEY": "synthetic-check-only",
                "ALBERTA_511_KEY": "placeholder",
            },
        ),
        TestClient(create_app(), base_url="http://localhost") as api,
        httpx.Client(transport=httpx.MockTransport(transport)) as camera_http,
    ):
        service = WeatherService(weather_readings)
        assert asyncio.run(service.refresh()), (
            "Synthetic freezing weather did not refresh"
        )
        base = matching.HazardProvider(api.app.state.store, network)
        provider = CameraIceHazardProvider(
            network, api.app.state.camera_store.latest, weather=service, base=base
        )
        api.app.state.router = Router(network, load_routing_config())
        api.app.state.hazard_provider = provider
        start, end = points[0], points[3]

        def route(profile: str) -> dict[str, object]:
            response = api.get(
                "/api/route",
                params={
                    "from": f"{start[1]},{start[0]}",
                    "to": f"{end[1]},{end[0]}",
                    "profile": profile,
                },
            )
            assert response.status_code == 200, "Route API rejected synthetic route"
            return response.json()["routes"][0]

        assert route("walker_cane")["distance_m"] == 200
        limiter = RateLimiter()
        worker = camera_worker.CameraWorker(
            CameraAdapter("placeholder", client=camera_http, limiter=limiter),
            camera_worker.FrameFetcher(camera_http, limiter),
            classifier,
            camera_worker.HttpPublisher(
                api, "http://localhost", "synthetic-check-only"
            ),
        )
        assert not worker.cycle(), "A single frame incorrectly confirmed a condition"
        assert worker.cycle()[0].label == "wet" and worker.publish_succeeded == 1
        camera_response = api.get("/api/cameras")
        assert camera_response.status_code == 200
        assert camera_response.json()["features"][0]["properties"]["label"] == "wet"
        routed = {
            profile: route(profile)
            for profile in ["walker_cane", "wheelchair", "low_vision", "standard"]
        }
        for profile in ["walker_cane", "wheelchair", "low_vision"]:
            assert routed[profile]["distance_m"] == 280
            assert any(
                h["effect"] == "ice_risk" for h in routed[profile]["hazards_avoided"]
            )
        assert routed["standard"]["distance_m"] == 200
        assert any(
            h["effect"] == "ice_risk" for h in routed["standard"]["hazards_on_route"]
        )
        assert set(provider(datetime.now(UTC))) == {1}
        saved = provider.latest.risks[0]

        state["weather_failed"] = True
        assert not asyncio.run(service.refresh())
        assert route("walker_cane")["distance_m"] == 280
        assert provider.latest.risks[0] == saved
        state["offline"] = True
        worker.cycle()
        assert worker.cycle()[0].label == "unknown"
        assert route("walker_cane")["distance_m"] == 280
        assert provider.latest.risks[0] == saved

        state.update(offline=False, weather_failed=False, warm=True)
        assert asyncio.run(service.refresh())
        worker.cycle()
        assert worker.cycle()[0].label == "wet"
        assert route("walker_cane")["distance_m"] == 200
        assert not provider.latest.risks
        # API auth is real, but every credential and reading in this check is synthetic.
        assert (
            api.post(
                "/api/ingest/camera",
                json=worker.last_good["511:9001:1"].model_dump(mode="json"),
                headers={"X-Worker-Key": "wrong-placeholder"},
            ).status_code
            == 401
        )
    return {
        "mode": "synthetic-integration",
        "trained_model": False,
        "external_requests": 0,
        "images_saved": 0,
        "simulated_511_requests": state["requests"],
        "baseline_distance_m": 200,
        "icy_detour_distance_m": 280,
        "recovered_distance_m": 200,
        "checks": [
            "CPU ONNX",
            "two-frame confirmation",
            "authenticated ingest",
            "stored readings",
            "crossing-only hazards",
            "four profile routes/reports",
            "outage retention",
            "warm recovery",
            "wrong-key rejection",
        ],
    }


def main() -> int:
    try:
        print(json.dumps(run_check(), indent=2))
        return 0
    except ImportError:
        print(
            "This check needs OP-204/205/206/213 in one local checkout and the worker test dependencies; see docs/OP-205.md.",
            file=sys.stderr,
        )
        return 2
    except (AssertionError, ValueError, KeyError) as error:
        print(f"Synthetic integration check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Running scripts/ directly must still use imports rooted at the repository.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())

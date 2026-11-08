"""OP-415: the worker posts the Claude check's vehicle-incident answer with a reading.

The ``incident`` object is additive and optional on the OP-213 payload: present only
when the check ran on the posted frame, absent (not null) otherwise, so a reading
without it is byte-for-byte the body the worker always sent. Nothing here touches
the network or stores an image.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from vision import worker
from vision.inference import Incident, Prediction
from vision.tests.test_worker import NOW, Clock, make_worker, reading
from vision.worker import HttpPublisher, Reading, ReadingIncident, _incident_of

SEEN = ReadingIncident(seen=True, detail="A car sits across both lanes.")
AGREED = ("source", "source_ref", "camera_id", "view_id", "latitude", "longitude",
          "label", "score", "reason", "observed_at", "expires_at", "model_version",
          "mode", "attribution")  # fmt: skip


def with_incident(incident: ReadingIncident | None) -> Reading:
    return reading().model_copy(update={"incident": incident})


# ---------------------------------------------------------------- the payload


def test_payload_without_the_check_is_the_agreed_body_and_nothing_more() -> None:
    body = reading().payload()
    assert tuple(body) == AGREED  # same fields, same order, no incident key
    assert body == {
        k: v for k, v in reading().model_dump(mode="json").items() if k != "incident"
    }
    assert "incident" not in body


def test_payload_with_the_check_adds_only_the_incident_object() -> None:
    body = with_incident(SEEN).payload()
    assert tuple(body) == (*AGREED, "incident")
    assert body["incident"] == {"seen": True, "detail": "A car sits across both lanes."}
    assert {k: v for k, v in body.items() if k != "incident"} == reading().payload()


def test_a_quiet_frame_is_posted_as_seen_false() -> None:
    body = with_incident(
        ReadingIncident(seen=False, detail="Normal traffic.")
    ).payload()
    assert body["incident"] == {"seen": False, "detail": "Normal traffic."}


def test_the_incident_object_is_strict() -> None:
    with pytest.raises(ValueError):
        ReadingIncident(seen=True, detail="x" * 201)
    with pytest.raises(ValueError):
        ReadingIncident.model_validate({"seen": True, "detail": "x", "extra": 1})


# -------------------------------------------------------------- the publisher


def posted_bodies(readings: list[Reading]) -> list[dict]:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"stored": True})

    publisher = HttpPublisher(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "https://api.example.org",
        "test-header-value",
    )
    for item in readings:
        assert publisher.publish(item)
    return bodies


def test_publisher_sends_the_payload_with_and_without_the_field() -> None:
    plain, seen = posted_bodies([reading(), with_incident(SEEN)])
    assert "incident" not in plain
    assert seen["incident"] == {"seen": True, "detail": "A car sits across both lanes."}
    assert {k: v for k, v in seen.items() if k != "incident"} == plain


# ----------------------------------------------------------------- the worker


class IncidentClassifier:
    """A classifier whose predictions carry whatever incident each test gives."""

    version = "synthetic-test-model"

    def __init__(self, predictions: list[Prediction]) -> None:
        self.predictions = iter(predictions)

    def predict(self, frame: bytes) -> Prediction:
        return next(self.predictions)


def confirmed(predictions: list[Prediction]) -> Reading:
    camera_worker, _, _ = make_worker([])
    camera_worker.classifier = IncidentClassifier(predictions)
    assert camera_worker.cycle() == []  # one frame never confirms
    (confirmed_reading,) = camera_worker.cycle()
    return confirmed_reading


def test_the_worker_posts_the_incident_of_the_confirming_frame() -> None:
    quiet = Incident(False, "Normal traffic.")
    seen = Incident(True, "A car has left the road into the ditch.")
    got = confirmed(
        [Prediction("wet", 0.9, incident=quiet), Prediction("wet", 0.9, incident=seen)]
    )
    assert got.label == "wet" and got.score == 0.9
    assert got.incident == ReadingIncident(
        seen=True, detail="A car has left the road into the ditch."
    )
    assert got.payload()["incident"] == {
        "seen": True,
        "detail": "A car has left the road into the ditch.",
    }


def test_without_the_check_the_reading_has_no_incident() -> None:
    got = confirmed([Prediction("wet", 0.9), Prediction("wet", 0.9)])
    assert got.incident is None
    assert "incident" not in got.payload()


def test_unknown_readings_can_carry_the_answer_when_the_check_ran() -> None:
    """Claude disagreed with the model, so the label is unknown; its answer still rides along."""
    seen = Incident(True, "Spun out across the median.")
    got = confirmed(
        [
            Prediction("unknown", reason="ai_check_disagrees", incident=seen),
            Prediction("unknown", reason="ai_check_disagrees", incident=seen),
        ]
    )
    assert got.label == "unknown" and got.reason == "ai_check_disagrees"
    assert got.incident == ReadingIncident(
        seen=True, detail="Spun out across the median."
    )


@pytest.mark.parametrize(
    "carrier",
    [
        None,
        SimpleNamespace(seen="yes", detail="x"),
        SimpleNamespace(seen=True, detail=None),
        SimpleNamespace(seen=1, detail="x"),
        object(),
    ],
)
def test_a_malformed_carrier_from_a_classifier_is_no_information(carrier) -> None:
    assert _incident_of(Prediction("wet", 0.9, incident=carrier)) is None


def test_the_detail_is_one_line_of_at_most_200_characters() -> None:
    long = "A truck\nlies on its  side. " * 20
    got = _incident_of(Prediction("wet", 0.9, incident=Incident(True, long)))
    assert got is not None and "\n" not in got.detail and len(got.detail) == 200


# ----------------------------------------------------------------- the replay


def test_replay_output_has_no_incident_key() -> None:
    """``make worker`` prints the same synthetic readings it always did."""
    for row in worker.replay(worker.REPLAY_PATH)["readings"]:
        assert "incident" not in row


def test_the_detail_limit_matches_the_api_and_the_check() -> None:
    from cloud.app import camera_readings
    from vision import ai_check

    assert worker.INCIDENT_DETAIL_MAX == camera_readings.INCIDENT_DETAIL_MAX == 200
    assert ai_check.INCIDENT_DETAIL_MAX == 200


def test_expiry_and_times_are_untouched_by_the_field() -> None:
    plain, seen = reading(), with_incident(SEEN)
    assert seen.observed_at == plain.observed_at == NOW
    assert seen.expires_at == plain.expires_at == NOW + timedelta(minutes=15)
    assert Clock()() == 0.0  # the shared test clock still starts at zero

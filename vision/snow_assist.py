"""A second model for snow and ice beside the main model (lead decision, November 8, 2026).

The main model trains on 511 Alberta frames only, and no Alberta camera showed snow or
ice on November 8, so it has never seen either and answers `unknown` for them. The snow
model trains on the same frames plus RSCD (CC BY 4.0) snow and ice patches. On the 308
test frames from unseen 511 cameras it found fewer wet roads than the main model (2 of 16
against 7 of 16), so it decides nothing except snow and ice: the main model's label
stands unless the snow model answers `snow_partial`, `snow_covered` or `ice`. With
`--ai-check`, Claude must still agree with the final label before it is posted, so a
snow or ice reading needs two models and Claude to agree.

Both models are measured in `vision/MODEL_CARD.md`; snow and ice stay unmeasured on 511
cameras until real frames of them exist.
"""

from __future__ import annotations

import logging

from vision.inference import Classifier, Prediction

log = logging.getLogger("openpath.vision.snow_assist")

SNOW_ICE = frozenset({"snow_partial", "snow_covered", "ice"})
SNOW_MODEL_REASON = "snow_model"


class SnowAssistClassifier:
    """The main model's reading, unless the snow model sees snow or ice."""

    def __init__(self, main: Classifier, snow: Classifier) -> None:
        self.main = main
        self.snow = snow
        # The reading's model_version names both models (the API allows 128 characters).
        self.version = f"{main.version}+snow:{snow.version[:16]}"

    def predict(self, frame: bytes) -> Prediction:
        first = self.main.predict(frame)
        second = self.snow.predict(frame)
        if second.label in SNOW_ICE:
            log.info(
                "snow model: %s (score %s) over main %s",
                second.label,
                second.score,
                first.label,
            )
            return Prediction(second.label, second.score, reason=SNOW_MODEL_REASON)
        return first

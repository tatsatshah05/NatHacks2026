"""The hand-off between training (OP-202) and the worker (OP-204): labels.json.

train.py writes labels.json next to model.onnx with labels_manifest(); the worker
reads it to prepare images exactly as training did. Keeping the wording and the
class list here, in one module with no PyTorch, means a change to either side
cannot silently break the other, and CI can test the manifest without PyTorch.
"""

from __future__ import annotations

from typing import Any

from vision.tools.label import LABELS

INPUT_NAME = "image"
OUTPUT_NAME = "logits"
LAYOUT = "NCHW"
RESIZE = "stretch to size_px x size_px"
COLOR = "RGB"
SCALE = "pixel / 255"
SOFTMAX = "apply in the worker"
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def labels_manifest(size_px: int, model_sha256: str) -> dict[str, Any]:
    """The labels.json content for a model trained at size_px, identified by its SHA-256."""
    if len(model_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in model_sha256
    ):
        raise ValueError("model_sha256 must be a 64-character lowercase hex digest")
    return {
        "classes": list(LABELS),
        "sha256": model_sha256,
        "input": {
            "name": INPUT_NAME,
            "layout": LAYOUT,
            "size_px": size_px,
            "resize": RESIZE,
            "color": COLOR,
            "scale": SCALE,
            "mean": list(IMAGENET_MEAN),
            "std": list(IMAGENET_STD),
        },
        "output": {"name": OUTPUT_NAME, "softmax": SOFTMAX},
    }

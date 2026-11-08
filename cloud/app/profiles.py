"""Mobility profiles: how each kind of barrier changes a route's cost (OP-207).

The numbers live in ``config/profiles.json`` (a shared contract, see AGENTS.md).
This module loads and validates them, and answers one question for the routing
engine: for this profile, what does a given effect do to a path? Either a cost
multiplier (1.0 means no change) or ``None``, meaning "remove the path".

Example: for ``wheelchair``, ``snow`` is ``None`` (impassable) and ``ice_risk`` is
``3.0`` (allowed but three times as costly, so routing prefers to go around).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import Effect

DEFAULT_PROFILES_PATH = Path(__file__).resolve().parents[2] / "config" / "profiles.json"


class Profile(BaseModel):
    """One mobility profile."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    speed_m_s: float = Field(gt=0)
    # effect -> multiplier, or None to remove the path.
    effects: dict[Effect, float | None]
    # Multiplier for highway=steps edges, or None to remove them.
    stairs: float | None

    @field_validator("effects")
    @classmethod
    def _every_effect_present(
        cls, value: dict[Effect, float | None]
    ) -> dict[Effect, float | None]:
        missing = [effect.value for effect in Effect if effect not in value]
        if missing:
            raise ValueError(f"profile is missing effects: {missing}")
        bad = [
            effect.value
            for effect, mult in value.items()
            if mult is not None and mult < 1
        ]
        if bad:
            raise ValueError(
                f"multipliers must be at least 1 (a barrier never speeds you up): {bad}"
            )
        return value

    def multiplier(self, effect: Effect) -> float | None:
        return self.effects[effect]


class Alternates(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_routes: int = Field(ge=1, le=5, default=3)
    max_shared_fraction: float = Field(gt=0, le=1, default=0.7)
    penalty: float = Field(gt=1, default=1.6)
    attempts: int = Field(ge=1, le=10, default=4)


class Snap(BaseModel):
    model_config = ConfigDict(frozen=True)

    warn_m: float = Field(gt=0, default=150)
    max_m: float = Field(gt=0, default=1000)


class RoutingConfig(BaseModel):
    """Everything ``config/profiles.json`` holds."""

    model_config = ConfigDict(frozen=True)

    profiles: dict[str, Profile]
    alternates: Alternates = Alternates()
    snap: Snap = Snap()

    def profile(self, key: str) -> Profile:
        try:
            return self.profiles[key]
        except KeyError:
            raise KeyError(
                f"unknown profile {key!r}; choose one of {sorted(self.profiles)}"
            ) from None


def load_routing_config(path: Path | str = DEFAULT_PROFILES_PATH) -> RoutingConfig:
    """Read and validate ``config/profiles.json``."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = {
        key: Profile(
            key=key, **{k: v for k, v in entry.items() if not k.startswith("_")}
        )
        for key, entry in raw["profiles"].items()
    }
    strip = lambda d: {k: v for k, v in (d or {}).items() if not k.startswith("_")}
    return RoutingConfig(
        profiles=profiles,
        alternates=Alternates(**strip(raw.get("alternates"))),
        snap=Snap(**strip(raw.get("snap"))),
    )

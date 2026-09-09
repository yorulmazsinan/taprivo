"""Configuration models and loader."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from taprivo import paths


class ConfigError(Exception):
    """Raised when configuration is invalid."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EnergyConfig(_Frozen):
    energy_per_tap: int = Field(default=10, ge=1)
    max_energy: int = Field(default=10000, ge=1)


class ComboTier(_Frozen):
    at: int = Field(ge=1, description="Combo count at which this multiplier starts.")
    multiplier: float = Field(ge=1.0)


class ComboConfig(_Frozen):
    enabled: bool = True
    timeout_ms: int = Field(default=600, ge=1)
    energy_multiplier_enabled: bool = True
    tiers: tuple[ComboTier, ...] = (
        ComboTier(at=10, multiplier=1.5),
        ComboTier(at=25, multiplier=2.0),
    )

    @model_validator(mode="after")
    def _tiers_ascend(self) -> ComboConfig:
        thresholds = [tier.at for tier in self.tiers]
        if thresholds != sorted(set(thresholds)):
            raise ValueError(
                f"combo.tiers must be in ascending order by 'at' with no repeats (got {thresholds})"
            )
        return self


class RhythmConfig(_Frozen):
    enabled: bool = True
    window: int = Field(default=8, ge=4, description="How many recent tap intervals to weigh.")
    tolerance: float = Field(
        default=0.15, gt=0, le=1, description="Allowed spread of the intervals, 0-1."
    )
    bpm_min: int = Field(default=60, ge=1)
    bpm_max: int = Field(default=240, ge=1)
    steady_multiplier: float = Field(default=1.25, ge=1.0)

    @model_validator(mode="after")
    def _bpm_range_ascends(self) -> RhythmConfig:
        if self.bpm_min >= self.bpm_max:
            raise ValueError(
                "rhythm.bpm_min must be below rhythm.bpm_max "
                f"(got bpm_min={self.bpm_min}, bpm_max={self.bpm_max})"
            )
        return self


class ServerConfig(_Frozen):
    host: str = "127.0.0.1"
    port: int = Field(default=32145, ge=1, le=65535)
    max_reason_length: int = Field(default=200, ge=1, le=2000)
    rate_limit_per_second: int = Field(default=20, ge=1)

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value in ("127.0.0.1", "localhost"):
            return value
        raise ValueError("server.host must be a loopback address (127.0.0.1 or localhost)")


class SimulatorConfig(_Frozen):
    displacement: float = Field(default=0.04, gt=0)
    velocity: float = Field(default=0.6, gt=0)
    max_taps_per_second: int = Field(default=12, ge=1)


class HudConfig(_Frozen):
    always_on_top: bool = True
    opacity: float = Field(default=0.92, ge=0.2, le=1.0)
    reduced_motion: bool = False
    theme: Literal["system", "dark", "light"] = "system"


class CameraConfig(_Frozen):
    device_index: int | None = Field(default=None, ge=0)
    width: int = Field(default=640, ge=160, le=4096)
    height: int = Field(default=480, ge=120, le=4096)
    preview_fps: int = Field(default=15, ge=1, le=60)


class SqueezeConfig(_Frozen):
    smoothing_alpha: float = Field(default=0.5, gt=0, le=1)
    open_level: float = Field(default=0.80, gt=0, le=2)
    closed_level: float = Field(default=0.45, gt=0, le=2)
    band_ratio: float = Field(default=0.25, gt=0, lt=0.5)
    min_closed_ms: int = Field(default=120, ge=0)
    max_cycle_ms: int = Field(default=2500, ge=100)
    cooldown_ms: int = Field(default=300, ge=0)
    frame_gap_reset_ms: int = Field(default=250, ge=50)

    @model_validator(mode="after")
    def _open_above_closed(self) -> SqueezeConfig:
        if self.open_level <= self.closed_level:
            raise ValueError(
                "squeeze.open_level must be greater than squeeze.closed_level "
                f"(got open_level={self.open_level}, closed_level={self.closed_level})"
            )
        return self


class StatsConfig(_Frozen):
    enabled: bool = True
    path: str | None = None


class Config(_Frozen):
    schema_version: int = 1
    energy: EnergyConfig = Field(default_factory=EnergyConfig)
    combo: ComboConfig = Field(default_factory=ComboConfig)
    rhythm: RhythmConfig = Field(default_factory=RhythmConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    hud: HudConfig = Field(default_factory=HudConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    squeeze: SqueezeConfig = Field(default_factory=SqueezeConfig)
    stats: StatsConfig = Field(default_factory=StatsConfig)

    @property
    def endpoint_url(self) -> str:
        return f"http://{self.server.host}:{self.server.port}/mcp"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(text: str, source: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: top level must be a mapping")
    return data


def default_config_text() -> str:
    return resources.files("taprivo.resources").joinpath("default.yaml").read_text("utf-8")


def load_config(user_path: Path | None = None) -> Config:
    data = _load_yaml(default_config_text(), "default.yaml")
    path = user_path or paths.user_config_path()
    if path.exists():
        data = _deep_merge(data, _load_yaml(path.read_text("utf-8"), str(path)))
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
        raise ConfigError("invalid configuration:\n  " + "\n  ".join(lines)) from exc

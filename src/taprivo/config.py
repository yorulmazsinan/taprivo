"""Configuration models and loader."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from taprivo import paths


class ConfigError(Exception):
    """Raised when configuration is invalid."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EnergyConfig(_Frozen):
    energy_per_tap: int = Field(default=10, ge=1)
    max_energy: int = Field(default=10000, ge=1)


class ComboConfig(_Frozen):
    enabled: bool = True
    timeout_ms: int = Field(default=600, ge=1)
    energy_multiplier_enabled: bool = False


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


class HudConfig(_Frozen):
    always_on_top: bool = True
    opacity: float = Field(default=0.92, ge=0.2, le=1.0)
    reduced_motion: bool = False


class CameraConfig(_Frozen):
    device_index: int | None = Field(default=None, ge=0)
    width: int = Field(default=640, ge=160, le=4096)
    height: int = Field(default=480, ge=120, le=4096)
    preview_fps: int = Field(default=15, ge=1, le=60)


class DetectorConfig(_Frozen):
    smoothing_alpha: float = Field(default=0.4, gt=0, le=1)
    baseline_alpha: float = Field(default=0.03, gt=0, le=1)
    threshold: float = Field(default=0.22, gt=0, le=1)
    release_ratio: float = Field(default=0.4, gt=0, lt=1)
    max_cycle_ms: int = Field(default=600, ge=50)
    cooldown_ms: int = Field(default=140, ge=0)
    attribution_window_ms: int = Field(default=120, ge=0)
    reacquire_guard_ms: int = Field(default=300, ge=0)
    frame_gap_reset_ms: int = Field(default=250, ge=50)


class Config(_Frozen):
    schema_version: int = 1
    energy: EnergyConfig = Field(default_factory=EnergyConfig)
    combo: ComboConfig = Field(default_factory=ComboConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    hud: HudConfig = Field(default_factory=HudConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)

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

from pathlib import Path

import pytest
from pydantic import ValidationError

from taprivo.config import ConfigError, load_config


def test_defaults(taprivo_home: Path) -> None:
    cfg = load_config()
    assert cfg.energy.energy_per_tap == 10
    assert cfg.energy.max_energy == 10000
    assert cfg.combo.timeout_ms == 600
    assert cfg.combo.energy_multiplier_enabled is False
    assert cfg.simulator.max_taps_per_second == 12
    assert cfg.server.port == 32145
    assert cfg.server.max_reason_length == 200
    assert cfg.endpoint_url == "http://127.0.0.1:32145/mcp"


def test_user_override_merges(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("server:\n  port: 40000\n")
    cfg = load_config()
    assert cfg.server.port == 40000
    assert cfg.energy.energy_per_tap == 10


def test_unknown_key_rejected(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("energy:\n  per_tap: 5\n")
    with pytest.raises(ConfigError, match="per_tap"):
        load_config()


def test_non_loopback_host_rejected(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("server:\n  host: 0.0.0.0\n")
    with pytest.raises(ConfigError, match="loopback"):
        load_config()


@pytest.mark.parametrize("host", ["::1", "127.0.0.2"])
def test_other_loopback_addresses_rejected(taprivo_home: Path, host: str) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(f"server:\n  host: {host}\n")
    with pytest.raises(ConfigError, match="loopback"):
        load_config()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_accepted_hosts_pass(taprivo_home: Path, host: str) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(f"server:\n  host: {host}\n")
    cfg = load_config()
    assert cfg.server.host == host


def test_config_is_frozen(taprivo_home: Path) -> None:
    cfg = load_config()
    with pytest.raises(ValidationError):
        cfg.energy.energy_per_tap = 99  # type: ignore[misc]


def test_camera_and_squeeze_defaults(taprivo_home: Path) -> None:
    cfg = load_config()
    assert cfg.camera.device_index is None
    assert (cfg.camera.width, cfg.camera.height, cfg.camera.preview_fps) == (640, 480, 15)
    assert cfg.squeeze.open_level == 0.80
    assert cfg.squeeze.cooldown_ms == 300
    assert cfg.squeeze.frame_gap_reset_ms == 250


def test_camera_override(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(
        "camera:\n  device_index: 1\nsqueeze:\n  cooldown_ms: 500\n"
    )
    cfg = load_config()
    assert cfg.camera.device_index == 1
    assert cfg.squeeze.cooldown_ms == 500


def test_inverted_squeeze_levels_rejected(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(
        "squeeze:\n  open_level: 0.40\n  closed_level: 0.80\n"
    )
    with pytest.raises(ConfigError, match=r"open_level.*closed_level"):
        load_config()


def test_equal_squeeze_levels_rejected(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(
        "squeeze:\n  open_level: 0.50\n  closed_level: 0.50\n"
    )
    with pytest.raises(ConfigError, match=r"open_level.*closed_level"):
        load_config()


def test_hud_theme_defaults_to_system(taprivo_home: Path) -> None:
    cfg = load_config()
    assert cfg.hud.theme == "system"


@pytest.mark.parametrize("theme", ["system", "dark", "light"])
def test_hud_theme_accepts_known_values(taprivo_home: Path, theme: str) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(f"hud:\n  theme: {theme}\n")
    cfg = load_config()
    assert cfg.hud.theme == theme


def test_hud_theme_rejects_unknown_value(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("hud:\n  theme: neon\n")
    with pytest.raises(ConfigError, match="theme"):
        load_config()


def test_tap_cap_must_be_at_least_one(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("simulator:\n  max_taps_per_second: 0\n")
    with pytest.raises(ConfigError, match="max_taps_per_second"):
        load_config()

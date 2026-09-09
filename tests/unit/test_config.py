from pathlib import Path

import pytest
from pydantic import ValidationError

from taprivo.config import ConfigError, load_config


def test_defaults(taprivo_home: Path) -> None:
    cfg = load_config()
    assert cfg.energy.energy_per_tap == 10
    assert cfg.energy.max_energy == 10000
    assert cfg.combo.timeout_ms == 600
    assert cfg.combo.energy_multiplier_enabled is True
    assert [(tier.at, tier.multiplier) for tier in cfg.combo.tiers] == [(10, 1.5), (25, 2.0)]
    assert cfg.rhythm.enabled is True
    assert cfg.rhythm.window == 8
    assert cfg.rhythm.tolerance == 0.15
    assert (cfg.rhythm.bpm_min, cfg.rhythm.bpm_max) == (60, 240)
    assert cfg.rhythm.steady_multiplier == 1.25
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


def test_combo_tiers_override(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(
        "combo:\n  tiers:\n    - {at: 5, multiplier: 1.25}\n    - {at: 40, multiplier: 3.0}\n"
    )
    cfg = load_config()
    assert [(tier.at, tier.multiplier) for tier in cfg.combo.tiers] == [(5, 1.25), (40, 3.0)]


def test_combo_tiers_must_ascend(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(
        "combo:\n  tiers:\n    - {at: 25, multiplier: 2.0}\n    - {at: 10, multiplier: 1.5}\n"
    )
    with pytest.raises(ConfigError, match="ascending"):
        load_config()


def test_combo_tier_bounds(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("combo:\n  tiers:\n    - {at: 0, multiplier: 1.5}\n")
    with pytest.raises(ConfigError, match="at"):
        load_config()
    (taprivo_home / "config.yaml").write_text("combo:\n  tiers:\n    - {at: 5, multiplier: 0.5}\n")
    with pytest.raises(ConfigError, match="multiplier"):
        load_config()


def test_empty_combo_tiers_allowed(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("combo:\n  tiers: []\n")
    assert load_config().combo.tiers == ()


def test_stats_defaults_to_enabled_with_the_home_path(taprivo_home: Path) -> None:
    from taprivo import paths

    cfg = load_config()
    assert cfg.stats.enabled is True
    assert cfg.stats.path is None
    assert paths.stats_path(cfg.stats.path) == taprivo_home / "stats.sqlite"


def test_stats_can_be_disabled_and_moved(taprivo_home: Path) -> None:
    from taprivo import paths

    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(
        "stats:\n  enabled: false\n  path: ~/elsewhere/stats.sqlite\n"
    )
    cfg = load_config()
    assert cfg.stats.enabled is False
    assert paths.stats_path(cfg.stats.path) == Path.home() / "elsewhere" / "stats.sqlite"


def test_unknown_stats_key_rejected(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("stats:\n  keep_days: 30\n")
    with pytest.raises(ConfigError, match="keep_days"):
        load_config()


def test_rhythm_override(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text(
        "rhythm:\n  enabled: false\n  steady_multiplier: 1.5\n  window: 6\n"
    )
    cfg = load_config()
    assert cfg.rhythm.enabled is False
    assert cfg.rhythm.steady_multiplier == 1.5
    assert cfg.rhythm.window == 6
    assert cfg.rhythm.tolerance == 0.15  # untouched keys keep the shipped default


def test_rhythm_bpm_range_must_ascend(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("rhythm:\n  bpm_min: 240\n  bpm_max: 60\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "bpm_min" in str(exc.value)


def test_rhythm_bounds(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("rhythm:\n  window: 3\n")
    with pytest.raises(ConfigError):
        load_config()
    (taprivo_home / "config.yaml").write_text("rhythm:\n  tolerance: 0\n")
    with pytest.raises(ConfigError):
        load_config()
    (taprivo_home / "config.yaml").write_text("rhythm:\n  steady_multiplier: 0.5\n")
    with pytest.raises(ConfigError):
        load_config()

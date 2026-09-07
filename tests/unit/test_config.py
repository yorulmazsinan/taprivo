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

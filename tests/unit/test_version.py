from importlib.metadata import version

import taprivo


def test_version_matches_package_metadata() -> None:
    assert taprivo.__version__ == "0.1.0b2"
    assert version("taprivo") == "0.1.0b2"

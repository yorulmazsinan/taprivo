import hashlib
from importlib import resources

EXPECTED_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


def test_bundled_hand_model_matches_checksum() -> None:
    data = resources.files("taprivo.resources.models").joinpath("hand_landmarker.task").read_bytes()
    assert len(data) == 7_819_105
    assert hashlib.sha256(data).hexdigest() == EXPECTED_SHA256

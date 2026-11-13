import pytest
from ciwaste.rates import Pricer, billed_minutes, parse_rate_overrides


@pytest.mark.parametrize("labels,sku,rate", [
    (["ubuntu-latest"], "actions_linux", 0.006),
    (["ubuntu-24.04"], "actions_linux", 0.006),
    (["ubuntu-slim"], "actions_linux_slim", 0.002),
    (["ubuntu-24.04-arm"], "actions_linux_arm", 0.005),
    (["windows-latest"], "actions_windows", 0.010),
    (["windows-11-arm"], "actions_windows_arm", 0.010),
    (["macos-latest"], "actions_macos", 0.062),
    (["macos-15"], "actions_macos", 0.062),
    (["macos-15-intel"], "actions_macos", 0.062),
    (["macos-14-large"], "macos_l", 0.077),
    (["macos-15-xlarge"], "macos_xl", 0.102),
    (["linux_8_core"], "linux_8_core", 0.022),
    (["self-hosted", "linux", "x64"], "self-hosted", 0.0),
])
def test_label_mapping(labels, sku, rate):
    p = Pricer()
    assert p.sku(labels) == sku
    assert p.rate(sku) == rate


def test_unknown_and_override():
    p = Pricer({"big-runner": 0.05})
    assert p.sku(["my-custom-label"]).startswith("unknown:")
    assert p.rate(p.sku(["my-custom-label"])) is None
    assert p.rate(p.sku(["big-runner"])) == 0.05


def test_self_hosted_rate():
    p = Pricer(self_hosted_rate=0.002)
    assert p.rate(p.sku(["self-hosted"])) == 0.002


def test_rounding():
    assert billed_minutes(0) == 0
    assert billed_minutes(1) == 1
    assert billed_minutes(60) == 1
    assert billed_minutes(61) == 2


def test_public_free():
    p = Pricer()
    assert p.is_free_in_public("actions_macos")
    assert not p.is_free_in_public("linux_8_core")


def test_parse_overrides():
    assert parse_rate_overrides(["a=0.1"]) == {"a": 0.1}
    with pytest.raises(ValueError):
        parse_rate_overrides(["nope"])

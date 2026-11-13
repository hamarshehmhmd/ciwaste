"""GitHub-hosted runner per-minute rates (USD).

Source: https://docs.github.com/en/billing/reference/actions-runner-pricing
(rates in effect since January 1, 2026; checked November 2026).
GitHub rounds each job's minutes up to the nearest whole minute.
"""
import math
import re

RATES_AS_OF = "2026-11"
RATES_URL = "https://docs.github.com/en/billing/reference/actions-runner-pricing"

SKU_RATES = {
    "actions_linux_slim": 0.002,
    "actions_linux": 0.006,
    "actions_linux_arm": 0.005,
    "actions_windows": 0.010,
    "actions_windows_arm": 0.010,
    "actions_macos": 0.062,
    "macos_l": 0.077,
    "macos_xl": 0.102,
    "linux_2_core_advanced": 0.006,
    "linux_4_core": 0.012,
    "linux_8_core": 0.022,
    "linux_16_core": 0.042,
    "linux_32_core": 0.082,
    "linux_64_core": 0.162,
    "linux_96_core": 0.252,
    "windows_4_core": 0.022,
    "windows_8_core": 0.042,
    "windows_16_core": 0.082,
    "windows_32_core": 0.162,
    "windows_64_core": 0.322,
    "windows_96_core": 0.552,
    "linux_2_core_arm": 0.005,
    "linux_4_core_arm": 0.008,
    "linux_8_core_arm": 0.014,
    "linux_16_core_arm": 0.026,
    "linux_32_core_arm": 0.050,
    "linux_64_core_arm": 0.098,
    "windows_2_core_arm": 0.008,
    "windows_4_core_arm": 0.014,
    "windows_8_core_arm": 0.026,
    "windows_16_core_arm": 0.050,
    "windows_32_core_arm": 0.098,
    "windows_64_core_arm": 0.194,
    "linux_4_core_gpu": 0.052,
    "windows_4_core_gpu": 0.102,
}

# Standard runners: free in public repos. Larger runners are billed everywhere.
STANDARD_SKUS = {"actions_linux_slim", "actions_linux", "actions_linux_arm", "actions_windows",
                 "actions_windows_arm", "actions_macos"}

_LABEL_RULES = [
    (re.compile(r"^ubuntu-slim$"), "actions_linux_slim"),
    (re.compile(r"^ubuntu-[\w.]+-arm$"), "actions_linux_arm"),
    (re.compile(r"^ubuntu-(latest|\d+\.\d+)$"), "actions_linux"),
    (re.compile(r"^windows-[\w.]*-?arm$"), "actions_windows_arm"),
    (re.compile(r"^windows-(latest|\d+)$"), "actions_windows"),
    (re.compile(r"^macos-[\w.]+-xlarge$"), "macos_xl"),
    (re.compile(r"^macos-[\w.]+-large$"), "macos_l"),
    (re.compile(r"^macos-(latest|\d+(\.\d+)?)(-intel)?$"), "actions_macos"),
]


class Pricer:
    """Maps a job's runner labels to a SKU and a per-minute rate."""

    def __init__(self, overrides=None, self_hosted_rate=0.0):
        self.overrides = {k.lower(): float(v) for k, v in (overrides or {}).items()}
        self.self_hosted_rate = float(self_hosted_rate)

    def sku(self, labels):
        labels = [str(x).lower() for x in (labels or [])]
        for lab in labels:
            if lab in self.overrides:
                return "custom:" + lab
        if "self-hosted" in labels:
            return "self-hosted"
        for lab in labels:
            if lab in SKU_RATES:
                return lab
            for rx, sku in _LABEL_RULES:
                if rx.match(lab):
                    return sku
        return "unknown:" + (",".join(labels) or "none")

    def rate(self, sku):
        if sku.startswith("custom:"):
            return self.overrides[sku[7:]]
        if sku == "self-hosted":
            return self.self_hosted_rate
        return SKU_RATES.get(sku)  # None for unknown

    def is_free_in_public(self, sku):
        return sku in STANDARD_SKUS


def billed_minutes(seconds):
    """GitHub bills each job rounded up to a whole minute; zero-length jobs cost nothing."""
    if seconds <= 0:
        return 0
    return int(math.ceil(seconds / 60.0))


def parse_rate_overrides(items):
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError("--rate expects LABEL=USD_PER_MINUTE, got %r" % item)
        k, v = item.split("=", 1)
        out[k.strip()] = float(v)
    return out

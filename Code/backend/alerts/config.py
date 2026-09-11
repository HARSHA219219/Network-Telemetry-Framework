from __future__ import annotations

import yaml


def load_thresholds(path: str) -> dict:
    """Loads config/thresholds.yaml into a flat dict of threshold values."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("thresholds", {})

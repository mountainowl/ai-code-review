from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_default_poller_enables_core_longtail_repos() -> None:
    config = tomllib.loads((ROOT / "config" / "poller.toml").read_text())
    projects = {
        item["path"]
        for item in config["projects"]
        if item.get("enabled", True)
    }

    assert projects == {
        "longtaildev/dataGatherer",
        "longtaildev/internal-ui",
        "longtaildev/commonservice",
        "longtaildev/itinerary-engine",
        "longtaildev/pricingOrchestrator",
    }


def test_default_poller_review_limits() -> None:
    config = tomllib.loads((ROOT / "config" / "poller.toml").read_text())

    assert config["max_reviews_per_run"] == 8
    assert config["max_findings_per_review"] == 8

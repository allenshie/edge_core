"""業務輸出回歸：導入健康回報前後，送給 messaging client 的 topic 與 payload 必須相同（見本目錄的 README.md）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import comparable, load_baseline, run_harness  # noqa: E402

MODES = {
    "health-enabled": {"EDGE_HEALTH_ENABLED": "1"},
    "health-disabled": {"EDGE_HEALTH_ENABLED": "0"},
    "observation": {"EDGE_HEALTH_ENABLED": "1", "EDGE_HEALTH_EXIT_ON_FAILURE": "0"},
}


@pytest.mark.parametrize("mode", sorted(MODES))
def test_business_output_matches_baseline(mode: str) -> None:
    result = run_harness(MODES[mode])

    assert comparable(result) == load_baseline()
    assert result["_exit_calls"] == []

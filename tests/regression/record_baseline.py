"""錄製業務輸出基準。必須在導入健康回報之前的環境執行（見同目錄 README.md）。

用法：python tests/regression/record_baseline.py --commit <edge_core commit> [--output 路徑]
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from harness import BASELINE_PATH, comparable, run_harness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="錄製時 edge_core 的 commit")
    parser.add_argument("--smart-workflow", required=True, help="錄製時 smart-workflow 的版本或 commit")
    parser.add_argument("--output", type=Path, default=BASELINE_PATH)
    args = parser.parse_args()

    result = run_harness()
    document = {
        "recorded_with": {
            "edge_core_commit": args.commit,
            "smart_workflow": args.smart_workflow,
            "python": platform.python_version(),
        },
        "result": comparable(result),
    }
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"寫入 {args.output}：{len(result['publishes'])} 次 publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

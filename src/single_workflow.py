#!/usr/bin/env python3
"""使用 batch 缓存的全局参数计算一个 TJA，结果仅输出到控制台。"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from batch_workflow import PROJECT_ROOT, build_chart_entry, decode_tja
from computing_parameters import DEFAULT_PARAMETERS, load_computing_parameters
from rating import RatingPipeline
from tja_analysis import TJAChartAnalyzer


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="单个 TJA 文件的绝对路径或相对路径")
    parser.add_argument(
        "--parameters", type=Path, default=Path(DEFAULT_PARAMETERS),
        help=f"batch 生成的参数缓存（默认 {DEFAULT_PARAMETERS}）",
    )
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error(f"TJA 文件不存在或不是文件：{args.file}")

    try:
        ref_values = load_computing_parameters(args.parameters)
        content = decode_tja(args.file.read_bytes())
        charts = TJAChartAnalyzer().analyze_and_process(content)
        if not charts:
            raise ValueError("无可用谱面分支")
        results = RatingPipeline(ref_values).compute_all(charts)
        output = {
            "path": str(args.file.resolve()),
            "charts": [build_chart_entry(c, r) for c, r in zip(charts, results)],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    except Exception as exc:
        print(f"计算失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

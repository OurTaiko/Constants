#!/usr/bin/env python3
"""
TJA 定数计算工作流
==================

第一阶段: TJA 文件 → API 分析 → 原始定数计算 (tja_analysis)
第二阶段: 原始定数 → 最终8字段输出 (rating)

用法:
    uv run workflow.py chart.tja                  # 分析单个谱面文件
    uv run workflow.py chart.tja --unit measures  # 使用 measure 单位
    uv run workflow.py chart.tja --json           # 输出 JSON 格式
    cat chart.tja | uv run workflow.py --stdin    # 从标准输入读取
    uv run workflow.py chart.tja --raw            # 仅输出第一阶段（原始定数）

输出 (默认 8 字段):
    sub_constant_1, main_constant, sub_constant_2,
    stamina, handspeed, burst, complex, rhythm
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error

from tja_analysis import TJAChartAnalyzer, DEFAULT_API_URL
from rating import RatingPipeline


def format_text_output(charts: list, results: list, show_raw: bool = False) -> str:
    """格式化文本输出。"""
    lines = ["=" * 80, "  TJA 定数计算结果", "=" * 80]

    for chart, result in zip(charts, results):
        branch_label = (
            f" [{chart['branchType']}]"
            if chart["branchType"] != "unbranched"
            else ""
        )
        lines.append(f"\n---{chart['course']}{branch_label}---")

        if show_raw:
            r = chart["ratings"]
            lines.append(f"  Note 总数:            {r['totalNotes']:>10}")
            lines.append(f"  体力 (stamina):        {r['stamina']:>10.4f}")
            lines.append(f"  手速 (speed):          {r['speed']:>10.4f}")
            lines.append(f"  爆发 (burst):          {r['burst']:>10.4f}")
            lines.append(f"  复合 (complex):        {r['complex']:>10.4f}  (占比: {r['complexRatio']:.6f})")
            lines.append(f"  节奏 (rhythm):         {r['rhythm']:>10.4f}  (占比: {r['rhythmRatio']:.6f})")
            lines.append(f"  手速95 (speed95):      {r['speed95']:>10.4f}")
            lines.append(f"  节奏整体 (rhythmO):    {r['rhythmOverall']:>10.4f}  (占比: {r['rhythmRatioOverall']:.6f})")
            lines.append(f"  滚奏等效 (rollEq):     {r['rollEquivalent']:>10.4f}")
        else:
            lines.append(f"  sub_constant_1 (75):   {result.sub_constant_1:>10.4f}")
            lines.append(f"  main_constant:         {result.main_constant:>10.4f}")
            lines.append(f"  sub_constant_2 (99):   {result.sub_constant_2:>10.4f}")
            lines.append(f"  stamina:               {result.stamina:>10.4f}")
            lines.append(f"  handspeed:             {result.handspeed:>10.4f}")
            lines.append(f"  burst:                 {result.burst:>10.4f}")
            lines.append(f"  complex:               {result.complex:>10.4f}")
            lines.append(f"  rhythm:                {result.rhythm:>10.4f}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def format_json_output(charts: list, results: list, show_raw: bool = False) -> str:
    """格式化 JSON 输出。"""
    output = []
    for chart, result in zip(charts, results):
        entry = {
            "course": chart["course"],
            "difficulty": chart["difficulty"],
            "branchType": chart["branchType"],
        }
        if show_raw:
            entry["ratings"] = chart["ratings"]
        else:
            entry.update(result.as_dict())
        output.append(entry)
    return json.dumps(output, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description="TJA 定数计算工作流 — 分析谱面并输出最终定数",
        epilog=f"API 地址: {DEFAULT_API_URL}\n可通过环境变量 TJA_API_URL 覆盖",
    )
    parser.add_argument("file", nargs="?", help="TJA 谱面文件路径")
    parser.add_argument(
        "--unit", choices=["ms", "measures"], default="ms", help="间隔单位 (默认: ms)"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="仅输出第一阶段结果（原始算法定数），不经过 rating 管线",
    )

    args = parser.parse_args()

    try:
        # ---- 读取 TJA 内容 ----
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                tja_content = f.read()
        else:
            parser.print_help()
            sys.exit(1)

        if not tja_content.strip():
            print("Error: empty TJA content", file=sys.stderr)
            sys.exit(1)

        # ---- 第一阶段: TJA 分析 + 原始定数计算 ----
        print(f"[请求 API: {DEFAULT_API_URL}]", file=sys.stderr)
        analyzer = TJAChartAnalyzer()
        charts = analyzer.analyze_and_process(tja_content, args.unit)

        if not charts:
            print("Warning: 未找到可计算的谱面数据", file=sys.stderr)
            sys.exit(0)

        # ---- 第二阶段: 最终定数管线 ----
        if args.raw:
            # 仅原始定数输出
            results = None
            print("[原始定数模式 — 跳过 rating 管线]", file=sys.stderr)
            if args.json:
                output = []
                for c in charts:
                    output.append({
                        "course": c["course"],
                        "difficulty": c["difficulty"],
                        "branchType": c["branchType"],
                        "ratings": c["ratings"],
                    })
                print(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                # 用旧格式打印原始定数
                lines = ["=" * 80, "  TJA 原始定数计算结果", "=" * 80]
                for c in charts:
                    r = c["ratings"]
                    branch_label = (
                        f" [{c['branchType']}]"
                        if c["branchType"] != "unbranched"
                        else ""
                    )
                    lines.append(f"\n---{c['course']}{branch_label}---")
                    lines.append(f"  Note 总数:        {r['totalNotes']:>10}")
                    lines.append(f"  体力 (stamina):    {r['stamina']:>10.4f}")
                    lines.append(f"  手速 (speed):      {r['speed']:>10.4f}")
                    lines.append(f"  爆发 (burst):      {r['burst']:>10.4f}")
                    lines.append(f"  复合 (complex):    {r['complex']:>10.4f}  (占比: {r['complexRatio']:.6f})")
                    lines.append(f"  节奏 (rhythm):     {r['rhythm']:>10.4f}  (占比: {r['rhythmRatio']:.6f})")
                    lines.append(f"  手速95 (speed95):  {r['speed95']:>10.4f}")
                    lines.append(f"  节奏整体 (rhythmO):{r['rhythmOverall']:>10.4f}  (占比: {r['rhythmRatioOverall']:.6f})")
                    lines.append(f"  滚奏等效 (rollEq): {r['rollEquivalent']:>10.4f}")
                lines.append("\n" + "=" * 80)
                print("\n".join(lines))
        else:
            print("[计算最终定数中...]", file=sys.stderr)
            pipeline = RatingPipeline()
            results = pipeline.compute_all(charts)

            if args.json:
                print(format_json_output(charts, results, show_raw=False))
            else:
                print(format_text_output(charts, results, show_raw=False))

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        print(f"API HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"无法连接 API ({DEFAULT_API_URL}): {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

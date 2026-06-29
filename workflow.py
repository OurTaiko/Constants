#!/usr/bin/env python3
"""
本地 TJA 定数计算工作流
=======================

用法:
    uv run workflow.py chart.tja                  # 分析单个谱面文件
    uv run workflow.py chart.tja --unit measures  # 使用 measure 单位
    uv run workflow.py chart.tja --json           # 输出 JSON 格式
    cat chart.tja | uv run workflow.py --stdin    # 从标准输入读取

工作流:
    TJA 文件 → POST 到 TJAAnalyzer API → 获取分析结果 (gaps + noteTypes) → 计算定数 → 输出

输出:
    体力(stamina), 复合(complex), 节奏(rhythm), 节奏整体(rhythmOverall),
    手速(speed), 手速95(speed95), 滚奏等效(rollEquivalent), 爆发(burst)
"""

import argparse
import json
import os
import sys
import importlib
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# 配置 — 替换为实际的 TJAAnalyzer API 地址
# ---------------------------------------------------------------------------
API_URL = os.environ.get("TJA_API_URL", "https://tja-analysis.ourtaiko.org")
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALGORITHMS_DIR = os.path.join(SCRIPT_DIR, "algorithms")
sys.path.insert(0, ALGORITHMS_DIR)

# 通过 Unicode 码点导入中文模块名
_stamina = importlib.import_module("\u4f53\u529b")            # 体力
_compound = importlib.import_module("\u590d\u5408")           # 复合
_rhythm = importlib.import_module("\u8282\u594f")             # 节奏
_rhythm_overall = importlib.import_module("\u8282\u594f_\u6574\u4f53")  # 节奏_整体
_speed = importlib.import_module("\u624b\u901f")              # 手速
_speed95 = importlib.import_module("\u624b\u901f_95\u7ebf")   # 手速_95线
_roll = importlib.import_module("\u6eda\u594f\u7b49\u6548")   # 滚奏等效
_burst = importlib.import_module("\u7206\u53d1")              # 爆发


def normalize_difficulty_name(name):
    """标准化难度名称，同时处理 course_side 格式（如 edit_p1 → edit）。"""
    mapping = {"0": "easy", "1": "normal", "2": "hard", "3": "oni", "4": "edit"}
    # Strip _p1/_p2/_single suffix for lookup
    base = str(name).lower()
    for suffix in ("_p1", "_p2", "_single"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    return mapping.get(base, base)


# ===========================================================================
# API 调用
# ===========================================================================

def  analyze_tja(tja_content: str, unit: str = "ms") -> dict:
    """将 TJA 内容 POST 到 TJAAnalyzer API，返回完整分析 JSON。"""
    url = f"{API_URL}/?unit={unit}&longNoteHandling=skip"
    data = tja_content.encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "taiko-constants-workflow/1.0",
        },
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
        result = json.loads(body)

    if "error" in result:
        raise RuntimeError(f"API error: {result['error']}")

    return result


# ===========================================================================
# 数据提取与预处理
# ===========================================================================

def extract_intervals(branch_gaps):
    """从 per-bar gap 数组中提取 flat 间隔数组（过滤 null 和 <=0 值）。"""
    intervals = []
    if not branch_gaps or not isinstance(branch_gaps, list):
        return intervals
    for bar in branch_gaps:
        if bar and isinstance(bar, list):
            for g in bar:
                if g is not None and g > 0:
                    intervals.append(g)
    return intervals

def count_total_notes(branch_gaps, note_types=None):
    """统计谱面 judgeable note 总数。"""
    if isinstance(note_types, list):
        return sum(1 for v in note_types if v in (1, 2, 3, 4))
    total = 0
    if branch_gaps and isinstance(branch_gaps, list):
        for bar in branch_gaps:
            if bar and isinstance(bar, list):
                total += len(bar)
    return total


# ===========================================================================
# 各维度定数计算
# ===========================================================================

def calc_roll_equivalent(intervals, note_types=None):
    """计算滚奏等效值。"""
    if not intervals:
        return 0, [[], [], 0]

    an = intervals.copy()
    results = _roll.process(an, note_types)

    if not results:
        return 0, [[], [], 0]

    candidates = []
    if isinstance(results, tuple) and len(results) == 3:
        candidates = [results]
    elif isinstance(results, list):
        for item in results:
            if isinstance(item, (tuple, list)) and len(item) == 3:
                candidates.append(item)

    if not candidates:
        return 0, [[], [], 0]

    max_c = max((item[2] for item in candidates), default=0)
    primary = candidates[0]
    return max_c, [
        primary[0] if isinstance(primary[0], list) else [],
        primary[1] if isinstance(primary[1], list) else [],
        primary[2] if isinstance(primary[2], (int, float)) else 0,
    ]


def calculate_difficulty_ratings(branch_gaps, note_types=None):
    """计算单个谱面的全部定数。"""
    intervals = extract_intervals(branch_gaps)
    total_notes = count_total_notes(branch_gaps, note_types)

    ratings = {
        "stamina": 0.0,
        "complex": 0.0,
        "complexRatio": 0.0,
        "rhythm": 0.0,
        "rhythmRatio": 0.0,
        "rhythmOverall": 0.0,
        "rhythmRatioOverall": 0.0,
        "speed": 0.0,
        "speed95": 0.0,
        "rollEquivalent": 0.0,
        "rollEquivalentOutputs": [[], [], 0],
        "burst": 0.0,
        "totalNotes": total_notes,
    }

    if not intervals:
        return ratings

    # 体力
    try:
        _, _, ratings["stamina"] = _stamina.calculate_result(intervals)
    except Exception:
        pass

    # 复合
    try:
        ratings["complex"], ratings["complexRatio"] = _compound.calculate_complete_compound_difficulty(intervals, note_types)
    except Exception:
        pass

    # 节奏 (分支版)
    try:
        ratings["rhythm"], ratings["rhythmRatio"] = _rhythm.compute_final_rhythm_difficulty(intervals)
    except Exception:
        pass

    # 节奏 (整体版)
    try:
        ratings["rhythmOverall"], ratings["rhythmRatioOverall"] = _rhythm_overall.compute_final_rhythm_difficulty(intervals)
    except Exception:
        pass

    # 手速 (75线)
    try:
        ratings["speed"] = _speed.compute_weighted_average(intervals)
    except Exception:
        pass

    # 手速95 (99线)
    try:
        ratings["speed95"] = _speed95.compute_weighted_average(intervals)
    except Exception:
        pass

    # 滚奏等效
    try:
        roll_value, roll_outputs = calc_roll_equivalent(intervals, note_types)
        ratings["rollEquivalent"] = roll_value
        ratings["rollEquivalentOutputs"] = roll_outputs
    except Exception:
        pass

    # 爆发
    try:
        ratings["burst"] = _burst.compute_weighted_average(intervals)
    except Exception:
        pass

    return ratings


# ===========================================================================
# 分析结果处理
# ===========================================================================

def process_analysis(analysis: dict) -> list:
    """处理 API 返回的分析数据，为所有谱面计算定数。
    API 已返回扁平结构：每个 key 直接对应一个谱面。
    STYLE:Double 的 p1/p2/single 已打平为 course_side 键。"""
    charts = []
    courses = analysis.get("courses", {})
    note_types_all = analysis.get("noteTypes", {})

    for course_name, chart_gaps in courses.items():
        if not isinstance(chart_gaps, dict):
            continue

        normalized_diff = normalize_difficulty_name(course_name)
        chart_note_types = note_types_all.get(course_name, {})
        if not isinstance(chart_note_types, dict):
            chart_note_types = {}

        for branch_type, branch_gaps in chart_gaps.items():
            if not isinstance(branch_gaps, list):
                continue

            note_types = chart_note_types.get(branch_type, [])
            ratings = calculate_difficulty_ratings(branch_gaps, note_types)

            charts.append({
                "course": course_name,
                "difficulty": normalized_diff,
                "baseDifficulty": "oni" if normalized_diff == "edit" else normalized_diff,
                "isUra": normalized_diff == "edit",
                "branchType": branch_type,
                "ratings": ratings,
            })

    return charts


# ===========================================================================
# 输出格式化
# ===========================================================================

def format_output(charts: list, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(charts, indent=2, ensure_ascii=False)

    lines = ["=" * 80, "  TJA 定数计算结果", "=" * 80]

    for chart in charts:
        r = chart["ratings"]
        branch_label = f" [{chart['branchType']}]" if chart["branchType"] != "unbranched" else ""
        lines.append(f"\n---{chart['course']}{branch_label}---")
        lines.append(f"  Note 总数:        {r['totalNotes']:>10}")
        lines.append(f"  体力 (stamina):    {r['stamina']:>10.4f}")
        lines.append(f"  复合 (complex):    {r['complex']:>10.4f}  (占比: {r['complexRatio']:.6f})")
        lines.append(f"  节奏 (rhythm):     {r['rhythm']:>10.4f}  (占比: {r['rhythmRatio']:.6f})")
        lines.append(f"  节奏整体 (rhythmO):{r['rhythmOverall']:>10.4f}  (占比: {r['rhythmRatioOverall']:.6f})")
        lines.append(f"  手速 (speed):      {r['speed']:>10.4f}")
        lines.append(f"  手速95 (speed95):  {r['speed95']:>10.4f}")
        lines.append(f"  滚奏等效 (rollEq): {r['rollEquivalent']:>10.4f}")
        lines.append(f"  爆发 (burst):      {r['burst']:>10.4f}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


# ===========================================================================
# CLI
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="本地 TJA 定数计算工作流 — 调用 TJAAnalyzer API 分析谱面后计算定数",
        epilog=f"API 地址: {API_URL}\n可通过环境变量 TJA_API_URL 覆盖",
    )
    parser.add_argument("file", nargs="?", help="TJA 谱面文件路径")
    parser.add_argument("--unit", choices=["ms", "measures"], default="ms", help="间隔单位 (默认: ms)")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取 TJA 内容")

    args = parser.parse_args()

    try:
        # 读取 TJA 内容
        if args.stdin:
            tja_content = sys.stdin.read()
        elif args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                tja_content = f.read()
        else:
            parser.print_help()
            sys.exit(1)

        if not tja_content.strip():
            print("Error: empty TJA content", file=sys.stderr)
            sys.exit(1)

        # 步骤 1: 调用 API 分析谱面
        print(f"[请求 API: {API_URL}]", file=sys.stderr)
        analysis = analyze_tja(tja_content, args.unit)

        # 步骤 2: 计算定数
        print("[计算定数中...]", file=sys.stderr)
        charts = process_analysis(analysis)

        if not charts:
            print("Warning: 未找到可计算的谱面数据", file=sys.stderr)

        # 步骤 3: 输出结果
        print(format_output(charts, as_json=args.json))

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        print(f"API HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"无法连接 API ({API_URL}): {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

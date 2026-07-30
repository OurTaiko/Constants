#!/usr/bin/env python3
"""从 constants.json 提取 edit、oni 和 hard 难度的定数字段。

用法：
    uv run extract_song_constants.py
    uv run extract_song_constants.py constants.json -o song_constants.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("constants.json")
DEFAULT_OUTPUT = Path("song_constants.json")

ALLOWED_BRANCH_TYPES = {"master", "unbranched"}

COURSE_PRIORITIES = {
    "edit": ("edit", "edit_single", "edit_p1"),
    "oni": ("oni", "oni_single", "oni_p1"),
    "hard": ("hard", "hard_single", "hard_p1"),
}

OUTPUT_FIELDS = (
    "sub_constant_1",
    "main_constant",
    "sub_constant_2",
    "stamina",
    "handspeed",
    "burst",
    "complex",
    "rhythm",
    "totalNotes",
)


def choose_chart(
    charts: list[dict[str, Any]],
    course_priority: tuple[str, ...],
) -> dict[str, Any] | None:
    """从有效分支中，按 course 优先级选择第一张谱面。"""
    valid_charts = [
        chart
        for chart in charts
        if chart.get("branchType") in ALLOWED_BRANCH_TYPES
    ]

    for course in course_priority:
        for chart in valid_charts:
            if chart.get("course") == course:
                return chart
    return None


def extract_song_constants(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """将 constants.json 的完整数据转换为目标结构。"""
    songs = data.get("songs")
    if not isinstance(songs, dict):
        raise ValueError("输入 JSON 的 songs 字段必须是一个 JSON 对象")

    result: dict[str, dict[str, Any]] = {}
    for song_id, song in songs.items():
        if not isinstance(song, dict):
            raise ValueError(f"songs[{song_id!r}] 必须是一个 JSON 对象")

        charts = song.get("charts", [])
        if not isinstance(charts, list):
            raise ValueError(f"songs[{song_id!r}].charts 必须是一个 JSON 数组")
        if not all(isinstance(chart, dict) for chart in charts):
            raise ValueError(
                f"songs[{song_id!r}].charts 中的每一项都必须是 JSON 对象"
            )

        song_result: dict[str, Any] = {}
        for difficulty, priorities in COURSE_PRIORITIES.items():
            chart = choose_chart(charts, priorities)
            if chart is not None:
                song_result[difficulty] = {
                    field: chart[field] for field in OUTPUT_FIELDS if field in chart
                }

        result[str(song_id)] = song_result

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 constants.json 提取 edit、oni 和 hard 难度定数"
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"输入 JSON 路径（默认：{DEFAULT_INPUT}）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"输出 JSON 路径（默认：{DEFAULT_OUTPUT}）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with args.input.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("输入 JSON 的根节点必须是一个 JSON 对象")

    result = extract_song_constants(data)
    with args.output.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print(f"已提取 {len(result)} 首歌曲的数据：{args.output}")


if __name__ == "__main__":
    main()

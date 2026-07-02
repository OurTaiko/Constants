#!/usr/bin/env python3
"""
批量定数计算工作流
==================

从 ese_mapping 获取 id→相对路径映射，在本地 Songs 目录读取每首 tja，
经 tja-analysis API 算出六维原始数据，再按 workflow.md 流程动态校准并
计算所有谱面的最终定数，结果以 JSON 存到根目录。

数据流：
    ese_mapping ──► (id → 相对路径)[]
                       │  ThreadPoolExecutor 并发
        ┌──────────────┴──────────────┐
        │ 读 tja (编码回退)            │
        │ h = sha256(content)          │
        │ 缓存命中(.cache/{id}.json)?  │
        │   是 → 用缓存的 analysis     │
        │   否 → POST API → 落盘缓存   │
        │ analyzer.process(analysis)   │
        └──────────────┬──────────────┘
                       │
        收集全部 ChartRawData → calibrate → 13 个全局参考值
        RatingPipeline(ref) → compute_all → 最终 8 字段
                       │
        写 constants.json

用法:
    uv run batch_workflow.py                       # 全量
    uv run batch_workflow.py --limit 5 --workers 4 # 小批量冒烟
    uv run batch_workflow.py --refresh             # 清空缓存重来
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import threading
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rating import ChartRawData, RatingPipeline
from tja_analysis import Chart, TJAChartAnalyzer

# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------
DEFAULT_BASE_DIR = (
    r"E:\Programs\TaikoNijiiroDondaEX Ver4.1\TaikoNijiiroDondaEX Ver4.1\Songs"
)
DEFAULT_MAPPING_URL = "https://cdn.ourtaiko.org/api/ese_mapping"
DEFAULT_CACHE_DIR = ".cache"
DEFAULT_OUTPUT = "constants.json"
DEFAULT_WORKERS = 8

# TJA 文件可能的编码，按优先级回退
DECODINGS = ("utf-8-sig", "utf-8", "shift_jis", "gb18030", "latin-1")


# ===========================================================================
# 工具函数
# ===========================================================================


def fetch_ese_mapping(url: str) -> Dict[str, str]:
    """从 ese_mapping API 拉取 id → 相对路径 映射。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "taiko-constants-batch/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"ese_mapping 返回格式异常: {type(data)}")
    return data


def resolve_tja_path(base_dir: str, relative: str) -> Path:
    """把映射里的相对路径（含反斜杠）拼到 Songs 根目录上。"""
    return Path(base_dir) / relative


def decode_tja(raw: bytes) -> str:
    """按优先级尝试多种编码解码 tja 字节流。"""
    for enc in DECODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")  # 兜底，永不抛错


def content_hash(text: str) -> str:
    """tja 内容的 sha256，用作缓存键。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_cache_atomic(cache_path: Path, obj: dict) -> None:
    """原子写缓存：先写临时文件再 os.replace，避免崩溃产生半截文件。"""
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, cache_path)


def get_analysis(
    analyzer: TJAChartAnalyzer,
    song_id: str,
    tja_path: Path,
    cache_dir: Path,
    use_cache: bool,
) -> Tuple[dict, str]:
    """获取某首歌的 API 原始分析结果。

    返回 (analysis, source)，source 为 "cache" 或 "api"。
    缓存键为 tja 文件内容的 sha256；文件变动即自动重新请求。
    """
    content = decode_tja(tja_path.read_bytes())
    h = content_hash(content)

    cache_path = cache_dir / f"{song_id}.json"
    if use_cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("hash") == h:
                return cached["analysis"], "cache"
        except (json.JSONDecodeError, KeyError):
            pass  # 缓存损坏，重新请求

    analysis = analyzer.analyze(content)
    save_cache_atomic(cache_path, {"hash": h, "analysis": analysis})
    return analysis, "api"


def process_song(
    analyzer: TJAChartAnalyzer,
    song_id: str,
    relative: str,
    base_dir: str,
    cache_dir: Path,
    use_cache: bool,
) -> dict:
    """处理单首歌：读 tja → 缓存/API → 原始定数。失败不抛异常，返回 error 字段。"""
    try:
        if not relative or not relative.strip():
            return {"id": song_id, "path": relative, "error": "ese_mapping 中路径为空（无效 id）"}
        tja_path = resolve_tja_path(base_dir, relative)
        if not tja_path.exists():
            return {"id": song_id, "path": relative, "error": "tja 文件不存在"}

        analysis, source = get_analysis(
            analyzer, song_id, tja_path, cache_dir, use_cache
        )
        charts = analyzer.process(analysis)
        if not charts:
            return {
                "id": song_id,
                "path": relative,
                "error": "无可用谱面分支",
                "source": source,
            }
        return {
            "id": song_id,
            "path": relative,
            "charts": charts,
            "source": source,
        }
    except Exception as e:  # noqa: BLE001 — 批处理需容错，单首失败不中断
        return {"id": song_id, "path": relative, "error": f"{type(e).__name__}: {e}"}


# ===========================================================================
# 主流程
# ===========================================================================


def build_chart_entry(chart: Chart, result) -> dict:
    """组装单条谱面的输出（元信息 + 最终 8 字段 + 原始六维）。"""
    ratings = chart.ratings
    return {
        "course": chart.course,
        "difficulty": chart.difficulty,
        "branchType": chart.branch_type,
        "sub_constant_1": result.sub_constant_1,
        "main_constant": result.main_constant,
        "sub_constant_2": result.sub_constant_2,
        "stamina": result.stamina,
        "handspeed": result.handspeed,
        "burst": result.burst,
        "complex": result.complex,
        "rhythm": result.rhythm,
        "totalNotes": ratings.total_notes,
        "raw": {
            "stamina": ratings.stamina,
            "speed": ratings.speed,
            "burst": ratings.burst,
            "complex": ratings.complex,
            "complexRatio": ratings.complex_ratio,
            "rhythm": ratings.rhythm,
            "rhythmRatio": ratings.rhythm_ratio,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量定数计算工作流 — ese_mapping → 本地 tja → 最终定数 JSON",
    )
    parser.add_argument("--base-dir", default=DEFAULT_BASE_DIR, help="Songs 根目录")
    parser.add_argument("--mapping-url", default=DEFAULT_MAPPING_URL, help="ese_mapping API")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="缓存目录")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 JSON 路径")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="并发线程数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 首（0=全部）")
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存，强制全量请求")
    parser.add_argument("--refresh", action="store_true", help="清空缓存后运行")
    args = parser.parse_args()

    use_cache = not args.no_cache
    cache_dir = Path(args.cache_dir)

    # ---- 清空缓存 ----
    if args.refresh and cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir)
        print(f"[已清空缓存 {cache_dir}]", file=sys.stderr)

    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- 拉取映射 ----
    print(f"[拉取 ese_mapping: {args.mapping_url}]", file=sys.stderr)
    mapping = fetch_ese_mapping(args.mapping_url)
    items: List[Tuple[str, str]] = list(mapping.items())
    total = len(items)
    if args.limit > 0:
        items = items[: args.limit]
    print(
        f"[共 {total} 首，本次处理 {len(items)} 首，并发 {args.workers}]",
        file=sys.stderr,
    )

    # ---- 并发处理阶段一（读 tja + API + 原始定数）----
    analyzer = TJAChartAnalyzer()
    results_by_id: Dict[str, dict] = {}
    done = 0
    done_lock = threading.Lock()
    api_count = 0
    cache_count = 0

    def _work(item):
        return process_song(
            analyzer, item[0], item[1], args.base_dir, cache_dir, use_cache
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_work, item): item for item in items}
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            results_by_id[res["id"]] = res
            with done_lock:
                done += 1
                src = res.get("source")
                if src == "cache":
                    cache_count += 1
                elif src == "api":
                    api_count += 1
                tag = (
                    f"[{done}/{len(items)}] id={res['id']} "
                    f"{'失败: ' + res['error'] if 'error' in res else 'OK'}"
                )
                print(tag, file=sys.stderr)

    # ---- 收集成功的谱面，按原始顺序展开 ----
    flat: List[Tuple[str, Chart]] = []  # (song_id, chart)
    errors: List[dict] = []
    for song_id, relative in items:
        res = results_by_id.get(song_id)
        if res is None or "error" in res:
            errors.append(
                {
                    "id": song_id,
                    "path": relative,
                    "error": res.get("error", "未处理") if res else "未处理",
                }
            )
            continue
        for chart in res["charts"]:
            flat.append((song_id, chart))

    print(
        f"[阶段一完成] 谱面分支 {len(flat)} 个，失败 {len(errors)} 首 "
        f"(API {api_count} / 缓存 {cache_count})",
        file=sys.stderr,
    )

    if not flat:
        print("无可计算的谱面，终止。", file=sys.stderr)
        return 1

    # ---- 阶段二：动态校准 + 最终定数 ----
    all_data = [ChartRawData.from_chart(chart) for _, chart in flat]
    print("[动态校准全局参考值...]", file=sys.stderr)
    ref_values = RatingPipeline.calibrate(all_data)

    pipeline = RatingPipeline(ref_values)
    all_results = pipeline.compute_all([chart for _, chart in flat])

    # 按歌聚合结果
    charts_by_song: Dict[str, List[Tuple[Chart, object]]] = defaultdict(list)
    for (song_id, chart), result in zip(flat, all_results):
        charts_by_song[song_id].append((chart, result))

    # ---- 组装输出 ----
    songs_out: Dict[str, dict] = {}
    for song_id, relative in items:
        if song_id in charts_by_song:
            songs_out[song_id] = {
                "path": relative,
                "charts": [
                    build_chart_entry(c, r) for c, r in charts_by_song[song_id]
                ],
            }

    output = {
        "metadata": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "mapping_url": args.mapping_url,
            "base_dir": args.base_dir,
            "total_songs": total,
            "processed_songs": len(songs_out),
            "total_charts": len(flat),
            "failed": len(errors),
            "ref_values": ref_values,
        },
        "songs": songs_out,
        "errors": errors,
    }

    out_path = Path(args.output)
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[完成] 写入 {out_path.resolve()} "
        f"({len(songs_out)} 首 / {len(flat)} 谱面，失败 {len(errors)})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

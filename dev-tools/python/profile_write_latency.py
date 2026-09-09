#!/usr/bin/env python3
"""
写入耗时埋点测试脚本（独立工具，不影响现有写入代码）。

用途：
- 测量「add-message / commit / verify」各阶段耗时，定位写入慢的环节
- 只读 benchmark 数据 + 调用后端 API，不写 seen 目录、不碰正式输出目录
- 输出独立的耗时统计目录 benchmark-results/profile-latency/<时间戳>/

与现有程序的关系：
- 复用 generate_benchmark_memories.Backend / load_company（只 import，不修改）
- 不改动任何现有脚本逻辑

用法示例：
    python3 dev-tools/python/profile_write_latency.py \
        --server http://127.0.0.1:8366 \
        --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
        --companies C011 C012 \
        --session-limit 5 \
        --id-prefix zh-memory-v1 --source-client zh-profile-test
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from generate_benchmark_memories import Backend, load_company
from memind_shared import company_entries, load_env

DEFAULT_SERVER = "http://127.0.0.1:8366"
DEFAULT_BENCHMARK_DIR = "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"
DEFAULT_OUT_DIR = "benchmark-results/profile-latency"

# 不支持长 commit 时后端可配置超时；这里默认与正式写入一致
COMMIT_TIMEOUT = 900


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="写入耗时埋点测试（独立测试工具）")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--benchmark-dir", default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--source-client", default="zh-profile-test")
    parser.add_argument("--id-prefix", default="zh-memory-v1")
    parser.add_argument("--companies", nargs="*", default=[])
    parser.add_argument("--sessions", nargs="*", default=[])
    parser.add_argument("--session-limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="只统计 session 数量和对话长度，不发请求")
    parser.add_argument("--no-verify", action="store_true", help="commit 后不执行 verify 计时")
    return parser.parse_args()


def log(msg: str) -> None:
    print(msg, flush=True)


def add_message_timed(backend: Backend, user_id: str, agent_id: str, message: dict, source_client: str) -> float:
    """返回 add-message 单次耗时(秒)。"""
    t0 = time.time()
    backend.add_message(user_id, agent_id, message, source_client)
    return time.time() - t0


def commit_timed(backend: Backend, user_id: str, agent_id: str, source_client: str) -> float:
    """返回 commit 耗时(秒)，包含 LLM 抽取等后端处理。"""
    t0 = time.time()
    backend.commit(user_id, agent_id, source_client)
    return time.time() - t0


def verify_timed(backend: Backend, user_id: str, agent_id: str, source_client: str) -> tuple[float, int]:
    """返回 verify 耗时(秒) 和 item 条数。"""
    t0 = time.time()
    items = backend.query_items(user_id, agent_id, source_client, limit=10)
    return time.time() - t0, len(items)


def stage_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0, "sum": 0.0}
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "sum": round(sum(values), 2),
    }


def main() -> None:
    args = parse_args()
    load_env()

    companies = [
        c
        for c in company_entries()
        if not args.companies or c["company_id"] in [x.upper() for x in args.companies]
    ]
    if not companies:
        log("未匹配到公司")
        sys.exit(1)

    selected: list[dict] = []
    for company in companies:
        company_file = Path(args.benchmark_dir) / company["file_name"]
        if not company_file.exists():
            log(f"[WARN] 缺少数据文件: {company_file}")
            continue
        sessions = load_company(company_file)
        sessions.sort(key=lambda s: s["started_at"])
        if args.sessions:
            want = [s.upper() for s in args.sessions]
            sessions = [s for s in sessions if s["session_id"] in want]
        if args.session_limit > 0:
            sessions = sessions[: args.session_limit]
        selected.append({**company, "sessions": sessions})

    if args.dry_run:
        log("=== dry-run：只统计 session 数与对话长度，不发请求 ===")
        grand_total = 0
        for c in selected:
            turns_total = sum(len(s["turns"]) for s in c["sessions"])
            grand_total += turns_total
            log(
                f"{c['company_id']}: sessions={len(c['sessions'])} 总对话轮数={turns_total} "
                f"平均轮数/会话={turns_total / len(c['sessions']) if c['sessions'] else 0:.1f}"
            )
        log(f"合计 sessions={sum(len(c['sessions']) for c in selected)} 轮数={grand_total}")
        return

    out_dir = Path(args.out_dir) / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []

    with Backend(args.server, 5, 180) as backend:
        try:
            log(f"Health: {backend.health()}")
        except Exception as ex:
            log(f"[FATAL] 后端健康检查失败: {ex}")
            sys.exit(1)

        for company in selected:
            company_id = company["company_id"]
            user_id = f"{args.id_prefix}-{company_id}"
            agent_id = f"{args.id_prefix}-{company_id}-agent"

            log("=" * 90)
            log(f"Company: {company_id}  sessions={len(company['sessions'])}")
            for session in company["sessions"]:
                session_id = session["session_id"]
                turns = session["turns"]
                source_client = f"{args.source_client}-{company_id}-{session_id}"

                add_wall = 0.0
                for turn in turns:
                    text = f"[profile] {turn['content']}"
                    message = {
                        "role": turn["role"],
                        "content": [{"type": "text", "text": text}],
                        "timestamp": turn["timestamp"],
                    }
                    add_wall += add_message_timed(backend, user_id, agent_id, message, source_client)

                commit_wall = commit_timed(backend, user_id, agent_id, source_client)

                verify_wall = 0.0
                verify_items = 0
                if not args.no_verify:
                    try:
                        verify_wall, verify_items = verify_timed(backend, user_id, agent_id, source_client)
                    except Exception as ex:
                        verify_wall = -1.0
                        verify_items = -1

                total = add_wall + commit_wall + (verify_wall if verify_wall > 0 else 0)
                log(
                    f"[{session_id}] turns={len(turns):>3} add={add_wall:.1f}s "
                    f"commit={commit_wall:.1f}s verify={verify_wall:.1f}s "
                    f"items={verify_items} total={total:.1f}s"
                )
                rows.append(
                    {
                        "company_id": company_id,
                        "session_id": session_id,
                        "turns": len(turns),
                        "add_seconds": round(add_wall, 2),
                        "commit_seconds": round(commit_wall, 2),
                        "verify_seconds": round(verify_wall, 2),
                        "verify_items": verify_items,
                        "total_seconds": round(total, 2),
                    }
                )

    # 汇总统计
    log("\n" + "=" * 90)
    log("阶段耗时汇总（秒）")
    log(f"  add-message : {stage_stats([r['add_seconds'] for r in rows])}")
    log(f"  commit      : {stage_stats([r['commit_seconds'] for r in rows])}")
    log(f"  verify      : {stage_stats([r['verify_seconds'] for r in rows])}")
    log(f"  total       : {stage_stats([r['total_seconds'] for r in rows])}")

    if rows:
        avg_add = statistics.mean(r["add_seconds"] for r in rows)
        avg_commit = statistics.mean(r["commit_seconds"] for r in rows)
        avg_total = statistics.mean(r["total_seconds"] for r in rows) or 1.0
        log("\n阶段占比（基于各自平均值）：")
        log(f"  add-message 占 {avg_add / avg_total * 100:.1f}%")
        log(f"  commit      占 {avg_commit / avg_total * 100:.1f}%")

    result = {
        "server": args.server,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "stage": {
                "add": stage_stats([r["add_seconds"] for r in rows]),
                "commit": stage_stats([r["commit_seconds"] for r in rows]),
                "verify": stage_stats([r["verify_seconds"] for r in rows]),
                "total": stage_stats([r["total_seconds"] for r in rows]),
            }
        },
        "rows": rows,
    }
    result_path = out_dir / "profile.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n结果已写入: {result_path}")


if __name__ == "__main__":
    main()
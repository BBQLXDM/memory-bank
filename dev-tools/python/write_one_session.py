#!/usr/bin/env python3
"""
单 session 直写脚本 - 绕过主脚本跳过逻辑，独立写入指定 session。

用法示例：
    python3 dev-tools/python/write_one_session.py \
        --server http://127.0.0.1:8366 \
        --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
        --company C017 \
        --session S003 \
        --id-prefix benchmark-v107 \
        --source-client benchmark-v107

每步都会打印 HTTP 状态码和响应，方便定位问题。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DEFAULT_SERVER = os.environ.get("MEMIND_SERVER", "http://127.0.0.1:8366")
DEFAULT_BENCHMARK_DIR = os.environ.get(
    "BENCHMARK_DIR", "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"
)

from memind_shared import load_companies

COMPANIES = load_companies()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a single session directly")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--benchmark-dir", default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--company", required=True, help="C001-C020")
    parser.add_argument("--session", required=True, help="session id, e.g. S003")
    parser.add_argument("--id-prefix", default="benchmark-v107")
    parser.add_argument("--source-client", default="benchmark-v107")
    parser.add_argument("--commit-timeout", type=int, default=900, help="commit 超时(秒)")
    parser.add_argument("--add-timeout", type=int, default=180, help="add-message 超时(秒)")
    parser.add_argument("--no-add", action="store_true", help="只 commit，不重新 add-message")
    return parser.parse_args()


def project_id_for(benchmark_dir: str) -> str:
    normalized = os.path.abspath(benchmark_dir)
    digest = hashlib_sha1(normalized)
    return f"{os.path.basename(normalized)}-{digest}"


def hashlib_sha1(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]


def load_session(benchmark_dir: str, company_id: str, session_id: str) -> tuple[dict, str]:
    file_name = f"{COMPANIES[company_id]}.json"
    path = Path(benchmark_dir) / file_name
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    sessions = data.get("context", {}).get("sessions", [])
    for s in sessions:
        if s.get("session_id") == session_id:
            return s, session_id
    # 大小写不敏感兜底（hash 型 session 名混合大小写，不能强转大写）
    for s in sessions:
        if str(s.get("session_id", "")).lower() == session_id.lower():
            canonical = str(s.get("session_id"))
            return s, canonical
    raise SystemExit(f"[ERROR] session {session_id} not found in {path}")


def normalize_turn(t: dict) -> dict:
    role = str(t.get("role", "user")).lower()
    ts = t.get("timestamp")
    if not ts:
        ts = "1970-01-01T00:00:00Z"  # 与 Java 版 Instant.EPOCH 一致
    else:
        try:
            from datetime import datetime as _dt, timezone as _tz
            dt = _dt.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            ts = dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return {
        "role": "ASSISTANT" if role == "assistant" else "USER",
        "content": str(t.get("content", "")),
        "timestamp": ts,
    }


def main() -> None:
    args = parse_args()
    company_id = args.company.upper()
    if company_id not in COMPANIES:
        raise SystemExit(f"[ERROR] unknown company {company_id}, choose from {list(COMPANIES)}")

    project_id = project_id_for(args.benchmark_dir)
    user_id = f"{args.id_prefix}-{company_id}"
    agent_id = f"{args.id_prefix}-{company_id}-agent"

    session, session_id = load_session(args.benchmark_dir, company_id, args.session)
    session_source_client = f"{args.source_client}-{company_id}-{session_id}"

    print(f"Server: {args.server}")
    print(f"ProjectId: {project_id}")
    print(f"Company: {company_id}")
    print(f"Session: {session_id}")
    print(f"userId: {user_id}")
    print(f"agentId: {agent_id}")
    print(f"session sourceClient: {session_source_client}")
    print("=" * 80)

    turns = session.get("turns", [])
    print(f"Loaded session {session_id}: {len(turns)} turns, started_at={session.get('started_at')}")
    for i, t in enumerate(turns, 1):
        role = str(t.get("role", "?"))
        content = str(t.get("content", ""))
        print(f"  turn {i}: [{role}] {content[:60]}{'...' if len(content) > 60 else ''}")

    base = args.server.rstrip("/")
    headers = {"Content-Type": "application/json"}

    # 1. health check
    try:
        resp = requests.get(f"{base}/open/v1/health", timeout=(5, 10))
        print(f"[health] status={resp.status_code} {resp.text[:120]}")
    except Exception as ex:
        print(f"[health] FAILED: {ex}")
        sys.exit(1)

    # 2. add-message（可选）
    if not args.no_add:
        for i, t in enumerate(turns, 1):
            text = f"[project={project_id}][session={session_id}] {t['content']}"
            message = {
                "role": normalize_turn(t)["role"],
                "content": [{"type": "text", "text": text}],
                "timestamp": normalize_turn(t)["timestamp"],
            }
            payload = {
                "userId": user_id,
                "agentId": agent_id,
                "message": message,
                "sourceClient": session_source_client,
            }
            try:
                resp = requests.post(
                    f"{base}/open/v1/memory/sync/add-message",
                    json=payload,
                    headers=headers,
                    timeout=(5, args.add_timeout),
                )
                print(
                    f"[add-message {i}/{len(turns)}] status={resp.status_code} "
                    f"body={resp.text[:200]}"
                )
            except Exception as ex:
                print(f"[add-message {i}/{len(turns)}] FAILED: {ex}")
                sys.exit(1)
    else:
        print("[add-message] skipped (--no-add)")

    # 3. commit
    payload = {"userId": user_id, "agentId": agent_id, "sourceClient": session_source_client}
    print(f"[commit] sending (timeout={args.commit_timeout}s) ...")
    t0 = time.time()
    try:
        resp = requests.post(
            f"{base}/open/v1/memory/sync/commit",
            json=payload,
            headers=headers,
            timeout=(5, args.commit_timeout),
        )
        elapsed = time.time() - t0
        print(f"[commit] status={resp.status_code} elapsed={elapsed:.1f}s body={resp.text[:300]}")
    except Exception as ex:
        elapsed = time.time() - t0
        print(f"[commit] FAILED after {elapsed:.1f}s: {ex}")
        sys.exit(1)

    # 4. verify - 查询该 sourceClient 是否有 items
    print("=" * 80)
    print("[verify] querying items ...")
    query = {
        "userId": user_id,
        "agentId": agent_id,
        "sourceClients": [session_source_client],
        "limit": 5,
    }
    try:
        resp = requests.post(
            f"{base}/open/v1/memory/items/query", json=query, headers=headers, timeout=(5, 60)
        )
        print(f"[verify] status={resp.status_code}")
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        print(f"[verify] items found: {len(items)}")
        for it in items[:5]:
            print(f"  - {json.dumps(it, ensure_ascii=False)[:200]}")
        if items:
            print("[verify] SUCCESS - data persisted")
        else:
            print("[verify] WARNING - no items found yet (backend may still be processing)")
    except Exception as ex:
        print(f"[verify] FAILED: {ex}")

    print("Done.")


if __name__ == "__main__":
    main()

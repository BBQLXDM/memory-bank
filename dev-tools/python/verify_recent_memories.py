#!/usr/bin/env python3
"""
验证最近写入的记忆是否真实有效。

用法：
    python3 dev-tools/python/verify_recent_memories.py \
        --server http://127.0.0.1:8366 \
        --companies C017 C018 C020 \
        --last 5

对每个公司按 seen 记录修改时间找最近成功写入的 N 个 session，
逐个调用 /memory/items/query 验证服务器上是否真有数据，并打印 item 内容。
判定：item 数量 > 0 且文本长度合理（>50字符）且不包含错误标记 => 有效。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_SERVER = os.environ.get("MEMIND_SERVER", "http://127.0.0.1:8366")
DEFAULT_SEEN_DIR = Path(os.environ.get("SEEN_DIR", "benchmark-results/memory-generation-seen"))
DEFAULT_ID_PREFIX = os.environ.get("ID_PREFIX", "benchmark-v107")

ERROR_MARKERS = [
    "internal error", "internal_error", "extraction failed", "server error",
    "exception", "timeout", "quota exhausted", "out of quota",
]


def post_json(url: str, body: dict, timeout: int = 30):
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload) if payload else {}
    except HTTPError as exc:
        payload = exc.read().decode("utf-8") if exc.fp else ""
        try:
            return exc.code, json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            return exc.code, {"error": payload}
    except URLError as exc:
        return 0, {"error": str(exc)}


def query_items(server: str, user_id: str, agent_id: str, source_client: str):
    payload = {
        "userId": user_id,
        "agentId": agent_id,
        "sourceClients": [source_client],
        "limit": 10,
    }
    status, response = post_json(f"{server}/open/v1/memory/items/query", payload)
    if status >= 400:
        return status, None
    result = response.get("data") or response.get("result") or response
    return status, result.get("items", [])


def text_of(item: dict) -> str:
    for key in ("text", "content", "caption"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(item, ensure_ascii=False)


def is_valid_item(text: str) -> tuple[bool, str]:
    if len(text) < 50:
        return False, f"文本过短({len(text)}字符)"
    lower = text.lower()
    for marker in ERROR_MARKERS:
        if marker in lower:
            return False, f"包含错误标记: {marker}"
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="验证最近写入的记忆是否有效")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--seen-dir", default=str(DEFAULT_SEEN_DIR))
    parser.add_argument("--companies", nargs="*", default=["C017", "C018", "C020"])
    parser.add_argument("--last", type=int, default=5, help="每公司检查最近 N 个成功写入的 session")
    parser.add_argument("--id-prefix", default=DEFAULT_ID_PREFIX)
    args = parser.parse_args()

    seen_dir = Path(args.seen_dir)
    total_valid = 0
    total_checked = 0

    for company_id in args.companies:
        company_dir = seen_dir / company_id
        if not company_dir.exists():
            print(f"[C{company_id}] seen 目录不存在: {company_dir}")
            continue

        # 找最近修改的 success 记录，按 mtime 倒序
        candidates = []
        for path in company_dir.glob("*.response.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(record.get("status", "")).lower() == "success":
                candidates.append((path.stat().st_mtime, path, record))

        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[: args.last]

        user_id = f"{args.id_prefix}-{company_id}"
        agent_id = f"{args.id_prefix}-{company_id}-agent"

        print("=" * 100)
        print(f"Company: {company_id}  最近 {len(selected)} 个成功写入的 session")
        print(f"userId: {user_id}  agentId: {agent_id}")

        for mtime, path, record in selected:
            session_id = record.get("sessionId", "?")
            source_client = record.get("sourceClient", f"{args.id_prefix}-{company_id}-{session_id}")

            import datetime
            ts = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

            http, items = query_items(args.server, user_id, agent_id, source_client)
            if http != 200:
                print(f"[{ts}] {session_id}  查询失败 HTTP={http}")
                continue
            if not items:
                print(f"[{ts}] {session_id}  ⚠ 服务器上无 item（可能未真正写入）")
                continue

            # 校验所有 items
            checks = [is_valid_item(text_of(it)) for it in items]
            all_ok = all(ok for ok, _ in checks) and bool(items)
            first_ok, first_reason = checks[0] if checks else (False, "无 item")

            print(f"[{ts}] {session_id}  items={len(items)}  {'✅ 有效' if all_ok else '⚠ 有问题'}")
            for it in items[:2]:
                t = text_of(it)
                print(f"      - {t[:130]}")
            if not all_ok:
                print(f"      ⚠ 原因: {first_reason or '第一条文本为空'}")

            total_checked += 1
            if all_ok:
                total_valid += 1

        print()

    print("=" * 100)
    print(f"检查 {total_checked} 个 session，有效 {total_valid} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
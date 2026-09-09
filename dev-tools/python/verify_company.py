#!/usr/bin/env python3
"""
全量验证某公司所有 session 的记忆是否真实有效。

对 seen 目录中每条成功记录，逐个调用 /memory/items/query 验证服务器上是否有 item，
并检查 item 文本质量（非空、足够长、无错误标记）。

输出三类统计：
- ✅ 有效：item 存在且文本正常
- ⚠ 部分问题：有 item 但个别文本为空/过短/含错误标记
- ❌ 无效：服务器无 item（假成功）

用法：
    python3 dev-tools/python/verify_company.py --companies C017
    python3 dev-tools/python/verify_company.py --companies C017 C018 --min-items 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_SERVER = "http://127.0.0.1:8366"
DEFAULT_SEEN_DIR = "benchmark-results/memory-generation-seen"
DEFAULT_ID_PREFIX = "benchmark-v107"

ERROR_MARKERS = [
    "internal error", "internal_error", "extraction failed", "server error",
    "exception", "timeout", "quota exhausted", "out of quota",
]


def post_json(url: str, body: dict, timeout: int = 20):
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


def query_items(server: str, user_id: str, agent_id: str, source_client: str, limit: int = 20):
    payload = {
        "userId": user_id,
        "agentId": agent_id,
        "sourceClients": [source_client],
        "limit": limit,
    }
    status, response = post_json(f"{server}/open/v1/memory/items/query", payload)
    if status != 200:
        return status, None, response
    result = response.get("data") or response.get("result") or response
    return status, result.get("items", []), None


def text_of(item: dict) -> str:
    for key in ("text", "content", "caption"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(item, ensure_ascii=False)


def item_quality(text: str) -> tuple[bool, str]:
    """返回 (是否合格, 原因)。"""
    if not text:
        return False, "文本为空"
    if len(text) < 12:
        return False, f"文本过短({len(text)}字符)"
    lower = text.lower()
    for marker in ERROR_MARKERS:
        if marker in lower:
            return False, f"含错误标记: {marker}"
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="全量验证某公司的记忆是否有效")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--seen-dir", default=DEFAULT_SEEN_DIR)
    parser.add_argument("--companies", nargs="*", default=["C017"])
    parser.add_argument("--id-prefix", default=DEFAULT_ID_PREFIX)
    parser.add_argument("--min-items", type=int, default=1, help="有效需要的最少 item 数")
    parser.add_argument("--show-items", action="store_true", help="打印每个 session 的 item 文本")
    args = parser.parse_args()

    seen_dir = Path(args.seen_dir)

    for company_id in args.companies:
        company_dir = seen_dir / company_id
        if not company_dir.exists():
            print(f"[{company_id}] seen 目录不存在: {company_dir}")
            continue

        user_id = f"{args.id_prefix}-{company_id}"
        agent_id = f"{args.id_prefix}-{company_id}-agent"

        records = []
        for path in sorted(company_dir.glob("*.response.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(record.get("status", "")).lower() != "success":
                continue
            session_id = record.get("sessionId", path.stem.replace(".response", ""))
            records.append((session_id, record.get("sourceClient", f"{args.id_prefix}-{company_id}-{session_id}"), path))

        print("=" * 100)
        print(f"Company: {company_id}  成功记录: {len(records)}")
        print(f"userId: {user_id}")

        valid = []
        partial = []
        invalid = []
        query_fail = []

        for session_id, source_client, path in records:
            http, items, err = query_items(args.server, user_id, agent_id, source_client)
            if http != 200:
                query_fail.append((session_id, f"HTTP={http}"))
                continue
            if not items:
                invalid.append((session_id, "无 item"))
                continue

            qualities = [item_quality(text_of(it)) for it in items]
            bad_count = sum(1 for ok, _ in qualities if not ok)
            if bad_count == 0:
                valid.append((session_id, len(items)))
            else:
                reasons = [r for ok, r in qualities if not ok]
                partial.append((session_id, len(items), bad_count, reasons[:3]))

        print(f"✅ 有效: {len(valid)}")
        print(f"⚠ 部分问题: {len(partial)}")
        print(f"❌ 无效(假成功): {len(invalid)}")
        if query_fail:
            print(f"❓ 查询失败: {len(query_fail)}")

        if valid:
            print("\n--- 有效 session ---")
            print(", ".join(f"{s}({n}条)" for s, n in valid))
        if partial:
            print("\n--- 部分问题 session ---")
            for s, n, bad, reasons in partial:
                print(f"  {s}: items={n} 有{bad}条不合格 -> {reasons}")
                if args.show_items:
                    for it in query_items(args.server, user_id, agent_id, f"{args.id_prefix}-{company_id}-{s}")[1]:
                        t = text_of(it)
                        ok, why = item_quality(t)
                        print(f"    {'✅' if ok else '⚠'} {t[:100]}")
        if invalid:
            print("\n--- 无效 session（需重新写入）---")
            print(", ".join(s for s, _ in invalid))
        if query_fail:
            print("\n--- 查询失败 session ---")
            print(", ".join(f"{s}({r})" for s, r in query_fail))

        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
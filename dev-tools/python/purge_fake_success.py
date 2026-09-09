#!/usr/bin/env python3
"""
清除"假成功"的 seen 记录。

背景：LLM 额度耗尽时 commit 可能返回 SUCCESS 但 itemIds=[]（抽取失败），
seen 记录为 success 但服务器上根本没有 item。这类记录会让主脚本误以为
已写入并跳过，导致无法自动补齐。

本脚本：
1. 遍历 seen 目录中所有 status=success 的记录
2. 对每个 session 调 /memory/items/query 按 sourceClient 验证服务器是否有 item
3. 无 item（假成功）→ 打印并删除其 seen 记录（--dry-run 只列出不删除）
4. 删除后重跑主脚本即可自动补齐

用法：
    python3 dev-tools/python/purge_fake_success.py --dry-run        # 先看会删哪些
    python3 dev-tools/python/purge_fake_success.py                  # 实际删除
    python3 dev-tools/python/purge_fake_success.py --companies C018 # 只查某公司
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


def has_items(server: str, user_id: str, agent_id: str, source_client: str) -> bool:
    payload = {
        "userId": user_id,
        "agentId": agent_id,
        "sourceClients": [source_client],
        "limit": 1,
    }
    status, response = post_json(f"{server}/open/v1/memory/items/query", payload)
    if status != 200:
        print(f"    [WARN] query HTTP={status}: {response.get('error', response)}", file=sys.stderr)
        return True  # 查询失败不删除，保守处理
    result = response.get("data") or response.get("result") or response
    items = result.get("items") or []
    return len(items) > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="清除假成功的 seen 记录")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--seen-dir", default=DEFAULT_SEEN_DIR)
    parser.add_argument("--companies", nargs="*", default=["C017", "C018", "C020"])
    parser.add_argument("--id-prefix", default=DEFAULT_ID_PREFIX)
    parser.add_argument("--dry-run", action="store_true", help="只列出不删除")
    parser.add_argument("--delete", action="store_true", help="执行删除（不移 trash 直接删）")
    args = parser.parse_args()

    seen_dir = Path(args.seen_dir)
    total_fake = 0
    total_success = 0
    total_deleted = 0

    for company_id in args.companies:
        company_dir = seen_dir / company_id
        if not company_dir.exists():
            print(f"[{company_id}] seen 目录不存在: {company_dir}")
            continue

        print("=" * 100)
        print(f"Company: {company_id}")

        for path in sorted(company_dir.glob("*.response.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception as ex:
                print(f"  {path.name}: 读取失败 {ex}，跳过")
                continue

            status = str(record.get("status", "")).lower()
            if status != "success":
                continue  # 非 success 记录本来就会重写，不需要处理

            total_success += 1
            session_id = record.get("sessionId", path.stem.replace(".response", ""))
            source_client = record.get(
                "sourceClient", f"{args.id_prefix}-{company_id}-{session_id}"
            )
            user_id = f"{args.id_prefix}-{company_id}"
            agent_id = f"{args.id_prefix}-{company_id}-agent"

            if has_items(args.server, user_id, agent_id, source_client):
                print(f"  {session_id}: ✅ 服务器有 item（有效）")
                continue

            total_fake += 1
            print(f"  {session_id}: ⚠ 假成功（服务器无 item）-> {path.name}")

            if args.dry_run:
                continue

            # 备份后删除 seen 记录，让主脚本重新写入
            backup_path = company_dir / f"{path.stem}.bak.json"
            try:
                path.rename(backup_path)
                print(f"      -> 已移到 {backup_path.name}（主脚本将重新写入）")
                total_deleted += 1
            except OSError as ex:
                if args.delete:
                    path.unlink()
                    print(f"      -> 已删除")
                    total_deleted += 1
                else:
                    print(f"      -> 移动失败: {ex}（可用 --delete 直接删除）", file=sys.stderr)

        print()

    print("=" * 100)
    print(f"seen success 记录: {total_success}")
    print(f"假成功: {total_fake}")
    if args.dry_run:
        print("dry-run 模式：仅列出，未删除。加 --delete 或重跑（不带 --dry-run 默认备份）实际处理。")
    else:
        print(f"已处理（备份/删除）: {total_deleted}")
    print("处理后重跑主脚本即可自动补齐假成功的 session。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
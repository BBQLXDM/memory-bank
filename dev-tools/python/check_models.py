#!/usr/bin/env python3
"""
检测 DashScope 各模型是否可用（HTTP 200 且 content 非空）。

用法：
    python3 dev-tools/python/check_models.py
    python3 dev-tools/python/check_models.py --models qwen3.7-max-preview deepseek-v4-flash-0731

判定规则：
    ✅ 可用:      HTTP 200 且 content 非空
    ❌ content空: HTTP 200 但 content 为空（推理模型坑，不适合结构化抽取）
    ❌ 额度耗尽:  HTTP 403 且含 quota 关键字
    ⚠ 其他错误:  HTTP 非 200

凭据：OPENAI_API_KEY（环境变量或 .env）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 之前有额度的候选模型（按优先级排列）
DEFAULT_MODELS = [
    "qwen3.7-max-preview",
    "qwen3.7-max-2026-05-20",
    "qwen3.7-turbo",
    "qwen3.7-plus",
    "qwen3.7-flash",
    "qwen3.7-flash-2026-07-15",
    "qwen-turbo-latest",
    "qwen-plus-latest",
    "deepseek-v4-flash-0731",
    "glm-5.2",
]


def load_env(path: str = ".env") -> None:
    """加载 .env，用 .env 的值覆盖（优先）已有同名环境变量，避免残留旧值。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value


def check_model(api_key: str, model: str, timeout: int = 30) -> tuple[str, str]:
    """返回 (状态, 说明)。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 100,
    }
    request = Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body).get("error", {})
            message = err.get("message", body)
            code = err.get("code", "")
        except Exception:
            message = body
            code = ""
        if exc.code == 403 and "quota" in (message + code).lower():
            return "额度耗尽", f"HTTP 403: {message[:80]}"
        return "其他错误", f"HTTP {exc.code}: {message[:80]}"
    except URLError as exc:
        return "网络错误", str(exc)[:80]

    choices = data.get("choices") or []
    message = (choices[0] or {}).get("message", {}) if choices else {}
    content = str(message.get("content", "") or "").strip()
    reasoning = str(message.get("reasoning_content", "") or "").strip()
    if content:
        return "可用", f"content={content[:40]!r}"
    if reasoning:
        return "content空", f"只有 reasoning_content={reasoning[:40]!r}"
    return "content空", "content 与 reasoning_content 都为空"


def main() -> int:
    parser = argparse.ArgumentParser(description="检测 DashScope 各模型是否可用")
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS, help="要检测的模型（默认全部候选）")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY 未设置（请 source .env 或设置环境变量）")
        return 1

    print("=" * 90)
    print(f"API key: {api_key[:8]}...")
    print(f"检测 {len(args.models)} 个模型（URL: {BASE_URL}）")
    print("=" * 90)

    results = []
    for model in args.models:
        status, note = check_model(api_key, model, args.timeout)
        results.append((status, model, note))
        mark = {"可用": "✅", "content空": "❌", "额度耗尽": "❌", "其他错误": "⚠", "网络错误": "⚠"}.get(status, "?")
        print(f"{mark} {status:<6} {model:<40} {note}")

    print("=" * 90)
    available = [(m, n) for s, m, n in results if s == "可用"]
    if available:
        print("可用模型（HTTP 200 + content 非空）:")
        for m, n in available:
            print(f"  ✅ {m}  ({n})")
        print()
        print("需要在 .env 中设置: OPENAI_CHAT_MODEL=<上述任一模型>")
        print("然后重启后端 + 重跑写入脚本（断点机制会自动从已写部分继续）")
    else:
        print("没有可用的模型。所有候选均已耗尽或不可用。")
        print("建议：到阿里云百炼控制台充值 / 关闭 'use free tier only'，或更换平台 key。")
    return 0 if available else 2


if __name__ == "__main__":
    raise SystemExit(main())
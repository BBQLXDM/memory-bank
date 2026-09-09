#!/usr/bin/env python3
"""
LLM 推理延迟测试脚本（独立测试工具，不影响现有代码）。

用途：
- 直接测量 LLM API 在不同输入/输出规模下的耗时，判断 commit 慢是否来自模型本身
- 不发 commit 请求，不写任何记忆，纯测量

场景设计（模拟写入抽取的典型负载）：
1. ping          : 最小请求，测基础延迟
2. short_out     : 短输入 + 中等输出（模拟简单记忆条目）
3. long_input    : 长输入 + 结构化输出（模拟抽取 prompt + 6 轮对话上下文）
4. long_output   : 中等输入 + 长输出（模拟多条记忆条目生成）

用法示例：
    python3 dev-tools/python/profile_llm_latency.py
    python3 dev-tools/python/profile_llm_latency.py --model qwen-plus --rounds 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import requests

from memind_shared import load_env

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

SCENARIOS = {
    "ping": {
        "label": "ping（最小请求）",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    },
    "short_out": {
        "label": "短输入 + 中等输出",
        "messages": [
            {
                "role": "user",
                "content": (
                    "提取以下对话中的关键信息，输出为简洁记忆条目：\n"
                    "客户：华信精密，贷款利率年化4.45%，抵押物为合肥经开区紫云路288号综合办公楼。"
                ),
            }
        ],
        "max_tokens": 200,
    },
    "long_input": {
        "label": "长输入 + 结构化输出（模拟抽取）",
        "messages": [
            {
                "role": "user",
                "content": (
                    "你是记忆抽取助手。请从以下对话中抽取事实、事件、偏好等记忆条目，"
                    "按 JSON 数组输出。\n"
                    + "对话内容：\n"
                    + ("（客户经理）您好，请问贵公司目前的融资情况如何？\n"
                       "（客户）我们目前有流动资金贷款8000万，银行承兑汇票5000万。\n"
                       "（客户经理）贷款用途是什么？\n"
                       "（客户）主要用于家电家居采购、区域代理商货款结算和物流运输费用。\n"
                       "（客户经理）是否有其他融资需求？\n"
                       "（客户）暂无，目前这些就够用了。\n") * 6
                ),
            }
        ],
        "max_tokens": 1000,
    },
    "long_output": {
        "label": "中等输入 + 长输出（多条记忆）",
        "messages": [
            {
                "role": "user",
                "content": (
                    "请把以下段落拆解成 15 条独立的记忆条目，每条一句话，编号输出：\n"
                    "该企业成立于2015年，注册资本1.2亿元，主营家电家居供应链服务，"
                    "2025年营收约8.6亿元，员工420人，在华东、华南设有6个仓储中心，"
                    "主要合作银行包括工行、建行，2026年计划新增出口订单融资业务。"
                ),
            }
        ],
        "max_tokens": 1500,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM 推理延迟测试（独立测试工具）")
    parser.add_argument("--model", default=None, help="覆盖 .env 中的 OPENAI_CHAT_MODEL")
    parser.add_argument("--rounds", type=int, default=2, help="每个场景重复次数（默认 2）")
    parser.add_argument("--base-url", default=None, help="覆盖 .env 中的 OPENAI_BASE_URL")
    parser.add_argument("--api-key", default=None, help="覆盖 .env 中的 OPENAI_API_KEY")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env()

    model = args.model or __import__("os").environ.get("OPENAI_CHAT_MODEL", "")
    base_url = (args.base_url or __import__("os").environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
    api_key = args.api_key or __import__("os").environ.get("OPENAI_API_KEY", "")

    if not model or not api_key:
        print("[FATAL] 缺少 OPENAI_CHAT_MODEL / OPENAI_API_KEY（检查 .env）")
        sys.exit(1)

    url = f"{base_url}/chat/completions"
    print(f"模型: {model}")
    print(f"服务: {base_url}")
    print(f"每场景轮数: {args.rounds}")
    print("=" * 90)

    results: dict[str, list[float]] = {}
    for name, scenario in SCENARIOS.items():
        timings: list[float] = []
        output_tokens: list[int] = []
        for r in range(args.rounds):
            payload = {
                "model": model,
                "messages": scenario["messages"],
                "max_tokens": scenario["max_tokens"],
                "temperature": 0.2,
            }
            t0 = time.time()
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                    timeout=(10, 900),
                )
                elapsed = time.time() - t0
                if resp.status_code != 200:
                    print(f"  [round {r+1}] HTTP {resp.status_code}: {resp.text[:200]}")
                    continue
                data = resp.json()
                usage = data.get("usage", {})
                out_tok = usage.get("completion_tokens", 0)
                timings.append(elapsed)
                output_tokens.append(out_tok)
                print(f"  [round {r+1}] {scenario['label']}: {elapsed:.1f}s 输出{out_tok}tokens")
            except Exception as ex:
                print(f"  [round {r+1}] 失败: {ex}")
        results[name] = timings
        print()

    print("=" * 90)
    print("汇总（秒）")
    for name, scenario in SCENARIOS.items():
        timings = results[name]
        if not timings:
            print(f"  {scenario['label']}: 无有效数据")
            continue
        print(
            f"  {scenario['label']}: "
            f"平均={statistics.mean(timings):.1f}s 中位={statistics.median(timings):.1f}s "
            f"min={min(timings):.1f}s max={max(timings):.1f}s"
        )

    summary = {
        "model": model,
        "rounds": args.rounds,
        "scenarios": {
            name: {
                "label": SCENARIOS[name]["label"],
                "timings": results[name],
            }
            for name in SCENARIOS
        },
    }
    out_path = "benchmark-results/profile-latency/llm-latency.json"
    import os
    from pathlib import Path

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out_path}")


if __name__ == "__main__":
    main()
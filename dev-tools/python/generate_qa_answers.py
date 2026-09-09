#!/usr/bin/env python3
"""
检索 + 对话回答 结果生成脚本（第一阶段）。

对 benchmark 文件中的 qa_items 逐条：
  1. 调用 /open/v1/memory/retrieve 检索（保存检索原始响应）
  2. 把检索结果作为上下文，调用 LLM 回答（保存回答）
每条题目的完整数据写入结果目录，供第二阶段评测脚本读取打分。

用法示例：
    python3 dev-tools/python/generate_qa_answers.py \
        --server http://127.0.0.1:8366 \
        --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
        --companies C017 C018 C020 \
        --id-prefix benchmark-v107 \
        --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
        --llm-model qwen3.7-flash \
        --llm-api-key sk-xxx
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from memind_shared import company_entries, load_env

load_env()

DEFAULT_SERVER = os.environ.get("MEMIND_SERVER", "http://127.0.0.1:8366")
DEFAULT_BENCHMARK_DIR = os.environ.get(
    "BENCHMARK_DIR", "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"
)
DEFAULT_LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DEFAULT_LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.7-flash")
DEFAULT_OUT_DIR = os.environ.get(
    "OUT_DIR", os.path.join(os.getcwd(), "benchmark-results", "qa-answers")
)
DEFAULT_STRATEGY = os.environ.get("STRATEGY", "SIMPLE")
DEFAULT_TOP_K = int(os.environ.get("TOP_K", "8"))

COMPANIES = company_entries()


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value.strip() else default


def post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 60):
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
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


def first_nonempty(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def load_company_qa(path: Path) -> list[dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    items = obj.get("qa_items") or obj.get("qa") or []
    return [item for item in items if isinstance(item, dict)]


def extract_response_items(response: dict):
    result = response.get("data") or response.get("result") or response
    items = result.get("items") or []
    insights = result.get("insights") or []
    raw_data = result.get("rawData") or []
    evidences = result.get("evidences") or []
    return result.get("status"), items, insights, raw_data, evidences


def retrieve(server: str, company: dict, question: str, strategy: str, trace: bool, timeout: int):
    payload = {
        "userId": company["user_id"],
        "agentId": company["agent_id"],
        "query": question,
        "strategy": strategy,
        "trace": trace,
    }
    return post_json(f"{server}/open/v1/memory/retrieve", payload, timeout=timeout)


def format_context(items, insights, raw_data, evidences, top_k: int) -> str:
    def dump_entry(prefix: str, entry: dict, idx: int) -> str:
        text = first_nonempty(entry.get("text"), entry.get("content"), entry.get("caption"), json.dumps(entry, ensure_ascii=False))
        score = entry.get("finalScore") or entry.get("vectorScore") or entry.get("maxScore")
        return f"[{prefix} {idx}] score={score}\n{text}"

    sections = []
    if items:
        sections.append("检索到的记忆条目:\n" + "\n\n".join(dump_entry("item", item, i) for i, item in enumerate(items[:top_k], 1)))
    if insights:
        sections.append("检索到的洞察:\n" + "\n\n".join(dump_entry("insight", ins, i) for i, ins in enumerate(insights[:top_k], 1)))
    if raw_data:
        sections.append("检索到的原始数据:\n" + "\n\n".join(dump_entry("raw", r, i) for i, r in enumerate(raw_data[:top_k], 1)))
    if evidences:
        sections.append("检索到的证据:\n" + "\n".join(f"[{i}] {json.dumps(e, ensure_ascii=False)}" for i, e in enumerate(evidences[:top_k], 1)))
    return "\n\n".join(sections) if sections else "（无检索结果）"


def call_llm(base_url: str, model: str, api_key: str, messages: list[dict], timeout: int, retries: int = 2) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0}
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_error = None
    for attempt in range(retries + 1):
        status, response = post_json(
            f"{base_url.rstrip('/')}/chat/completions", payload, headers=headers, timeout=timeout
        )
        if status == 0:
            last_error = response.get("error", "LLM request failed")
        elif status >= 400:
            last_error = f"LLM HTTP {status}: {json.dumps(response, ensure_ascii=False)}"
        else:
            choices = response.get("choices") or []
            if choices:
                message = (choices[0] or {}).get("message") or {}
                content = first_nonempty(message.get("content"), (choices[0] or {}).get("text"))
                if content:
                    return content.strip()
                last_error = "LLM response missing content"
            else:
                last_error = "LLM response missing choices"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_error or "LLM request failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="检索 + 回答结果生成（第一阶段）")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--benchmark-dir", default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--companies", nargs="*", default=["C017", "C018", "C020"])
    parser.add_argument("--id-prefix", default="benchmark-v107")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--limit-per-company", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--llm-timeout", type=int, default=60)
    parser.add_argument("--llm-base-url", default=DEFAULT_LLM_BASE_URL)
    parser.add_argument("--llm-model", default=env("LLM_MODEL", env("OPENAI_CHAT_MODEL", "qwen3.7-flash")))
    parser.add_argument("--llm-api-key", default=env("LLM_API_KEY", env("OPENAI_API_KEY", "")))
    parser.add_argument("--no-llm", action="store_true", help="只检索，不调用 LLM 回答")
    args = parser.parse_args()

    benchmark_dir = Path(args.benchmark_dir)
    out_dir = Path(args.out_dir)
    run_dir = out_dir / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    company_by_id = {c["company_id"]: c for c in COMPANIES}
    selected = []
    for cid in args.companies:
        if cid.upper() in company_by_id:
            c = dict(company_by_id[cid.upper()])
            c["user_id"] = f"{args.id_prefix}-{c['company_id']}"
            c["agent_id"] = f"{args.id_prefix}-{c['company_id']}-agent"
            selected.append(c)
    if not selected:
        print("[ERROR] no valid companies selected.")
        return 1

    print("=" * 100)
    print(f"Server: {args.server}")
    print(f"Benchmark dir: {benchmark_dir}")
    print(f"LLM base url: {args.llm_base_url}")
    print(f"LLM model: {args.llm_model}")
    print(f"LLM api key: {'set' if args.llm_api_key else 'empty'}")
    print(f"Strategy: {args.strategy}")
    print(f"Top-K: {args.top_k}")
    print(f"Companies: {', '.join(c['company_id'] for c in selected)}")
    print(f"LLM answer: {'OFF' if args.no_llm else 'ON'}")
    print(f"Output dir: {run_dir}")
    print("=" * 100)

    results = []

    for company in selected:
        file_path = benchmark_dir / company["file_name"]
        if not file_path.exists():
            print(f"[WARN] missing benchmark file: {file_path}")
            continue
        qa_items = load_company_qa(file_path)
        if args.limit_per_company > 0:
            qa_items = qa_items[: args.limit_per_company]

        print("=" * 100)
        print(f"Company: {company['company_id']} {company['company_name']}")
        print(f"QA items: {len(qa_items)}")

        for idx, qa in enumerate(qa_items, 1):
            qa_id = first_nonempty(qa.get("qa_id"), f"{company['company_id']}-{idx}")
            question = first_nonempty(qa.get("question"), qa.get("q"))
            expected_answer = first_nonempty(qa.get("answer"), qa.get("a"))
            if not question:
                continue

            record = {
                "company_id": company["company_id"],
                "company_name": company["company_name"],
                "qa_id": qa_id,
                "question": question,
                "expected_answer": expected_answer,
                "answer_type": qa.get("answer_type"),
                "capability": qa.get("capability"),
                "difficulty": qa.get("difficulty"),
                "evidence": qa.get("evidence"),
                "retrieval": None,
                "retrieval_http_status": None,
                "retrieval_status": None,
                "item_count": 0,
                "context": "",
                "answer": "",
                "answer_error": None,
            }

            # 1. 检索
            retrieval_status, retrieval_response = retrieve(
                args.server, company, question, args.strategy, trace=True, timeout=args.timeout
            )
            record["retrieval_http_status"] = retrieval_status
            record["retrieval"] = retrieval_response

            status, items, insights, raw_data, evidences = extract_response_items(retrieval_response)
            record["retrieval_status"] = status
            record["item_count"] = len(items)

            # 2. 组织上下文
            record["context"] = format_context(items, insights, raw_data, evidences, args.top_k)

            # 3. LLM 回答
            if not args.no_llm:
                system_prompt = (
                    "你是企业经营问答助手。根据检索到的记忆上下文回答问题。"
                    "如果上下文中包含与问题相关的关键事实，请直接给出简洁结论；"
                    "涉及数值、日期、编号、比例时优先抽取最直接的事实。"
                    "不要解释推理过程，不要复述证据。"
                    "如果上下文确实缺少回答问题所需的关键事实，回答'无法确定'，"
                    "并用一句话简要说明缺失的是什么信息"
                    "（例如：当前记忆中未记录该公司的抵押率数据）。"
                )
                user_prompt = f"公司：{company['company_name']}\n问题：{question}\n\n检索上下文：\n{record['context']}"
                try:
                    record["answer"] = call_llm(
                        args.llm_base_url, args.llm_model, args.llm_api_key,
                        [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
                        timeout=args.llm_timeout,
                    )
                except Exception as exc:
                    record["answer_error"] = str(exc)
                    record["answer"] = f"[LLM_ERROR] {record['answer_error']}"

            results.append(record)

            print(f"[{idx:03d}] {qa_id}")
            print(f"  Q: {question}")
            print(f"  A(预期): {expected_answer}")
            print(f"  检索: HTTP={retrieval_status} status={status} items={len(items)}")
            if not args.no_llm:
                print(f"  回答: {record['answer'][:120]}")
                if record["answer_error"]:
                    print(f"  回答错误: {record['answer_error']}")

    # 输出结果文件
    manifest = {
        "server": args.server,
        "benchmark_dir": str(benchmark_dir),
        "llm_base_url": args.llm_base_url,
        "llm_model": args.llm_model,
        "strategy": args.strategy,
        "top_k": args.top_k,
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "record_count": len(results),
        "records": results,
    }
    manifest_path = run_dir / "results.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 100)
    print(f"Done. 生成 {len(results)} 条记录 -> {manifest_path}")
    print("第二阶段: 运行 dev-tools/python/evaluate_qa_results.py 读取该文件进行评测。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

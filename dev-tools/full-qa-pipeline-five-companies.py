# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#!/usr/bin/env python3
"""Run retrieval plus LLM answering for selected benchmark companies."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK_DIR = Path(
    os.environ.get(
        "BENCHMARK_DIR", "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"
    )
)
DEFAULT_SERVER = os.environ.get("MEMIND_SERVER", "http://127.0.0.1:8366")
DEFAULT_LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
)
DEFAULT_LLM_MODEL = os.environ.get(
    "LLM_MODEL", os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
)
DEFAULT_OUT_DIR = Path(
    os.environ.get(
        "OUT_DIR", str(PROJECT_DIR / "benchmark-results" / "rag-two-companies")
    )
)
DEFAULT_STRATEGY = os.environ.get("STRATEGY", "SIMPLE")
DEFAULT_TRACE = os.environ.get("TRACE", "true").lower() == "true"
DEFAULT_TOP_K = int(os.environ.get("TOP_K", "8"))
DEFAULT_SCOPE = os.environ.get("SCOPE", "")
DEFAULT_CATEGORIES = [
    value.strip()
    for value in os.environ.get("CATEGORIES", "").split(",")
    if value.strip()
]
DEFAULT_INCLUDE_RAW_DATA_METADATA = os.environ.get("INCLUDE_RAW_DATA_METADATA", "false").lower() == "true"
DEFAULT_INCLUDE_RAW_DATA_SEGMENT = os.environ.get("INCLUDE_RAW_DATA_SEGMENT", "false").lower() == "true"

COMPANIES = [
    {
        "company_id": "C016",
        "company_name": "绿能新源装备有限公司",
        "user_id": "benchmark-v107-C016-isolated",
        "agent_id": "benchmark-v107-C016-isolated-agent",
        "file_name": "绿能新源装备有限公司.json",
    },
    {
        "company_id": "C019",
        "company_name": "鑫科精密零部件制造有限公司",
        "user_id": "benchmark-v107-C019-isolated",
        "agent_id": "benchmark-v107-C019-isolated-agent",
        "file_name": "鑫科精密零部件制造有限公司.json",
    },
]


@dataclass
class QAResult:
    company_id: str
    company_name: str
    qa_id: str
    question: str
    expected_answer: str
    status: str | None
    item_count: int
    top1: str | None
    match_rank: int | None
    retrieval_path: str
    answer: str
    answer_match: bool
    answer_path: str


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value.strip() else default


def post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> tuple[int, dict[str, Any]]:
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


def first_nonempty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def load_company_qa(path: Path) -> list[dict[str, Any]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    items = obj.get("qa_items") or obj.get("qa") or obj.get("qas") or []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def normalize_answer(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    variants = {text}
    if "——" in text:
        variants.add(text.split("——", 1)[0].strip())
    if "：" in text:
        variants.add(text.split("：", 1)[0].strip())
    if "-" in text:
        variants.add(text.split("-", 1)[0].strip())
    return [variant for variant in variants if variant]


def extract_response_items(
    response: dict[str, Any],
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result = response.get("data") or response.get("result") or response
    items = result.get("items") or []
    insights = result.get("insights") or []
    evidences = result.get("evidences") or []
    raw_data = result.get("rawData") or []
    return result.get("status"), items, insights, evidences, raw_data


def find_match_rank(items: list[dict[str, Any]], expected_answer: str) -> tuple[int | None, str | None]:
    variants = normalize_answer(expected_answer)
    if not variants:
        return None, None
    for idx, item in enumerate(items, 1):
        text = first_nonempty(
            item.get("text"),
            item.get("content"),
            json.dumps(item, ensure_ascii=False),
        )
        if any(variant in text for variant in variants):
            return idx, text
    return None, None


def format_context(
    items: list[dict[str, Any]],
    insights: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
    raw_data: list[dict[str, Any]],
    top_k: int,
) -> str:
    def dump_entry(prefix: str, entry: dict[str, Any], idx: int) -> str:
        text = first_nonempty(entry.get("text"), entry.get("content"), json.dumps(entry, ensure_ascii=False))
        score = entry.get("finalScore")
        if score is None:
            score = entry.get("vectorScore")
        meta = entry.get("metadata") or {}
        return (
            f"[{prefix} {idx}] score={score} id={entry.get('id') or entry.get('rawDataId') or ''}\n"
            f"{text}\n"
            f"meta={json.dumps(meta, ensure_ascii=False)}"
        )

    sections: list[str] = []
    if items:
        sections.append("Retrieved items:\n" + "\n\n".join(dump_entry("item", item, idx) for idx, item in enumerate(items[:top_k], 1)))
    if insights:
        sections.append(
            "Retrieved insights:\n"
            + "\n\n".join(dump_entry("insight", insight, idx) for idx, insight in enumerate(insights[:top_k], 1))
        )
    if raw_data:
        sections.append(
            "Retrieved raw data:\n"
            + "\n\n".join(dump_entry("raw", raw, idx) for idx, raw in enumerate(raw_data[:top_k], 1))
        )
    if evidences:
        sections.append(
            "Retrieved evidences:\n"
            + "\n".join(
                f"[{idx}] {json.dumps(evidence, ensure_ascii=False)}"
                for idx, evidence in enumerate(evidences[:top_k], 1)
            )
        )
    return "\n\n".join(sections) if sections else "No retrieval context returned."


def retrieve(
    server: str,
    company: dict[str, str],
    question: str,
    strategy: str,
    trace: bool,
    timeout: int,
    scope: str = "",
    categories: list[str] | None = None,
    include_raw_data_metadata: bool = False,
    include_raw_data_segment: bool = False,
) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {
        "userId": company["user_id"],
        "agentId": company["agent_id"],
        "query": question,
        "strategy": strategy,
        "trace": trace,
    }
    if scope:
        payload["scope"] = scope
    if categories:
        payload["categories"] = categories
    if include_raw_data_metadata or include_raw_data_segment:
        payload["include"] = {
            "rawDataMetadata": include_raw_data_metadata,
            "rawDataSegment": include_raw_data_segment,
        }
    return post_json(f"{server}/open/v1/memory/retrieve", payload, timeout=timeout)


def probe_company(
    server: str,
    company: dict[str, str],
    strategy: str,
    trace: bool,
    scope: str = "",
    categories: list[str] | None = None,
    include_raw_data_metadata: bool = False,
    include_raw_data_segment: bool = False,
) -> tuple[int, str | None, int]:
    probe_question = f"{company['company_name']}的基础经营情况如何？"
    status_code, response = retrieve(
        server,
        company,
        probe_question,
        strategy,
        trace,
        timeout=30,
        scope=scope,
        categories=categories,
        include_raw_data_metadata=include_raw_data_metadata,
        include_raw_data_segment=include_raw_data_segment,
    )
    status, items, _, _, _ = extract_response_items(response)
    return status_code, status, len(items)


def call_llm(
    llm_base_url: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    timeout: int,
    retries: int = 2,
    retry_delay: float = 1.5,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last_error: str | None = None
    for attempt in range(retries + 1):
        status, response = post_json(
            f"{llm_base_url.rstrip('/')}/chat/completions",
            payload,
            headers=headers,
            timeout=timeout,
        )
        if status == 0:
            last_error = response.get("error", "LLM request failed")
        elif status >= 400:
            last_error = f"LLM HTTP {status}: {json.dumps(response, ensure_ascii=False)}"
        else:
            choices = response.get("choices") or []
            if not choices:
                last_error = f"LLM response missing choices: {json.dumps(response, ensure_ascii=False)}"
            else:
                choice0 = choices[0] or {}
                message = choice0.get("message") or {}
                content = first_nonempty(message.get("content"), choice0.get("text"))
                if content:
                    return content.strip()
                last_error = f"LLM response missing content: {json.dumps(response, ensure_ascii=False)}"

        if attempt < retries:
            time.sleep(retry_delay * (attempt + 1))

    raise RuntimeError(last_error or "LLM request failed")


def answer_matches(expected_answer: str, generated_answer: str) -> bool:
    expected_variants = normalize_answer(expected_answer)
    actual = (generated_answer or "").strip()
    if not expected_variants or not actual:
        return False
    return any(variant in actual or actual in variant for variant in expected_variants)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--benchmark-dir", default=str(DEFAULT_BENCHMARK_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--companies", nargs="*", default=["C016", "C019"])
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--trace", default=str(DEFAULT_TRACE).lower())
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument(
        "--categories",
        nargs="*",
        default=DEFAULT_CATEGORIES,
        help="Optional retrieval categories, e.g. facts insights raw_data",
    )
    parser.add_argument(
        "--include-raw-data-metadata",
        action="store_true",
        default=DEFAULT_INCLUDE_RAW_DATA_METADATA,
    )
    parser.add_argument(
        "--include-raw-data-segment",
        action="store_true",
        default=DEFAULT_INCLUDE_RAW_DATA_SEGMENT,
    )
    parser.add_argument("--limit-per-company", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--llm-timeout", type=int, default=60)
    parser.add_argument("--llm-base-url", default=DEFAULT_LLM_BASE_URL)
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument(
        "--llm-api-key", default=env("LLM_API_KEY", env("OPENAI_API_KEY", ""))
    )
    parser.add_argument("--skip-unavailable", action="store_true", help="Skip companies whose probe has no retrieval results")
    args = parser.parse_args()

    benchmark_dir = Path(args.benchmark_dir)
    out_dir = Path(args.out_dir)
    run_dir = out_dir / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    company_by_id = {company["company_id"]: company for company in COMPANIES}
    selected = [company_by_id[cid] for cid in args.companies if cid in company_by_id]
    if not selected:
        print("[ERROR] no valid companies selected.")
        return 1

    trace = args.trace.lower() == "true"
    selected_companies: list[dict[str, str]] = []
    skipped_companies: list[dict[str, str]] = []

    print("=" * 100)
    print(f"Server: {args.server}")
    print(f"Benchmark dir: {benchmark_dir}")
    print(f"LLM base url: {args.llm_base_url}")
    print(f"LLM model: {args.llm_model}")
    print(f"LLM api key: {'set' if args.llm_api_key else 'empty'}")
    print(f"LLM timeout: {args.llm_timeout}s")
    print(f"Retrieval scope: {args.scope or '(default)'}")
    print(f"Retrieval categories: {args.categories or '(default)'}")
    print(
        "Retrieval include: "
        f"rawDataMetadata={args.include_raw_data_metadata}, "
        f"rawDataSegment={args.include_raw_data_segment}"
    )
    print(f"Companies: {', '.join(company['company_id'] for company in selected)}")
    print("=" * 100)

    for company in selected:
        probe_status, probe_result_status, probe_items = probe_company(
            args.server,
            company,
            args.strategy,
            trace,
            scope=args.scope,
            categories=args.categories,
            include_raw_data_metadata=args.include_raw_data_metadata,
            include_raw_data_segment=args.include_raw_data_segment,
        )
        print(
            f"[PROBE] {company['company_id']} {company['company_name']} "
            f"HTTP={probe_status} status={probe_result_status} items={probe_items}"
        )
        if probe_status >= 200 and probe_status < 300 and probe_items > 0:
            selected_companies.append(company)
        else:
            skipped_companies.append(company)

    if args.skip_unavailable:
        companies_to_run = selected_companies
    else:
        companies_to_run = selected

    if args.skip_unavailable and not companies_to_run:
        print("[ERROR] all companies were skipped by probe.")
        print("[HINT] if retrieval is healthy but probe still fails, run without --skip-unavailable only for debugging.")
        summary = {
            "server": args.server,
            "benchmark_dir": str(benchmark_dir),
            "llm_base_url": args.llm_base_url,
            "llm_model": args.llm_model,
            "companies": [],
            "skipped_companies": skipped_companies,
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "summary.csv").write_text(
            "company_id,company_name,qa_id,status,item_count,match_rank,answer_match,question,expected_answer,answer,retrieval_path,answer_path\n",
            encoding="utf-8",
        )
        return 0

    if skipped_companies:
        print("[WARN] probe had empty/failed companies:")
        for company in skipped_companies:
            print(f"  - {company['company_id']} {company['company_name']}")

    results: list[QAResult] = []
    company_summaries: list[dict[str, Any]] = []

    for company in companies_to_run:
        file_path = benchmark_dir / company["file_name"]
        if not file_path.exists():
            print(f"[WARN] missing benchmark file: {file_path}")
            continue

        qa_items = load_company_qa(file_path)
        if args.limit_per_company > 0:
            qa_items = qa_items[: args.limit_per_company]

        company_dir = run_dir / company["company_id"]
        company_dir.mkdir(parents=True, exist_ok=True)

        print("=" * 100)
        print(f"Company: {company['company_id']} {company['company_name']}")
        print(f"QA items: {len(qa_items)}")

        company_stats = {
            "company_id": company["company_id"],
            "company_name": company["company_name"],
            "qa_count": len(qa_items),
            "retrieval_success_count": 0,
            "answer_match_count": 0,
            "top1_match_count": 0,
        }

        for idx, qa in enumerate(qa_items, 1):
            qa_id = first_nonempty(qa.get("qa_id"), f"{company['company_id']}-{idx}")
            question = first_nonempty(qa.get("question"), qa.get("q"))
            expected_answer = first_nonempty(qa.get("answer"), qa.get("a"))
            if not question:
                continue

            retrieval_status_code, retrieval_response = retrieve(
                args.server,
                company,
                question,
                args.strategy,
                trace,
                timeout=args.timeout,
                scope=args.scope,
                categories=args.categories,
                include_raw_data_metadata=args.include_raw_data_metadata,
                include_raw_data_segment=args.include_raw_data_segment,
            )
            retrieval_path = company_dir / f"{idx:03d}-{qa_id}.retrieval.json"
            retrieval_path.write_text(
                json.dumps(retrieval_response, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            status, items, insights, evidences, raw_data = extract_response_items(retrieval_response)
            match_rank, matched_text = find_match_rank(items, expected_answer)
            top1 = first_nonempty(items[0].get("text") if items else "") or None

            if retrieval_status_code >= 200 and retrieval_status_code < 300:
                company_stats["retrieval_success_count"] += 1
            if match_rank == 1:
                company_stats["top1_match_count"] += 1
            if match_rank is not None:
                company_stats["answer_match_count"] += 1

            context = format_context(items, insights, evidences, raw_data, args.top_k)
            system_prompt = (
                "你是企业经营问答助手。先判断题目真正要问的事实，再依据检索上下文作答。"
                "只要上下文中已经出现了与问题相关的关键事实，即使表达不完全相同，也要据此给出最可能正确的答案。"
                "不要因为措辞不完全一致就拒答；只有在上下文确实缺少关键事实时，才回答无法确定。"
                "涉及数值、日期、编号、比例、是否存在、是否允许、是否全部、是否已经完成等问题时，"
                "优先抽取最直接对应的事实并输出简短结论。"
                "不要解释推理过程，不要复述证据，不要输出无关背景。"
            )
            user_prompt = (
                f"公司：{company['company_name']}\n"
                f"问题：{question}\n\n"
                f"检索上下文：\n{context}\n\n"
                "请先理解题目要求，再从上下文中抽取最直接支持答案的事实；"
                "如果上下文里有明显相关且可支撑答案的内容，请直接作答，不要过度保守。"
            )
            answer_error: str | None = None
            answer = ""
            try:
                answer = call_llm(
                    args.llm_base_url,
                    args.llm_model,
                    args.llm_api_key,
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    timeout=args.llm_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                answer_error = str(exc)
                answer = f"[LLM_ERROR] {answer_error}"

            answer_path = company_dir / f"{idx:03d}-{qa_id}.answer.json"
            answer_payload = {
                "company_id": company["company_id"],
                "company_name": company["company_name"],
                "qa_id": qa_id,
                "question": question,
                "expected_answer": expected_answer,
                "retrieval_status": status,
                "retrieval_http_status": retrieval_status_code,
                "retrieval_item_count": len(items),
                "retrieval_top1": top1,
                "retrieval_match_rank": match_rank,
                "retrieval_matched_text": matched_text,
                "answer": answer,
                "answer_match": answer_matches(expected_answer, answer),
                "answer_error": answer_error,
            }
            answer_path.write_text(json.dumps(answer_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            answer_match = answer_matches(expected_answer, answer)
            results.append(
                QAResult(
                    company_id=company["company_id"],
                    company_name=company["company_name"],
                    qa_id=qa_id,
                    question=question,
                    expected_answer=expected_answer,
                    status=status,
                    item_count=len(items),
                    top1=top1,
                    match_rank=match_rank,
                    retrieval_path=str(retrieval_path),
                    answer=answer,
                    answer_match=answer_match,
                    answer_path=str(answer_path),
                )
            )

            print(f"[{idx:03d}] {qa_id}")
            print(f"  Q: {question}")
            print(f"  A: {expected_answer}")
            print(
                f"  HTTP: {retrieval_status_code}  status={status}  items={len(items)}  "
                f"match_rank={match_rank}  answer_match={answer_match}"
            )
            print(f"  answer: {answer}")
            if answer_error:
                print(f"  answer_error: {answer_error}")
            if top1:
                print(f"  top1: {top1}")

        company_summaries.append(company_stats)

    summary = {
        "server": args.server,
        "benchmark_dir": str(benchmark_dir),
        "llm_base_url": args.llm_base_url,
        "llm_model": args.llm_model,
        "strategy": args.strategy,
        "trace": trace,
        "scope": args.scope,
        "categories": args.categories,
        "include_raw_data_metadata": args.include_raw_data_metadata,
        "include_raw_data_segment": args.include_raw_data_segment,
        "companies": company_summaries,
        "skipped_companies": skipped_companies,
        "run_dir": str(run_dir),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_lines = [
        "company_id,company_name,qa_id,status,item_count,match_rank,answer_match,top1,question,expected_answer,answer,retrieval_path,answer_path"
    ]
    for row in results:
        def esc(value: str | None) -> str:
            text = value or ""
            return '"' + text.replace('"', '""') + '"'

        csv_lines.append(
            ",".join(
                [
                    esc(row.company_id),
                    esc(row.company_name),
                    esc(row.qa_id),
                    esc(row.status or ""),
                    str(row.item_count),
                    "" if row.match_rank is None else str(row.match_rank),
                    "true" if row.answer_match else "false",
                    esc(row.top1),
                    esc(row.question),
                    esc(row.expected_answer),
                    esc(row.answer),
                    esc(row.retrieval_path),
                    esc(row.answer_path),
                ]
            )
        )
    (run_dir / "summary.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    print("=" * 100)
    print(f"Done. Summary written to: {run_dir / 'summary.json'}")
    print(f"CSV written to: {run_dir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

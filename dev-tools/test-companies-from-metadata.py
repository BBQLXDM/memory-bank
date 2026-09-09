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
"""Run retrieval tests from benchmark metadata QA items for multiple companies.

This script reads each company's benchmark JSON, extracts `qa_items`, and sends the
questions to the local Memind retrieval endpoint. It records top-K results and
reports whether the expected answer appears anywhere in the retrieved items.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SERVER = os.environ.get("BASE_URL", "http://127.0.0.1:8366")
DEFAULT_STRATEGY = os.environ.get("STRATEGY", "SIMPLE")
DEFAULT_TRACE = os.environ.get("TRACE", "true").lower() == "true"
DEFAULT_OUT_DIR = Path(
    os.environ.get(
        "OUT_DIR", str(PROJECT_DIR / "benchmark-results" / "metadata-company-tests")
    )
)
DEFAULT_BENCHMARK_DIR = Path(
    os.environ.get(
        "BENCHMARK_DIR", "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/core"
    )
)

COMPANIES = [
    {
        "company_id": "C016",
        "company_name": "绿能新源装备有限公司",
        "user_id": "benchmark-v107-C016-isolated",
        "agent_id": "benchmark-v107-C016-isolated-agent",
        "file_name": "绿能新源装备有限公司.json",
    },
    {
        "company_id": "C017",
        "company_name": "联科绿筑新型建材有限公司",
        "user_id": "benchmark-v107-C017-isolated",
        "agent_id": "benchmark-v107-C017-isolated-agent",
        "file_name": "联科绿筑新型建材有限公司.json",
    },
    {
        "company_id": "C018",
        "company_name": "鑫源精密机械制造有限公司",
        "user_id": "benchmark-v107-C018-isolated",
        "agent_id": "benchmark-v107-C018-isolated-agent",
        "file_name": "鑫源精密机械制造有限公司.json",
    },
    {
        "company_id": "C019",
        "company_name": "鑫科精密零部件制造有限公司",
        "user_id": "benchmark-v107-C019-isolated",
        "agent_id": "benchmark-v107-C019-isolated-agent",
        "file_name": "鑫科精密零部件制造有限公司.json",
    },
    {
        "company_id": "C020",
        "company_name": "锐科航空装备股份有限公司",
        "user_id": "benchmark-v107-C020-isolated",
        "agent_id": "benchmark-v107-C020-isolated-agent",
        "file_name": "锐科航空装备股份有限公司.json",
    },
]


@dataclass
class ResultRow:
    company_id: str
    company_name: str
    qa_id: str
    question: str
    expected_answer: str
    status: str | None
    item_count: int
    top1: str | None
    match_rank: int | None
    matched_text: str | None
    raw_path: str


def post_json(url: str, body: dict[str, Any], timeout: int = 60) -> tuple[int, dict[str, Any]]:
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
    if text.startswith("无法确定"):
        variants.add(text.split("——", 1)[0])
        variants.add(text.split(":", 1)[0])
    return [variant for variant in variants if variant]


def extract_response_items(response: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result = response.get("data") or response.get("result") or response
    items = result.get("items") or []
    insights = result.get("insights") or []
    evidences = result.get("evidences") or []
    raw_data = result.get("rawData") or []
    return result.get("status"), items, insights, evidences, raw_data


def first_nonempty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""


def find_match_rank(items: list[dict[str, Any]], expected_answer: str) -> tuple[int | None, str | None]:
    if not expected_answer.strip():
        return None, None
    variants = normalize_answer(expected_answer)
    for idx, item in enumerate(items, 1):
        text = first_nonempty(item.get("text"), item.get("content"), json.dumps(item, ensure_ascii=False))
        if any(variant in text for variant in variants):
            return idx, text
    return None, None


def probe_company_space(server: str, company: dict[str, str], strategy: str, trace: bool) -> tuple[int, str | None, int]:
    payload = {
        "userId": company["user_id"],
        "agentId": company["agent_id"],
        "query": company["company_name"],
        "strategy": strategy,
        "trace": trace,
    }
    status_code, response = post_json(f"{server}/open/v1/memory/retrieve", payload, timeout=30)
    status, items, _, _, _ = extract_response_items(response)
    return status_code, status, len(items)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--trace", default="true")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--benchmark-dir", default=str(DEFAULT_BENCHMARK_DIR))
    parser.add_argument("--limit-per-company", type=int, default=0)
    parser.add_argument(
        "--companies",
        nargs="*",
        default=[item["company_id"] for item in COMPANIES],
        help="Subset of company IDs to test, e.g. C019 C020",
    )
    args = parser.parse_args()

    benchmark_dir = Path(args.benchmark_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = [item for item in COMPANIES if item["company_id"] in set(args.companies)]
    if not selected:
        raise SystemExit(f"No matching companies found for {args.companies}")

    # Basic server sanity check.
    health_status, health_body = post_json(f"{args.server}/open/v1/health", {}, timeout=15)
    if health_status == 0:
        print(f"[WARN] health check failed: {health_body.get('error')}")
    else:
        print(f"[INFO] health status: {health_status}")

    # Probe the exact requested memory IDs once so we can separate
    # routing/identity problems from general retrieval quality.
    available_companies: list[dict[str, str]] = []
    unavailable_companies: list[dict[str, str]] = []
    for company in selected:
        probe_status, probe_result_status, probe_items = probe_company_space(
            args.server, company, args.strategy, args.trace.lower() == "true"
        )
        print(
            f"[PROBE] {company['company_id']} {company['company_name']} "
            f"HTTP={probe_status} status={probe_result_status} items={probe_items}"
        )
        if probe_status >= 200 and probe_status < 300 and probe_items > 0:
            available_companies.append(company)
        else:
            unavailable_companies.append(company)

    if unavailable_companies:
        print("[WARN] skipping companies that are not retrievable right now:")
        for company in unavailable_companies:
            print(f"  - {company['company_id']} {company['company_name']}")

    selected = available_companies
    if not selected:
        print("[ERROR] no companies are retrievable; stopping early.")
        summary_path = out_dir / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "server": args.server,
                    "strategy": args.strategy,
                    "trace": args.trace.lower() == "true",
                    "out_dir": str(out_dir),
                    "companies": [],
                    "skipped_companies": unavailable_companies,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (out_dir / "summary.csv").write_text(
            "company_id,company_name,qa_id,status,item_count,match_rank,top1,question,expected_answer,raw_path\n",
            encoding="utf-8",
        )
        return 0

    rows: list[ResultRow] = []
    summary: dict[str, Any] = {
        "server": args.server,
        "strategy": args.strategy,
        "trace": args.trace.lower() == "true",
        "out_dir": str(out_dir),
        "companies": [],
    }

    for company in selected:
        file_path = benchmark_dir / company["file_name"]
        if not file_path.exists():
            print(f"[WARN] missing benchmark file: {file_path}")
            continue

        qa_items = load_company_qa(file_path)
        if args.limit_per_company > 0:
            qa_items = qa_items[: args.limit_per_company]

        company_dir = out_dir / company["company_id"]
        company_dir.mkdir(parents=True, exist_ok=True)

        print("=" * 100)
        print(f"Company: {company['company_id']} {company['company_name']}")
        print(f"QA items: {len(qa_items)}")
        company_stats = {
            "company_id": company["company_id"],
            "company_name": company["company_name"],
            "qa_count": len(qa_items),
            "success_count": 0,
            "match_count": 0,
            "top1_match_count": 0,
        }

        for idx, qa in enumerate(qa_items, 1):
            qa_id = first_nonempty(qa.get("qa_id"), f"{company['company_id']}-{idx}")
            question = first_nonempty(qa.get("question"))
            expected_answer = first_nonempty(qa.get("answer"))
            raw_path = company_dir / f"{idx:03d}-{qa_id}.json"
            payload = {
                "userId": company["user_id"],
                "agentId": company["agent_id"],
                "query": question,
                "strategy": args.strategy,
                "trace": args.trace.lower() == "true",
            }
            status_code, response = post_json(f"{args.server}/open/v1/memory/retrieve", payload, timeout=90)
            raw_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")

            status, items_list, _, _, _ = extract_response_items(response)
            match_rank, matched_text = find_match_rank(items_list, expected_answer)
            top1 = first_nonempty(items_list[0].get("text") if items_list else "") or None

            if status_code >= 200 and status_code < 300:
                company_stats["success_count"] += 1
            if match_rank is not None:
                company_stats["match_count"] += 1
                if match_rank == 1:
                    company_stats["top1_match_count"] += 1

            rows.append(
                ResultRow(
                    company_id=company["company_id"],
                    company_name=company["company_name"],
                    qa_id=qa_id,
                    question=question,
                    expected_answer=expected_answer,
                    status=status,
                    item_count=len(items_list),
                    top1=top1,
                    match_rank=match_rank,
                    matched_text=matched_text,
                    raw_path=str(raw_path),
                )
            )

            print(f"[{idx:03d}] {qa_id}")
            print(f"  Q: {question}")
            print(f"  A: {expected_answer}")
            print(f"  HTTP: {status_code}  status={status}  items={len(items_list)}  match_rank={match_rank}")
            if top1:
                print(f"  top1: {top1}")

        summary["companies"].append(company_stats)

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_lines = [
        "company_id,company_name,qa_id,status,item_count,match_rank,top1,question,expected_answer,raw_path"
    ]
    for row in rows:
        def esc(value: str | None) -> str:
            value = value or ""
            return '"' + value.replace('"', '""') + '"'

        csv_lines.append(
            ",".join(
                [
                    esc(row.company_id),
                    esc(row.company_name),
                    esc(row.qa_id),
                    esc(row.status or ""),
                    str(row.item_count),
                    "" if row.match_rank is None else str(row.match_rank),
                    esc(row.top1),
                    esc(row.question),
                    esc(row.expected_answer),
                    esc(row.raw_path),
                ]
            )
        )
    (out_dir / "summary.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    print("=" * 100)
    print(f"Done. Summary written to: {summary_path}")
    print(f"CSV written to: {out_dir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

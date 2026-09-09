#!/usr/bin/env python3
"""
问答评测低分复核脚本（独立工具，不影响现有评测代码）。

目标：
- 只复核 report.md 中的 0 分/1 分题目
- 用“事实点覆盖法”给出是否建议修改，以及判断与原因
- 原始内容保留：问题 / 预期答案 / 模型回答 / 原始评分
- 输出单独的复核报告与 JSONL，支持断点续跑

输出目录示例：
    benchmark-results/qa-review/<时间戳>/
        review.jsonl
        review-summary.json
        review-report.md

断点续跑：
- 通过 --resume 指向一个已有复核目录
- 已完成的题目按 company_id + qa_id 跳过，继续追加未完成部分
- 即使最后一行 JSON 截断，读取时也会自动忽略损坏行

输入建议：
- 直接指定评测报告 report.md；脚本从中提取 0/1 分题目
- 复核提示词只喂问题 / 预期答案 / 模型回答，避免锚定原始分数

示例：
    python3 dev-tools/python/review_low_scores.py \
      --report benchmark-results/qa-evaluation/20260907-111713/report.md \
      --model qwen3.7-plus
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

from memind_shared import load_env

DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DEFAULT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "qwen3.7-plus")
DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_OUT_DIR = "benchmark-results/qa-review"


@dataclass
class QARecord:
    company_id: str
    qa_id: str
    question: str
    expected_answer: str
    answer: str
    original_score: int | None
    raw_block: list[str]


FACT_SYSTEM_PROMPT = (
    "你是问答评测复核助手。任务是根据问题、预期答案、模型回答，判断原始评分是否偏低。"
    "请采用‘事实点覆盖法’：先从预期答案中抽取关键事实点，再判断模型回答是否覆盖这些事实点。"
    "不要参考原始评分做锚定，不要输出推理过程，只给出结论和简短原因。"
    "\n\n评分规则："
    "\n1) 如果预期答案是‘无法确定’类，而模型回答也明确表达无法确定（哪怕措辞不同），建议 2 分。"
    "\n2) 如果预期答案包含具体事实，而模型回答明确覆盖了全部关键事实点，只是措辞不同，建议 2 分。"
    "\n3) 如果模型回答覆盖了主要事实，但缺少部分次要信息，建议 1 分。"
    "\n4) 如果关键事实点缺失、矛盾，或者模型回答为‘无法确定’但预期答案是具体事实，建议 0 分。"
    "\n5) 仅在模型回答与预期答案存在明显事实偏差时建议修改为更低分；若原始评分已合理则不建议修改。"
    "\n\n请输出严格 JSON，字段必须包含："
    "\n- recommend_modify: boolean"
    "\n- suggested_score: integer (0/1/2)"
    "\n- judgment: string (必须是以下之一：全覆盖 / 部分覆盖 / 关键事实缺失 / 正确拒答 / 明显矛盾)"
    "\n- reason: string (一句到三句，说明为什么建议修改或不修改)"
    "\n- fact_points: array[string] (从预期答案拆出的关键事实点)"
    "\n- covered_points: array[string] (模型回答明确覆盖的事实点)"
    "\n- missing_points: array[string] (未覆盖或矛盾的事实点)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="问答评测低分复核（独立工具）")
    parser.add_argument("--report", required=True, help="评测报告 report.md 路径")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="复核输出根目录")
    parser.add_argument("--resume", default="", help="续跑目录（已有 qa-review/<时间戳>）")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="复核模型")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI 兼容接口地址")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API Key；默认读环境变量")
    parser.add_argument("--companies", nargs="*", default=[], help="只复核指定公司")
    parser.add_argument("--limit", type=int, default=0, help="最多复核多少条 0=不限")
    parser.add_argument("--dry-run", action="store_true", help="只解析与统计，不调用 LLM")
    parser.add_argument("--retry-failed", action="store_true", help="续跑时重试上次失败记录")
    parser.add_argument(
        "--rebuild-report",
        action="store_true",
        help="不调用 LLM，仅根据 --resume 目录下已有 review.jsonl 重新生成报告",
    )
    return parser.parse_args()


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    print(msg, flush=True)


REPORT_RE = re.compile(r"^### \[(?P<company_id>[^\]]+)\] (?P<qa_id>\S+)\s*$")


def parse_report(report_path: Path) -> list[QARecord]:
    """从 report.md 提取逐题明细。仅返回 0/1 分的题目。"""
    lines = report_path.read_text(encoding="utf-8").splitlines()
    records: list[QARecord] = []
    i = 0
    while i < len(lines):
        m = REPORT_RE.match(lines[i])
        if not m:
            i += 1
            continue
        company_id = m.group("company_id")
        qa_id = m.group("qa_id")
        block = [lines[i]]
        i += 1
        question = expected = answer = ""
        original_score: int | None = None
        while i < len(lines) and not lines[i].startswith("### ["):
            line = lines[i]
            block.append(line)
            if line.startswith("- 问题: "):
                question = line[len("- 问题: "):].strip()
            elif line.startswith("- 预期答案: "):
                expected = line[len("- 预期答案: "):].strip()
            elif line.startswith("- 模型回答: "):
                answer = line[len("- 模型回答: "):].strip()
            elif line.startswith("- **评分**: "):
                score_text = line[len("- **评分**: "):].strip()
                m_score = re.match(r"(?P<score>[012])/2", score_text)
                if m_score:
                    original_score = int(m_score.group("score"))
            i += 1
        if original_score in (0, 1) and question and expected and answer:
            records.append(
                QARecord(
                    company_id=company_id,
                    qa_id=qa_id,
                    question=question,
                    expected_answer=expected,
                    answer=answer,
                    original_score=original_score,
                    raw_block=block,
                )
            )
    return records


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def post_json(url: str, payload: dict, api_key: str, timeout: int = 120) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    try:
        data = resp.json()
    except Exception:
        data = {"error": resp.text}
    return resp.status_code, data


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取第一个 JSON 对象
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise


def build_review_payload(record: QARecord) -> list[dict]:
    user_prompt = (
        f"问题：{record.question}\n"
        f"预期答案：{record.expected_answer}\n"
        f"模型回答：{record.answer}\n\n"
        "请判断原始评分是否偏低，并按要求输出 JSON。"
    )
    return [
        {"role": "system", "content": FACT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def review_one(record: QARecord, model: str, base_url: str, api_key: str, timeout: int = 120) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": build_review_payload(record),
        "temperature": 0,
        "max_tokens": 800,
    }
    status, data = post_json(url, payload, api_key=api_key, timeout=timeout)
    if status != 200:
        return {
            "company_id": record.company_id,
            "qa_id": record.qa_id,
            "review_error": f"HTTP {status}: {json.dumps(data, ensure_ascii=False)}",
        }
    try:
        content = data["choices"][0]["message"]["content"]
        result = extract_json(content)
    except Exception as exc:
        return {
            "company_id": record.company_id,
            "qa_id": record.qa_id,
            "review_error": f"parse_error: {exc}",
            "raw_response": data,
        }

    suggested_score = result.get("suggested_score")
    if suggested_score not in (0, 1, 2):
        result["suggested_score"] = None
        result["review_error"] = "invalid suggested_score"
    result.setdefault("recommend_modify", False)
    result.setdefault("judgment", "")
    result.setdefault("reason", "")
    result.setdefault("fact_points", [])
    result.setdefault("covered_points", [])
    result.setdefault("missing_points", [])
    result["company_id"] = record.company_id
    result["qa_id"] = record.qa_id
    result["question"] = record.question
    result["expected_answer"] = record.expected_answer
    result["answer"] = record.answer
    result["original_score"] = record.original_score
    result["review_error"] = None
    return result


def summarize(rows: list[dict]) -> dict:
    reviewed = [r for r in rows if not r.get("review_error")]
    recommend_modify = [r for r in reviewed if r.get("recommend_modify")]
    no_modify = [r for r in reviewed if not r.get("recommend_modify")]
    improved = [r for r in reviewed if r.get("original_score") is not None and r.get("suggested_score") is not None and r["suggested_score"] > r["original_score"]]
    worsened = [r for r in reviewed if r.get("original_score") is not None and r.get("suggested_score") is not None and r["suggested_score"] < r["original_score"]]

    orig_scores = [r["original_score"] for r in reviewed if isinstance(r.get("original_score"), int)]
    new_scores = [r["suggested_score"] for r in reviewed if isinstance(r.get("suggested_score"), int)]
    zero_rate_before = round(sum(1 for s in orig_scores if s == 0) / len(orig_scores) * 100, 2) if orig_scores else 0
    zero_rate_after = round(sum(1 for s in new_scores if s == 0) / len(new_scores) * 100, 2) if new_scores else 0
    avg_before = round(sum(orig_scores) / len(orig_scores), 3) if orig_scores else 0
    avg_after = round(sum(new_scores) / len(new_scores), 3) if new_scores else 0

    return {
        "reviewed_count": len(reviewed),
        "recommend_modify_count": len(recommend_modify),
        "no_modify_count": len(no_modify),
        "improved_count": len(improved),
        "worsened_count": len(worsened),
        "avg_before": avg_before,
        "avg_after": avg_after,
        "zero_rate_before": zero_rate_before,
        "zero_rate_after": zero_rate_after,
    }


def _score_dist(scores) -> dict:
    dist = {0: 0, 1: 0, 2: 0}
    for s in scores:
        if isinstance(s, int) and s in dist:
            dist[s] += 1
    return dist


def compute_overall(eval_summary_path: Path, rows: list[dict]) -> dict | None:
    """联算全量总平均分：原始评测 summary.json + 复核建议分。

    口径A：仅采纳 recommend_modify=True 的建议分
    口径B：凡 suggested_score 与原分不同都采纳
    """
    if not eval_summary_path.exists():
        return None
    try:
        eval_summary = json.loads(eval_summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"[WARN] 评测 summary 读取失败: {exc}")
        return None

    records = eval_summary.get("records") or []
    orig_scores = {
        (r.get("company_id"), r.get("qa_id")): r.get("judge_score")
        for r in records
        if isinstance(r.get("judge_score"), int)
    }
    if not orig_scores:
        return None

    valid_reviews = [r for r in rows if not r.get("review_error") and isinstance(r.get("suggested_score"), int)]

    applied_a = 0
    new_scores_a = dict(orig_scores)
    applied_b = 0
    new_scores_b = dict(orig_scores)
    for r in valid_reviews:
        key = (r.get("company_id"), r.get("qa_id"))
        if key not in new_scores_a:
            continue
        if r.get("recommend_modify") and r["suggested_score"] != new_scores_a[key]:
            new_scores_a[key] = r["suggested_score"]
            applied_a += 1
        if r["suggested_score"] != new_scores_b[key]:
            new_scores_b[key] = r["suggested_score"]
            applied_b += 1

    def stats(scores: dict) -> dict:
        vals = list(scores.values())
        dist = _score_dist(vals)
        n = len(vals)
        return {
            "avg": round(sum(vals) / n, 3) if n else 0,
            "dist": dist,
            "zero_rate": round(dist[0] / n * 100, 2) if n else 0,
        }

    return {
        "total_questions": len(records),
        "scored_questions": len(orig_scores),
        "reviewed_overlap": sum(1 for r in valid_reviews if (r.get("company_id"), r.get("qa_id")) in orig_scores),
        "original": stats(orig_scores),
        "modeA_applied": applied_a,
        "modeA": stats(new_scores_a),
        "modeB_applied": applied_b,
        "modeB": stats(new_scores_b),
    }


def _attach_overall(summary: dict, report_path: Path, rows: list[dict]) -> None:
    """尝试联算全量总平均分并写入 summary['overall']；失败则静默跳过。"""
    eval_summary_path = report_path.parent / "summary.json"
    overall = compute_overall(eval_summary_path, rows)
    if overall:
        summary["overall"] = overall


def _anchor_id(company_id: str, qa_id: str) -> str:
    """生成稳定的文档内锚点 id（兼容中文）。"""
    slug = f"{company_id}-{qa_id}".strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^\w\u4e00-\u9fff\-]", "", slug)
    return slug


def _overall_md(summary: dict) -> list[str]:
    """渲染全量总平均分对比区块（有 overall 数据才输出）。"""
    ov = summary.get("overall")
    if not ov:
        return []
    o, a, b = ov["original"], ov["modeA"], ov["modeB"]
    return [
        "\n## 复核后全量总平均分\n",
        f"- 评测题数: {ov['total_questions']}（有效评分 {ov['scored_questions']}，复核覆盖其中 {ov['reviewed_overlap']} 条）",
        "",
        "| 口径 | 平均分 | 2分 | 1分 | 0分 | 0分率 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 原始评测 | {o['avg']:.3f} | {o['dist'][2]} | {o['dist'][1]} | {o['dist'][0]} | {o['zero_rate']}% |",
        f"| A: 仅采纳建议修改（{ov['modeA_applied']}条） | {a['avg']:.3f} | {a['dist'][2]} | {a['dist'][1]} | {a['dist'][0]} | {a['zero_rate']}% |",
        f"| B: 凡复核分不同全采纳（{ov['modeB_applied']}条） | {b['avg']:.3f} | {b['dist'][2]} | {b['dist'][1]} | {b['dist'][0]} | {b['zero_rate']}% |",
        "",
    ]


def render_report(source_report: Path, rows: list[dict], summary: dict, model: str) -> str:
    lines: list[str] = []
    lines.append("# 低分复核报告（中文）\n")
    lines.append(f"- 原始报告: {source_report}")
    lines.append(f"- 复核模型: {model}")
    lines.append(f"- 复核时间: {dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 复核题数: {summary['reviewed_count']}")
    lines.append(f"- 建议修改: {summary['recommend_modify_count']}")
    lines.append(f"- 不建议修改: {summary['no_modify_count']}")
    lines.append(f"- 原均分: {summary['avg_before']:.3f} → 复核均分: {summary['avg_after']:.3f}")
    lines.append(f"- 原 0分率: {summary['zero_rate_before']}% → 复核 0分率: {summary['zero_rate_after']}%")
    lines.extend(_overall_md(summary))

    lines.append('<a id="suspicion-list"></a>')
    lines.append("## 判低嫌疑清单\n")
    lines.append("| 公司 | 题目 | 原分 | 复核分 | 差值 | 是否建议修改 | 判断 | 原因 |")
    lines.append("|---|---|---:|---:|---:|---|---|---|")

    def _delta(r: dict) -> int:
        if isinstance(r.get("original_score"), int) and isinstance(r.get("suggested_score"), int):
            return r["suggested_score"] - r["original_score"]
        return 0

    for r in sorted(rows, key=_delta, reverse=True):
        if r.get("review_error"):
            continue
        anchor = _anchor_id(r["company_id"], r["qa_id"])
        delta = _delta(r)
        modify_text = "是" if r.get("recommend_modify") else "否"
        lines.append(
            f"| {r['company_id']} | [{r['qa_id']}](#{anchor}) | {r['original_score']} | "
            f"{r['suggested_score']} | {delta:+d} | {modify_text} | {r.get('judgment','')} | {r.get('reason','')} |"
        )

    lines.append("\n## 逐题复核明细\n")
    for r in rows:
        anchor = _anchor_id(r["company_id"], r["qa_id"])
        lines.append(f'<a id="{anchor}"></a>')
        lines.append(f"### [{r['company_id']}] {r['qa_id']}")
        lines.append(f"- 问题: {r['question']}")
        lines.append(f"- 预期答案: {r['expected_answer']}")
        lines.append(f"- 模型回答: {r['answer']}")
        lines.append(f"- 原始评分: {r['original_score']}/2")
        if r.get("review_error"):
            lines.append("- **建议修改**: 无法复核")
            lines.append(f"- **原因**: {r['review_error']}")
            lines.append(f"\n↑ [返回判低嫌疑清单](#suspicion-list)\n")
            continue
        lines.append(f"- **建议修改**: {'是' if r['recommend_modify'] else '否'}")
        if r.get("recommend_modify") and isinstance(r.get("suggested_score"), int):
            lines.append(f"- **修改后评分**: {r['suggested_score']}/2（复核建议）")
        lines.append(f"- **复核判断**: {r.get('judgment','')}")
        lines.append(f"- **原因**: {r.get('reason','')}")
        if r.get("fact_points"):
            lines.append(f"- **关键事实点**: {json.dumps(r['fact_points'], ensure_ascii=False)}")
        if r.get("covered_points"):
            lines.append(f"- **已覆盖事实点**: {json.dumps(r['covered_points'], ensure_ascii=False)}")
        if r.get("missing_points"):
            lines.append(f"- **未覆盖/矛盾事实点**: {json.dumps(r['missing_points'], ensure_ascii=False)}")
        lines.append(f"\n↑ [返回判低嫌疑清单](#suspicion-list)\n")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    load_env()

    report_path = Path(args.report)
    if not report_path.exists():
        log(f"[FATAL] report 不存在: {report_path}")
        return 1

    source_name = report_path.parent.name
    if args.resume:
        run_dir = Path(args.resume)
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = Path(args.out_dir) / now_stamp()
        run_dir.mkdir(parents=True, exist_ok=True)

    review_jsonl = run_dir / "review.jsonl"
    summary_path = run_dir / "review-summary.json"
    report_out = run_dir / "review-report.md"

    log("=" * 100)
    log(f"来源报告: {report_path}")
    log(f"输出目录: {run_dir}")
    log(f"复核模型: {args.model}")
    log(f"Base URL: {args.base_url}")
    log(f"API Key: {'set' if args.api_key else 'empty'}")
    log("=" * 100)

    if not args.api_key and not args.rebuild_report and not args.dry_run:
        log("[FATAL] 未提供 API Key（环境变量 OPENAI_API_KEY 或 --api-key）")
        return 1

    records = parse_report(report_path)
    if args.companies:
        selected = {c.upper() for c in args.companies}
        records = [r for r in records if r.company_id in selected]
    if args.limit > 0:
        records = records[: args.limit]

    log(f"待复核题数: {len(records)}（只包含原始评分 0/1）")

    # 仅重生成报告：不调用 LLM，只依据已有 review.jsonl
    if args.rebuild_report:
        existing = load_jsonl(review_jsonl)
        if not existing:
            log("[FATAL] --rebuild-report 需要 --resume 目录下已有 review.jsonl")
            return 1
        summary = summarize(existing)
        summary.update(
            {
                "source_report": str(report_path),
                "resume_dir": str(run_dir),
                "model": args.model,
                "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "total_source_records": len(records),
                "newly_reviewed": 0,
            }
        )
        _attach_overall(summary, report_path, existing)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        report_out.write_text(render_report(report_path, existing, summary, args.model), encoding="utf-8")
        log("=" * 100)
        log(f"已根据 {len(existing)} 条 review.jsonl 重新生成报告（未调用 LLM）")
        log(f"建议修改: {summary['recommend_modify_count']} 条")
        if summary.get("overall"):
            ov = summary["overall"]
            log(
                f"全量总平均分: 原始 {ov['original']['avg']:.3f} → "
                f"口径A(采纳{ov['modeA_applied']}条) {ov['modeA']['avg']:.3f} | "
                f"口径B(采纳{ov['modeB_applied']}条) {ov['modeB']['avg']:.3f}"
            )
        log(f"汇总文件: {summary_path}")
        log(f"复核报告: {report_out}")
        return 0

    existing = load_jsonl(review_jsonl)
    existing_keys = {(r.get("company_id"), r.get("qa_id")) for r in existing if r.get("company_id") and r.get("qa_id")}
    if args.retry_failed:
        existing_keys = {
            (r.get("company_id"), r.get("qa_id"))
            for r in existing
            if r.get("company_id") and r.get("qa_id") and not r.get("review_error")
        }

    results: list[dict] = list(existing)
    new_rows: list[dict] = []

    reviewed_now = 0
    for idx, record in enumerate(records, 1):
        key = (record.company_id, record.qa_id)
        if key in existing_keys:
            continue
        reviewed_now += 1
        log(f"[{idx:03d}] {record.company_id} {record.qa_id} 复核中...")
        if args.dry_run:
            row = {
                "company_id": record.company_id,
                "qa_id": record.qa_id,
                "question": record.question,
                "expected_answer": record.expected_answer,
                "answer": record.answer,
                "original_score": record.original_score,
                "recommend_modify": False,
                "suggested_score": None,
                "judgment": "dry_run",
                "reason": "dry_run",
                "fact_points": [],
                "covered_points": [],
                "missing_points": [],
                "review_error": None,
            }
        else:
            row = review_one(record, args.model, args.base_url, args.api_key)
        results.append(row)
        new_rows.append(row)
        write_jsonl(review_jsonl, [row])

    # 兜底：把已存在的内容也标准化写回 summary/report
    final_results = load_jsonl(review_jsonl)
    summary = summarize(final_results)
    summary.update(
        {
            "source_report": str(report_path),
            "resume_dir": str(run_dir),
            "model": args.model,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "total_source_records": len(records),
            "newly_reviewed": reviewed_now,
        }
    )
    _attach_overall(summary, report_path, final_results)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_out.write_text(render_report(report_path, final_results, summary, args.model), encoding="utf-8")

    log("=" * 100)
    log(f"复核完成: {len(final_results)} 条")
    log(f"建议修改: {summary['recommend_modify_count']} 条")
    if summary.get("overall"):
        ov = summary["overall"]
        log(
            f"全量总平均分: 原始 {ov['original']['avg']:.3f} → "
            f"口径A(采纳{ov['modeA_applied']}条) {ov['modeA']['avg']:.3f} | "
            f"口径B(采纳{ov['modeB_applied']}条) {ov['modeB']['avg']:.3f}"
        )
    log(f"结果目录: {run_dir}")
    log(f"汇总文件: {summary_path}")
    log(f"复核报告: {report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

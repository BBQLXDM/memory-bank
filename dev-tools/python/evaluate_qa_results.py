#!/usr/bin/env python3
"""
评测脚本（第二阶段）—— 读取 generate_qa_answers.py 的结果文件进行评测。

评测规则：
  1. 记忆检索：Hit@5 + MRR（标准答案关键信息是否出现在检索结果 items 前 5 条中）
  2. 回答评测：LLM 裁判按 JUDGE_PROMPT 对生成回答打分 0/1/2（2=完全包含关键事实，1=部分，0=不包含）
  3. 最终分析：分公司 + 总体的检索指标、评分分布、平均分等

用法：
    python3 dev-tools/python/evaluate_qa_results.py \
        benchmark-results/qa-answers/<时间戳>/results.json \
        --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
        --llm-model qwen3.7-flash \
        --llm-api-key sk-xxx
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from memind_shared import load_env

load_env()

JUDGE_PROMPT = """你是一个金融数据评估专家。请判断"检索到的记忆"是否包含了"标准答案"中的关键事实信息。

标准答案: {ground_truth}

检索到的记忆: {retrieved}

评分规则:
- 2: 检索结果完全包含了标准答案的关键事实（数值、日期、名称等核心信息一致）
- 1: 检索结果部分包含了标准答案的关键事实
- 0: 检索结果不包含标准答案的关键事实

正确拒答规则（重要）:
- 如果"标准答案"本身表明该信息无法确定或不存在的（例如以"无法确定"、"未记录"、"无相关信息"等表述开头），
  而"检索到的记忆"的回答也表达了无法确定 / 不知道 / 未记录的意思，
  则属于"正确的拒答"，直接给 2 分；不要求回答附带解释原因，简短的"无法确定"即可得满分。
- 反之，如果"标准答案"包含明确的具体事实，而回答却说无法确定或未提及该事实，则给 0 分。

只回答数字(0/1/2):"""

RETRIEVAL_HIT_PROMPT = """你是一个金融数据评估专家。请判断下面"检索到的记忆条目"中，哪一条最可能包含了"标准答案"中的关键事实信息（数值、日期、名称等核心信息一致即可，语言中英文均可）。

标准答案: {ground_truth}

检索到的记忆条目:
{numbered_texts}

只回答一个数字，表示第一条包含标准答案关键事实的条目序号（1到{top_k}）；如果没有任何条目包含，回答 0。

只回答数字:"""


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


def load_results(path: Path) -> tuple[dict, list[dict]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, dict):
        return obj, obj.get("records", [])
    return {}, obj


_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_EN_MONTHS = {
    "1": "January", "2": "February", "3": "March", "4": "April",
    "5": "May", "6": "June", "7": "July", "8": "August",
    "9": "September", "10": "October", "11": "November", "12": "December",
}


def _cn_number_to_int(text: str) -> int | None:
    """把中文数字串（如 两千一百万）转成整数，失败返回 None。"""
    if not text:
        return None
    try:
        if text.isdigit():
            return int(text)
    except ValueError:
        pass
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in _CN_NUM:
            number = _CN_NUM[ch]
        elif ch == "十":
            section += number * 10 if number else 10
            number = 0
        elif ch == "百":
            section += number * 100 if number else 100
            number = 0
        elif ch == "千":
            section += number * 1000 if number else 1000
            number = 0
        elif ch == "万":
            total = (total + section + number) * 10000
            section = 0
            number = 0
        elif ch == "亿":
            total = (total + section + number) * 100000000
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _to_english_variants(value: str) -> set[str]:
    """把预期答案中的中文金额/日期/百分数转成常见英文表达，供匹配英文记忆文本。"""
    variants: set[str] = set()
    text = value or ""

    # 金额：N万元 / N亿元（含小数，如 3.5万元、2100万元）
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([万亿]?)(元)?", text):
        num = float(m.group(1))
        unit = m.group(2) or ""
        factor = {"万": 10000, "亿": 100000000}.get(unit, 1)
        total = int(num * factor)
        if total <= 0:
            continue
        if total >= 1000000:
            variants.add(f"{total:,}")                      # 21,000,000
            variants.add(str(total))                        # 21000000
            variants.add(f"{total / 1000000:.1f} million".rstrip(".0"))
            variants.add(f"{total / 1000000:g} million")
            variants.add(f"{total / 1000000:g} m")
            variants.add(f"{total / 1000000:g}m")
        elif total >= 10000:
            variants.add(f"{total:,}")
            variants.add(str(total))
            variants.add(f"{total / 10000:g} 万")
            variants.add(f"{total / 10000:g}wan")
        # 纯数字（无单位）也加入（英文文本里可能直接用数字）
        if total >= 1000:
            variants.add(f"{total:,}")

    # 日期：2025年12月 / 2026年3月8日
    for m in re.finditer(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?", text):
        year, month = m.group(1), m.group(2).lstrip("0")
        day = (m.group(3) or "").lstrip("0")
        month_name = _EN_MONTHS.get(month)
        variants.add(f"{year}-{month}")                      # 2025-12
        variants.add(f"{year}-{int(month):02d}")
        if day:
            variants.add(f"{year}-{int(month):02d}-{int(day):02d}")
            if month_name:
                variants.add(f"{month_name} {day}, {year}")  # March 8, 2026
                variants.add(f"{month_name} {int(day)}, {year}")
        elif month_name:
            variants.add(f"{month_name} {year}")             # December 2025

    # 百分数
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*[%％]", text):
        pct = m.group(1)
        variants.add(f"{pct}%")
        variants.add(f"{pct} percent")
        variants.add(f"{pct} per cent")
        variants.add(f"{pct} %")

    # 中文数字金额（如 三千五百万元）
    for m in re.finditer(r"([零一二两三四五六七八九十百千万亿]+)\s*万元", text):
        total = _cn_number_to_int(m.group(1))
        if total:
            total *= 10000
            variants.add(f"{total:,}")
            variants.add(str(total))
            variants.add(f"{total / 1000000:g} million")
    return {v for v in variants if v}


def normalize_answer(value: str) -> list[str]:
    """把预期答案展开成多个匹配变体：完整串、分隔符前缀、含数字的关键片段、中英文数值/日期表达。"""
    text = (value or "").strip()
    if not text:
        return []
    variants = {text}
    for sep in ("——", "：", "-", "，", ","):
        if sep in text:
            variants.add(text.split(sep, 1)[0].strip())
    for m in re.finditer(r"[\d年月日号批量比%％.万亿千百元]+", text):
        seg = m.group(0)
        # 纯数字短片段（<4）太泛化，容易误命中，跳过
        if re.fullmatch(r"\d{1,3}(?:\.\d+)?", seg):
            continue
        if len(seg) >= 4 and seg not in variants:
            variants.add(seg)
        elif len(seg) >= 2:
            variants.add(seg)
    variants.update(_to_english_variants(text))
    long_variants = {v for v in variants if len(v) >= 2}
    return sorted(long_variants, key=len, reverse=True)


def extract_item_texts(retrieval: dict, top_k: int) -> list[str]:
    """从检索响应中提取前 top_k 条 items 的文本。"""
    if not isinstance(retrieval, dict):
        return []
    result = retrieval.get("data") or retrieval.get("result") or retrieval
    items = result.get("items") or []
    texts = []
    for item in items[:top_k]:
        text = first_nonempty(item.get("text"), item.get("content"), item.get("caption"), json.dumps(item, ensure_ascii=False))
        texts.append(text)
    return texts


def compute_retrieval_metrics(expected_answer: str, texts: list[str], top_k: int) -> dict:
    """计算检索指标：Hit@K、MRR。标准答案任一关键片段命中即视为命中。"""
    variants = normalize_answer(expected_answer)
    hit_rank = None
    if variants:
        for idx, text in enumerate(texts, 1):
            if any(variant in text for variant in variants):
                hit_rank = idx
                break
    return {
        "top_k": top_k,
        "hit": hit_rank is not None,
        "hit_rank": hit_rank,
        "mrr": (1.0 / hit_rank) if hit_rank else 0.0,
    }


def call_judge_llm(base_url: str, model: str, api_key: str, ground_truth: str, answer: str, timeout: int) -> tuple[int | None, str | None]:
    """调用 LLM 裁判，返回 (score, error)。score 为 0/1/2，解析失败返回 None。"""
    prompt = JUDGE_PROMPT.format(ground_truth=ground_truth, retrieved=answer)
    content, error = call_llm_once(base_url, model, api_key, prompt, timeout)
    if error:
        return None, error
    match = re.search(r"[012]", content or "")
    if not match:
        return None, f"judge response no score: {content}"
    return int(match.group(0)), None


def call_retrieval_hit_llm(
    base_url: str, model: str, api_key: str, ground_truth: str, texts: list[str], timeout: int
) -> tuple[int | None, str | None]:
    """用 LLM 判断检索结果 top-K 中哪条包含标准答案关键事实。

    返回 (rank, error)。rank 为 1..len(texts) 表示第几条命中，0 表示都不包含，
    解析失败返回 (None, error)。
    """
    numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(texts, 1))
    prompt = RETRIEVAL_HIT_PROMPT.format(
        ground_truth=ground_truth, numbered_texts=numbered, top_k=len(texts)
    )
    content, error = call_llm_once(base_url, model, api_key, prompt, timeout)
    if error:
        return None, error
    match = re.search(r"\d+", content or "")
    if not match:
        return None, f"hit judge response no number: {content}"
    rank = int(match.group(0))
    if rank > len(texts):
        return None, f"hit judge rank out of range: {rank}"
    return rank, None


def call_llm_once(base_url: str, model: str, api_key: str, user_prompt: str, timeout: int) -> tuple[str | None, str | None]:
    """调用一次 LLM，返回 (content, error)。"""
    payload = {"model": model, "messages": [{"role": "user", "content": user_prompt}], "temperature": 0}
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        status, response = post_json(
            f"{base_url.rstrip('/')}/chat/completions", payload, headers=headers, timeout=timeout
        )
        if status >= 400:
            return None, f"LLM HTTP {status}: {json.dumps(response, ensure_ascii=False)}"
        choices = response.get("choices") or []
        if not choices:
            return None, f"LLM response missing choices: {json.dumps(response, ensure_ascii=False)}"
        message = (choices[0] or {}).get("message") or {}
        content = first_nonempty(message.get("content"), (choices[0] or {}).get("text"))
        if content:
            return content.strip(), None
        return None, f"LLM response empty content: {json.dumps(response, ensure_ascii=False)}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="评测（第二阶段）")
    parser.add_argument("results", help="generate_qa_answers.py 生成的 results.json 路径")
    parser.add_argument("--llm-base-url", default=env("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    parser.add_argument("--llm-model", default=env("LLM_MODEL", env("OPENAI_CHAT_MODEL", "qwen3.7-flash")))
    parser.add_argument("--llm-api-key", default=env("LLM_API_KEY", env("OPENAI_API_KEY", "")))
    parser.add_argument("--judge-timeout", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=5, help="检索评测 K 值")
    parser.add_argument("--llm-hit", action="store_true",
                        help="字符串匹配未命中时用 LLM 判断检索是否包含标准答案（中英文语义匹配）")
    parser.add_argument("--out-dir", default=None,
                        help="评测输出目录；默认跟随输入目录名 qa-answers/<run> -> qa-evaluation/<run>")
    args = parser.parse_args()

    results_path = Path(args.results)
    if not results_path.exists():
        print(f"[ERROR] results file not found: {results_path}")
        return 1

    manifest, records = load_results(results_path)
    if not records:
        print(f"[ERROR] no records in {results_path}")
        return 1

    if args.out_dir:
        run_dir = Path(args.out_dir)
    else:
        # 输入 benchmark-results/qa-answers/<run>/results.json 时，输出到同名 qa-evaluation/<run>
        run_dir = (
            Path(os.getcwd())
            / "benchmark-results"
            / "qa-evaluation"
            / results_path.parent.name
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print(f"评测来源: {results_path}")
    print(f"记录数: {len(records)}")
    print(f"检索指标: Hit@{args.top_k} + MRR")
    print(f"回答评测: LLM 裁判 0/1/2 评分 (model={args.llm_model})")
    print(f"LLM api key: {'set' if args.llm_api_key else 'empty'}")
    print(f"输出目录: {run_dir}")
    print("=" * 100)

    evaluated = []
    company_stats = {}

    for idx, record in enumerate(records, 1):
        qa_id = record.get("qa_id", f"item-{idx}")
        expected_answer = first_nonempty(record.get("expected_answer"), record.get("answer"))
        question = record.get("question", "")
        retrieval = record.get("retrieval") or {}
        answer = record.get("answer", "")

        # 1. 检索指标
        texts = extract_item_texts(retrieval, args.top_k)
        retrieval_metrics = compute_retrieval_metrics(expected_answer, texts, args.top_k)

        # 字符串匹配未命中时，用 LLM 判断（中英文语义匹配）
        llm_hit_rank = None
        llm_hit_error = None
        if not retrieval_metrics["hit"] and args.llm_hit and texts and args.llm_api_key:
            llm_hit_rank, llm_hit_error = call_retrieval_hit_llm(
                args.llm_base_url, args.llm_model, args.llm_api_key,
                expected_answer, texts, args.judge_timeout,
            )
            if llm_hit_rank is not None and llm_hit_rank > 0:
                retrieval_metrics = {
                    "top_k": args.top_k,
                    "hit": True,
                    "hit_rank": llm_hit_rank,
                    "mrr": 1.0 / llm_hit_rank,
                }

        # 2. 回答评分
        judge_score = None
        judge_error = None
        if answer and not answer.startswith("[LLM_ERROR]"):
            judge_score, judge_error = call_judge_llm(
                args.llm_base_url, args.llm_model, args.llm_api_key,
                expected_answer, answer, args.judge_timeout,
            )
        elif answer.startswith("[LLM_ERROR]"):
            judge_error = answer

        company_id = record.get("company_id", "UNKNOWN")
        if company_id not in company_stats:
            company_stats[company_id] = {
                "company_id": company_id,
                "company_name": record.get("company_name", ""),
                "qa_count": 0,
                "hit_count": 0,
                "mrr_sum": 0.0,
                "score_2_count": 0,
                "score_1_count": 0,
                "score_0_count": 0,
                "score_fail_count": 0,
                "score_sum": 0.0,
            }
        stats = company_stats[company_id]
        stats["qa_count"] += 1
        if retrieval_metrics["hit"]:
            stats["hit_count"] += 1
        stats["mrr_sum"] += retrieval_metrics["mrr"]
        if judge_score is not None:
            stats["score_sum"] += judge_score
            if judge_score == 2:
                stats["score_2_count"] += 1
            elif judge_score == 1:
                stats["score_1_count"] += 1
            else:
                stats["score_0_count"] += 1
        else:
            stats["score_fail_count"] += 1

        evaluated.append({
            "company_id": company_id,
            "company_name": record.get("company_name", ""),
            "qa_id": qa_id,
            "question": question,
            "expected_answer": expected_answer,
            "answer": answer,
            "answer_error": record.get("answer_error"),
            "retrieval_item_count": record.get("item_count", 0),
            "retrieval_hit": retrieval_metrics["hit"],
            "retrieval_hit_rank": retrieval_metrics["hit_rank"],
            "retrieval_mrr": retrieval_metrics["mrr"],
            "retrieval_hit_method": "llm" if (llm_hit_rank and llm_hit_rank > 0) else "string",
            "retrieval_hit_error": llm_hit_error,
            "judge_score": judge_score,
            "judge_error": judge_error,
        })

        print(f"[{idx:03d}] {qa_id}")
        print(f"  Q: {question[:60]}")
        print(f"  预期: {expected_answer[:60]}")
        method = "LLM" if (llm_hit_rank and llm_hit_rank > 0) else "字符串"
        print(f"  检索: hit={'是' if retrieval_metrics['hit'] else '否'} rank={retrieval_metrics['hit_rank']} "
              f"MRR={retrieval_metrics['mrr']:.3f} ({method})")
        if llm_hit_error:
            print(f"  检索LLM判定错误: {llm_hit_error}")
        print(f"  回答: {answer[:100]}")
        print(f"  评分: {judge_score if judge_score is not None else 'N/A'} "
              f"{'(error: ' + judge_error + ')' if judge_error else ''}")

    # 汇总输出
    summary = {
        "source": str(results_path),
        "llm_model": args.llm_model,
        "top_k": args.top_k,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "companies": [],
        "totals": {},
        "records": evaluated,
    }

    total_qa = 0
    total_hit = 0
    total_mrr = 0.0
    total_score = 0.0
    total_scored = 0
    score_2 = score_1 = score_0 = score_fail = 0
    score_ge1_count = 0
    score_zero_rate_total = 0.0

    for cid in sorted(company_stats):
        s = company_stats[cid]
        s["hit_rate"] = round(s["hit_count"] / s["qa_count"] * 100, 2) if s["qa_count"] else 0
        s["mrr"] = round(s["mrr_sum"] / s["qa_count"], 4) if s["qa_count"] else 0
        scored = s["score_2_count"] + s["score_1_count"] + s["score_0_count"]
        s["avg_score"] = round(s["score_sum"] / scored, 3) if scored else 0
        s["score_ge1_rate"] = round((s["score_2_count"] + s["score_1_count"]) / scored * 100, 2) if scored else 0
        s["score_zero_rate"] = round(s["score_0_count"] / scored * 100, 2) if scored else 0
        summary["companies"].append(s)

        total_qa += s["qa_count"]
        total_hit += s["hit_count"]
        total_mrr += s["mrr_sum"]
        total_score += s["score_sum"]
        total_scored += scored
        score_2 += s["score_2_count"]
        score_1 += s["score_1_count"]
        score_0 += s["score_0_count"]
        score_fail += s["score_fail_count"]
        score_ge1_count += s["score_2_count"] + s["score_1_count"]

    summary["totals"] = {
        "qa_count": total_qa,
        "hit_count": total_hit,
        "hit_rate": round(total_hit / total_qa * 100, 2) if total_qa else 0,
        "mrr": round(total_mrr / total_qa, 4) if total_qa else 0,
        "score_2_count": score_2,
        "score_1_count": score_1,
        "score_0_count": score_0,
        "score_fail_count": score_fail,
        "scored_count": total_scored,
        "score_ge1_count": score_ge1_count,
        "score_zero_rate": round(score_0 / total_scored * 100, 2) if total_scored else 0,
        "avg_score": round(total_score / total_scored, 3) if total_scored else 0,
        "score_ge1_rate": round((score_2 + score_1) / total_scored * 100, 2) if total_scored else 0,
    }

    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_columns = ["company_id", "company_name", "qa_id", "question", "expected_answer",
                   "answer", "retrieval_item_count", "retrieval_hit", "retrieval_hit_rank",
                   "retrieval_mrr", "judge_score", "judge_error", "answer_error"]
    with open(run_dir / "summary.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(evaluated)

    # 中文报告（与终端输出一致，落盘一份方便查看）
    t = summary["totals"]
    lines = []
    lines.append("# 问答评测报告（中文）\n")
    lines.append(f"- 评测来源: {results_path}")
    lines.append(f"- 评测时间: {summary['generated_at']}")
    lines.append(f"- 评测模型: {args.llm_model}")
    lines.append(f"- 检索 Top-K: {args.top_k}")
    lines.append(f"- 题目总数: {t['qa_count']}\n")
    lines.append("## 汇总\n")
    lines.append(f"- **检索 Hit@{args.top_k}**: {t['hit_rate']}% ({t['hit_count']}/{t['qa_count']})")
    lines.append(f"- **检索 MRR**: {t['mrr']:.4f}")
    lines.append(f"- **回答评分分布**: 2分={t['score_2_count']}  1分={t['score_1_count']}  0分={t['score_0_count']}  评分失败={t['score_fail_count']}")
    lines.append(f"- **回答平均分**: {t['avg_score']:.3f}（≥1分比例: {t['score_ge1_rate']}%，0分占比: {t['score_zero_rate']}%）\n")
    lines.append("### 各公司统计\n")
    lines.append("| 公司 | 题目 | Hit@{} | MRR | 2分 | 1分 | 0分 | 平均分 | ≥1分率 | 0分率 |".format(args.top_k))
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in summary["companies"]:
        lines.append(
            f"| {s['company_id']} {s['company_name']} | {s['qa_count']} | "
            f"{s['hit_rate']}% | {s['mrr']:.4f} | {s['score_2_count']} | {s['score_1_count']} | "
            f"{s['score_0_count']} | {s['avg_score']:.3f} | {s['score_ge1_rate']}% | {s['score_zero_rate']}% |"
        )
    lines.append("\n## 逐题明细\n")
    for r in evaluated:
        method = "LLM" if r["retrieval_hit_method"] == "llm" else "字符串"
        score_text = str(r["judge_score"]) if r["judge_score"] is not None else "N/A"
        lines.append(f"### [{r['company_id']}] {r['qa_id']}")
        lines.append(f"- 问题: {r['question']}")
        lines.append(f"- 预期答案: {r['expected_answer']}")
        lines.append(f"- 检索: {'✅ 命中' if r['retrieval_hit'] else '❌ 未命中'} "
                     f"(rank={r['retrieval_hit_rank']}, MRR={r['retrieval_mrr']:.3f}, 方式={method})")
        if r["retrieval_hit_error"]:
            lines.append(f"- 检索判定错误: {r['retrieval_hit_error']}")
        lines.append(f"- 模型回答: {r['answer']}")
        if r["judge_error"]:
            lines.append(f"- 评分错误: {r['judge_error']}")
        lines.append(f"- **评分**: {score_text}/2\n")
    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print("=" * 100)
    print("评测汇总:")
    print(f"  题目总数: {t['qa_count']}")
    print(f"  检索 Hit@{args.top_k}: {t['hit_rate']}%  ({t['hit_count']}/{t['qa_count']})")
    print(f"  检索 MRR: {t['mrr']:.4f}")
    print(f"  回答评分分布: 2分={t['score_2_count']}  1分={t['score_1_count']}  0分={t['score_0_count']}  评分失败={t['score_fail_count']}")
    print(f"  回答平均分: {t['avg_score']:.3f}  (≥1分比例: {t['score_ge1_rate']}%, 0分占比: {t['score_zero_rate']}%)")
    print(f"结果目录: {run_dir}")
    print(f"中文报告: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

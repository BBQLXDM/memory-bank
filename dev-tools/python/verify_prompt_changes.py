#!/usr/bin/env python3
"""
提示词改动单点验证（不生成评测文件，只发少量请求）。

验证两处改动：
1. 裁判「正确拒答」规则（evaluate_qa_results.py 的 JUDGE_PROMPT）：
   从已有 results.json 中挑出标准答案为"无法确定"类的题目，逐条送新裁判打分，
   预期全部得 2 分。
2. 回答「缺失说明」要求（generate_qa_answers.py 的 system_prompt）：
   构造一个空上下文的缺失场景和一个有答案的正常场景，看回答是否符合新要求。

用法：
    python3 dev-tools/python/verify_prompt_changes.py \
        --results benchmark-results/qa-answers/20260826-093326/results.json

可选：
    --skip-judge   跳过裁判验证
    --skip-answer  跳过回答验证
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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

ANSWER_SYSTEM_PROMPT = (
    "你是企业经营问答助手。根据检索到的记忆上下文回答问题。"
    "如果上下文中包含与问题相关的关键事实，请直接给出简洁结论；"
    "涉及数值、日期、编号、比例时优先抽取最直接的事实。"
    "不要解释推理过程，不要复述证据。"
    "如果上下文确实缺少回答问题所需的关键事实，回答'无法确定'，"
    "并用一句话简要说明缺失的是什么信息。"
)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value.strip() else default


def call_llm(base_url: str, model: str, api_key: str, messages: list[dict]) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0}
    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        out = json.loads(response.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"].strip()


def find_unanswerable(results_path: Path) -> list[dict]:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    records = data.get("records", [])
    return [
        r
        for r in records
        if str(r.get("expected_answer", "")).startswith("无法确定")
        and r.get("answer")
        and not r["answer"].startswith("[LLM_ERROR]")
    ]


def verify_judge(args: argparse.Namespace) -> None:
    results_path = Path(args.results)
    targets = find_unanswerable(results_path)
    if args.qa_ids:
        wanted = set(args.qa_ids)
        targets = [r for r in targets if r["qa_id"] in wanted]
    if not targets:
        print("[judge] 未找到可验证的『无法确定』类题目")
        return

    print("=" * 80)
    print(f"[裁判验证] 共 {len(targets)} 条『无法确定』类题目，预期全部判 2 分")
    print("=" * 80)
    passed = 0
    for r in targets:
        prompt = JUDGE_PROMPT.format(ground_truth=r["expected_answer"], retrieved=r["answer"])
        try:
            score = call_llm(env("OPENAI_BASE_URL"), env("OPENAI_CHAT_MODEL"), env("OPENAI_API_KEY"),
                             [{"role": "user", "content": prompt}])
        except Exception as exc:
            score = f"调用失败: {exc}"
        ok = str(score).strip().startswith("2")
        passed += int(ok)
        print(f"  {r['qa_id']} -> 判分: {score}  {'PASS' if ok else 'FAIL'}")
    print(f"[裁判验证] 通过 {passed}/{len(targets)}")


def verify_answer() -> None:
    cases = [
        ("缺失场景（应回答'无法确定'+一句原因）",
         "中路城建工程有限公司的抵押率是多少？",
         "（无相关记忆条目）"),
        ("正常场景（应直接给出结论）",
         "办公楼最新评估日期和评估机构是什么？",
         "[item 1] 项目办公楼的最新评估日期为2025年6月8日，由合肥恒信资产评估有限公司完成，评估有效期至2026年6月7日。"),
    ]
    print("=" * 80)
    print("[回答验证] 新 system_prompt 下两种场景的表现")
    print("=" * 80)
    for name, question, context in cases:
        user_prompt = f"公司：中路城建工程有限公司\n问题：{question}\n\n检索上下文：\n{context}"
        try:
            answer = call_llm(env("OPENAI_BASE_URL"), env("OPENAI_CHAT_MODEL"), env("OPENAI_API_KEY"),
                              [{"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                               {"role": "user", "content": user_prompt}])
        except Exception as exc:
            answer = f"调用失败: {exc}"
        print(f"  [{name}]")
        print(f"    Q: {question}")
        print(f"    A: {answer}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="提示词改动单点验证")
    parser.add_argument("--results", default="")
    parser.add_argument("--qa-ids", nargs="*", default=[])
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--skip-answer", action="store_true")
    args = parser.parse_args()

    if not env("OPENAI_API_KEY"):
        print("[FATAL] OPENAI_API_KEY 未设置（.env 未加载？）")
        return 1

    if not args.skip_judge:
        if not args.results:
            latest = sorted(Path("benchmark-results/qa-answers").glob("*/results.json"))
            if not latest:
                print("[WARN] 找不到任何 results.json，跳过裁判验证")
            else:
                args.results = str(latest[-1])
        if args.results:
            print(f"评测来源: {args.results}")
            verify_judge(args)

    if not args.skip_answer:
        verify_answer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

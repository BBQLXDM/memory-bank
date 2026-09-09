#!/usr/bin/env python3
"""
多模态写入探测程序（独立工具，不影响现有写入代码）。

目的：
1. 验证后端多模态链路：图片信息（base64 image block）能否被抽取进记忆
2. 三组对照：纯文字 / 图文（base64）/ 图文+extracted_text（上限参照）
3. 分阶段计时 add-message / commit / verify，对比各组耗时
4. 用数据集自带 extracted_text 金标准自动计算「图片信息入忆率」

数据集：data/multimodal/output_v1/（20 公司 × 5 session，事实只在图片里）

用法示例（3 家公司小样本，全部组别）：
    conda activate memind-ui && cd /home/zzx/py/Memind-Local-Dev
    python3 dev-tools/python/mm_write_probe.py \
      --server http://127.0.0.1:8366 \
      --companies C001 C002 C003

    # 只跑某一组
    python3 dev-tools/python/mm_write_probe.py --companies C001 --groups text,image
    # 干跑：不发请求，只展示将要发送的消息结构
    python3 dev-tools/python/mm_write_probe.py --companies C001 --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from memind_shared import load_env

DEFAULT_SERVER = "http://127.0.0.1:8366"
DEFAULT_DATASET = "data/multimodal/output_v1"
DEFAULT_OUT_DIR = "benchmark-results/mm-write-probe"
DEFAULT_ID_PREFIX = "zh-mm-v1"

GROUPS = ("text", "image", "image_text")

GROUP_LABELS = {
    "text": "A 纯文字（对照）",
    "image": "B 图文 base64（主方案）",
    "image_text": "C 图文+extracted_text（上限参照）",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多模态写入探测（独立工具）")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--id-prefix", default=DEFAULT_ID_PREFIX)
    parser.add_argument("--companies", nargs="*", default=["C001"])
    parser.add_argument("--groups", nargs="*", default=list(GROUPS), choices=GROUPS)
    parser.add_argument("--session-limit", type=int, default=0, help="每公司最多写多少 session（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="不发请求，打印消息结构")
    parser.add_argument("--connect-timeout", type=int, default=5)
    parser.add_argument("--read-timeout", type=int, default=900)
    return parser.parse_args()


def log(msg: str) -> None:
    print(msg, flush=True)


def load_company_dataset(dataset_dir: Path, company_id: str) -> dict | None:
    path = dataset_dir / f"{company_id}_multimodal.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def image_path_for(dataset_dir: Path, *refs: str | None) -> Path | None:
    """图片引用兼容：turn.image_ref 可能是 image_id，需先经 image_id→path 映射，再映射到本地 images 目录。"""
    images_dir = dataset_dir / "images"
    for ref in refs:
        if not ref:
            continue
        p = images_dir / Path(ref).name
        if p.exists():
            return p
    return None


def load_image_b64(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return base64.b64encode(raw).decode("ascii"), len(raw)


def build_message(turn_text: str, image_b64: str | None, extra_text: str | None, ts: str | None) -> dict:
    """构造 add-message 的 message；按组别决定是否含 image block / 附加文字。"""
    text = turn_text
    if extra_text:
        text = f"{turn_text}\n（图片文字内容：{extra_text}）"
    content: list[dict] = [{"type": "text", "text": text}]
    if image_b64:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
            }
        )
    message = {"role": "USER", "content": content}
    if ts:
        message["timestamp"] = ts
    return message


def key_facts_from_extracted_text(extracted_text: str) -> list[str]:
    """从 extracted_text 粗提取「数字+单位/日期」类关键事实，用于入忆率判定。"""
    import re

    facts = []
    for m in re.finditer(r"\d+(?:\.\d+)?(?:%|万元|亿元|万|亿元|个|家|项|笔|天|年)", extracted_text):
        token = m.group(0)
        if token not in facts and len(token) >= 2:
            facts.append(token)
    return facts[:8]


def check_facts_in_items(facts: list[str], items: list[dict]) -> tuple[int, list[str]]:
    """统计关键事实有多少出现在记忆条目文本中。"""
    texts = [json.dumps(it, ensure_ascii=False) for it in items]
    joined = "\n".join(texts)
    hit, missed = 0, []
    for f in facts:
        if f in joined:
            hit += 1
        else:
            missed.append(f)
    return hit, missed


class ProbeBackend:
    def __init__(self, server: str, connect_timeout: int, read_timeout: int):
        self.base = server.rstrip("/")
        self.ct = connect_timeout
        self.rt = read_timeout

    def _post(self, path: str, payload: dict, timeout: int | None = None):
        return requests.post(
            f"{self.base}{path}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=(self.ct, timeout or self.rt),
        )

    def health(self) -> str:
        data = requests.get(f"{self.base}/open/v1/health", timeout=(self.ct, 30)).json()
        inner = data.get("data", data)
        return str(inner.get("status", inner))

    def add_message(self, user_id: str, agent_id: str, message: dict, source_client: str) -> None:
        resp = self._post(
            "/open/v1/memory/sync/add-message",
            {"userId": user_id, "agentId": agent_id, "message": message, "sourceClient": source_client},
        )
        resp.raise_for_status()

    def commit(self, user_id: str, agent_id: str, source_client: str) -> None:
        resp = self._post(
            "/open/v1/memory/sync/commit",
            {"userId": user_id, "agentId": agent_id, "sourceClient": source_client},
            timeout=900,
        )
        resp.raise_for_status()

    def query_items(self, user_id: str, agent_id: str, source_client: str, limit: int = 50) -> list[dict]:
        resp = self._post(
            "/open/v1/memory/items/query",
            {"userId": user_id, "agentId": agent_id, "sourceClients": [source_client], "limit": limit},
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("items", [])


def stage_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": 0.0, "median": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "max": round(max(values), 2),
    }


def main() -> int:
    args = parse_args()
    load_env()

    dataset_dir = Path(args.dataset)
    if not dataset_dir.exists():
        log(f"[FATAL] 数据集目录不存在: {dataset_dir.resolve()}")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.out_dir) / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    log("=" * 100)
    log(f"数据集: {dataset_dir.resolve()}")
    log(f"公司: {', '.join(args.companies)}")
    log(f"组别: {', '.join(GROUP_LABELS[g] for g in args.groups)}")
    log(f"记忆前缀: {args.id_prefix}（独立隔离，不污染正式数据）")
    log(f"输出: {run_dir}")
    if args.dry_run:
        log("模式: dry-run（不发请求）")
    log("=" * 100)

    rows: list[dict] = []

    if args.dry_run:
        for company_id in args.companies:
            data = load_company_dataset(dataset_dir, company_id)
            if not data:
                log(f"[WARN] 缺少 {company_id}_multimodal.json")
                continue
            images = data.get("context", {}).get("images", [])
            sessions = data.get("context", {}).get("sessions", [])
            id2path = {im.get("image_id"): im.get("path") for im in images}
            if args.session_limit > 0:
                sessions = sessions[: args.session_limit]
            for s in sessions:
                img_ref = (s.get("turns") or [{}])[0].get("image_ref")
                if img_ref:
                    img_ref = id2path.get(img_ref, img_ref)
                p = image_path_for(dataset_dir, img_ref)
                turn = (s.get("turns") or [{}])[0]
                log(f"[dry] {company_id}-{s.get('session_id')}: 图片={p.name if p else '缺失'} 文本={turn.get('content', '')[:40]}")
            log(f"[dry] {company_id}: images={len(images)} sessions={len(sessions)} qa={len(data.get('qa_items', []))}")
        log("\ndry-run 完成（未发任何请求）")
        return 0

    backend = ProbeBackend(args.server, args.connect_timeout, args.read_timeout)
    try:
        status = backend.health()
    except Exception as ex:
        log(f"[FATAL] 后端健康检查失败: {ex}")
        return 1
    log(f"后端健康: {status}\n")

    for company_id in args.companies:
        data = load_company_dataset(dataset_dir, company_id)
        if not data:
            log(f"[WARN] 缺少 {company_id}_multimodal.json，跳过")
            continue
        ctx = data.get("context", {})
        images = ctx.get("images", [])
        sessions = ctx.get("sessions", [])
        if args.session_limit > 0:
            sessions = sessions[: args.session_limit]
        images_by_session = {im.get("session_id"): im for im in images}
        id2path = {im.get("image_id"): im.get("path") for im in images}

        log("=" * 100)
        log(f"公司 {company_id}: sessions={len(sessions)} images={len(images)}")

        for group in args.groups:
            log(f"\n----- 组别: {GROUP_LABELS[group]} -----")
            group_rows = []
            for session in sessions:
                sid = session.get("session_id")
                turn = (session.get("turns") or [{}])[0]
                turn_text = str(turn.get("content", ""))
                ts = turn.get("timestamp")
                image_ref = turn.get("image_ref")
                im = images_by_session.get(sid) or {}
                # image_ref 是 image_id，需映射到 images[].path
                if image_ref:
                    image_ref = id2path.get(image_ref, image_ref)
                if not image_ref:
                    # 兜底：session 顺序对应 images 顺序
                    idx = [s.get("session_id") for s in sessions].index(sid)
                    if idx < len(images):
                        image_ref = images[idx].get("path")
                img_path = image_path_for(dataset_dir, image_ref, im.get("path"))
                extracted = str(im.get("extracted_text", "") or "")

                image_b64 = None
                image_bytes = 0
                if group in ("image", "image_text"):
                    if not img_path:
                        log(f"  [WARN] {sid}: 图片文件缺失，本 session 跳过（{im.get('path')}）")
                        continue
                    image_b64, image_bytes = load_image_b64(img_path)

                extra_text = extracted if group == "image_text" else None
                message = build_message(turn_text, image_b64, extra_text, ts)

                user_id = f"{args.id_prefix}-{company_id}"
                agent_id = f"{args.id_prefix}-{company_id}-agent"
                source_client = f"{args.id_prefix}-{company_id}-{sid}"

                t_add0 = time.time()
                try:
                    backend.add_message(user_id, agent_id, message, source_client)
                    add_s = time.time() - t_add0
                except Exception as ex:
                    add_s = time.time() - t_add0
                    log(f"  [{sid}] add 失败({add_s:.1f}s): {ex}")
                    group_rows.append(
                        {
                            "company_id": company_id,
                            "session_id": sid,
                            "group": group,
                            "image_bytes": image_bytes,
                            "add_s": round(add_s, 2),
                            "commit_s": None,
                            "verify_s": None,
                            "error": f"add: {ex}",
                        }
                    )
                    rows.append(group_rows[-1])
                    continue

                t_commit0 = time.time()
                try:
                    backend.commit(user_id, agent_id, source_client)
                    commit_s = time.time() - t_commit0
                except Exception as ex:
                    commit_s = time.time() - t_commit0
                    log(f"  [{sid}] commit 失败({commit_s:.1f}s): {ex}")
                    group_rows.append(
                        {
                            "company_id": company_id,
                            "session_id": sid,
                            "group": group,
                            "image_bytes": image_bytes,
                            "add_s": round(add_s, 2),
                            "commit_s": round(commit_s, 2),
                            "verify_s": None,
                            "error": f"commit: {ex}",
                        }
                    )
                    rows.append(group_rows[-1])
                    continue

                t_verify0 = time.time()
                try:
                    items = backend.query_items(user_id, agent_id, source_client, limit=50)
                    verify_s = time.time() - t_verify0
                except Exception as ex:
                    items = []
                    verify_s = time.time() - t_verify0
                    log(f"  [{sid}] query 失败: {ex}")

                facts = key_facts_from_extracted_text(extracted)
                hit, missed = check_facts_in_items(facts, items)
                hit_rate = round(hit / len(facts) * 100, 1) if facts else None

                log(
                    f"  [{sid}] add={add_s:.1f}s commit={commit_s:.1f}s verify={verify_s:.2f}s "
                    f"img={image_bytes // 1024}KB items={len(items)} 事实命中 {hit}/{len(facts)} ({hit_rate if hit_rate is not None else '-'}%)"
                )
                if missed and group != "text":
                    log(f"          未命中: {', '.join(missed[:6])}")

                group_rows.append(
                    {
                        "company_id": company_id,
                        "session_id": sid,
                        "group": group,
                        "image_bytes": image_bytes,
                        "add_s": round(add_s, 2),
                        "commit_s": round(commit_s, 2),
                        "verify_s": round(verify_s, 2),
                        "items": len(items),
                        "facts_total": len(facts),
                        "facts_hit": hit,
                        "facts_missed": missed,
                        "fact_hit_rate": hit_rate,
                        "error": None,
                    }
                )

            # 组内汇总
            ok_rows = [r for r in group_rows if not r.get("error")]
            if ok_rows:
                add_vals = [r["add_s"] for r in ok_rows]
                commit_vals = [r["commit_s"] for r in ok_rows if r.get("commit_s") is not None]
                hit_rates = [r["fact_hit_rate"] for r in ok_rows if r.get("fact_hit_rate") is not None]
                log(
                    f"  >>> {GROUP_LABELS[group]} 汇总: add 均值={statistics.mean(add_vals):.2f}s "
                    f"commit 均值={statistics.mean(commit_vals):.2f}s "
                    f"入忆率均值={statistics.mean(hit_rates):.1f}% " if hit_rates else
                    f"  >>> {GROUP_LABELS[group]} 汇总: add 均值={statistics.mean(add_vals):.2f}s commit 均值={statistics.mean(commit_vals):.2f}s"
                )
            rows.extend(group_rows)

    # 汇总输出
    result = {
        "server": args.server,
        "dataset": str(dataset_dir),
        "id_prefix": args.id_prefix,
        "groups": list(args.groups),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "by_group": {
            g: {
                "add": stage_stats([r["add_s"] for r in rows if r.get("group") == g and not r.get("error")]),
                "commit": stage_stats([r["commit_s"] for r in rows if r.get("group") == g and not r.get("error") and r.get("commit_s") is not None]),
                "fact_hit_rates": [r["fact_hit_rate"] for r in rows if r.get("group") == g and r.get("fact_hit_rate") is not None],
            }
            for g in args.groups
        },
    }
    out_path = run_dir / "probe.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n" + "=" * 100)
    log("各组别汇总（秒）")
    for g in args.groups:
        s = result["by_group"][g]
        rates = s["fact_hit_rates"]
        rate_mean = round(statistics.mean(rates), 1) if rates else None
        log(
            f"  {GROUP_LABELS[g]}: add均值={s['add']['mean']}s commit均值={s['commit']['mean']}s"
            + (f" 入忆率均值={rate_mean}%" if rate_mean is not None else "")
        )
    log(f"\n结果已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

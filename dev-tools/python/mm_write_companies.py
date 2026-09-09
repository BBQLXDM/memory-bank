#!/usr/bin/env python3
"""
多模态记忆写入（标准图文，带终端进度面板）。

与 parallel_write_companies.py 的区别：
- 数据集为多模态数据集（data/multimodal/output_v1/），每 session 一条 user 消息 + 一张 PNG
- 写入格式：message.content = [text block, image block(base64)]，与探测 B 组一致
- seen 记录独立目录 memory-generation-mm-seen，与文本写入互不干扰
- 写入后自动比对 extracted_text 关键事实入忆率，逐公司汇总

用法示例：
    python3 dev-tools/python/mm_write_companies.py \
        --server http://127.0.0.1:8366 \
        --companies C001 C002 C003 \
        --id-prefix zh-mm-v1 --source-client zh-mm-v1 \
        --parallel 2
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
    from rich.table import Table
    try:
        from rich.group import Group
    except ImportError:
        from rich.console import Group

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from generate_benchmark_memories import Backend, check_llm_api, parse_instant, project_id_for
from memind_shared import load_env

if RICH_AVAILABLE:
    console = Console()

DEFAULT_SERVER = "http://127.0.0.1:8366"
DEFAULT_DATASET = "data/multimodal/output_v1"
DEFAULT_OUT_DIR = "benchmark-results/memory-generation-mm"
DEFAULT_SEEN_DIR = "benchmark-results/memory-generation-mm-seen"
DEFAULT_ID_PREFIX = "zh-mm-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多模态记忆写入（图文标准格式）")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--seen-dir", default=DEFAULT_SEEN_DIR)
    parser.add_argument("--id-prefix", default=DEFAULT_ID_PREFIX)
    parser.add_argument("--source-client", default=DEFAULT_ID_PREFIX)
    parser.add_argument("--companies", nargs="*", default=[])
    parser.add_argument("--parallel", type=int, default=1, help="公司级并行数（默认 1 串行）")
    parser.add_argument("--connect-timeout", type=int, default=5)
    parser.add_argument("--read-timeout", type=int, default=900)
    parser.add_argument("--verify-timeout", type=int, default=120)
    parser.add_argument("--verify-poll-interval", type=int, default=5)
    parser.add_argument("--no-fact-check", action="store_true", help="跳过事实入忆率校验")
    return parser.parse_args()


def log(msg: str) -> None:
    print(msg, flush=True)


def fmt_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m:02d}分"
    if m:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


class ProgressBoard:
    """线程安全进度面板（与 parallel_write_companies.py 同款交互）。"""

    def __init__(self, enabled: bool) -> None:
        self.lock = threading.Lock()
        self.enabled = enabled
        self.start_time = time.time()
        self.order: list[str] = []
        self.companies: dict[str, dict] = {}
        self.events: list[str] = []
        self.print_lock = threading.Lock()
        self._live: Live | None = None

    def register(self, company_id: str, name: str, total: int) -> None:
        with self.lock:
            self.companies[company_id] = {
                "name": name,
                "total": total,
                "done": 0,
                "success": 0,
                "failed": 0,
                "current": "",
                "status": "排队中",
                "error": "",
            }
            self.order.append(company_id)

    def update(self, company_id: str, done: int, success: int, failed: int, current: str) -> None:
        with self.lock:
            c = self.companies.get(company_id)
            if c:
                c.update({"done": done, "success": success, "failed": failed, "current": current})

    def mark_running(self, company_id: str) -> None:
        with self.lock:
            if company_id in self.companies:
                self.companies[company_id]["status"] = "写入中"

    def mark_finished(self, company_id: str, error: str = "") -> None:
        with self.lock:
            c = self.companies.get(company_id)
            if c:
                c["status"] = "完成" if not error else "异常"
                c["error"] = error

    def add_event(self, message: str) -> None:
        with self.lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.events.append(f"[{ts}] {message}")
            self.events = self.events[-15:]
        if not self.enabled:
            self._print(message)

    def _print(self, message: str) -> None:
        with self.print_lock:
            sys.stdout.write(f"{message}\n")
            sys.stdout.flush()

    def build_layout(self):
        if not RICH_AVAILABLE:
            return None
        total_done = sum(c["done"] for c in self.companies.values())
        total = sum(c["total"] for c in self.companies.values())
        success = sum(c["success"] for c in self.companies.values())
        failed = sum(c["failed"] for c in self.companies.values())
        elapsed = time.time() - self.start_time

        header = Table.grid(padding=(0, 2))
        header.add_column(justify="left")
        header.add_row(
            f"多模态记忆写入 | 已用 {fmt_duration(elapsed)} | 进度 {total_done}/{total} | 成功 {success} 失败 {failed}"
        )

        table = Table(title=None, expand=True)
        table.add_column("公司", width=8)
        table.add_column("进度", width=12)
        table.add_column("成功/失败", width=10)
        table.add_column("状态", width=8)
        table.add_column("当前", overflow="fold")
        for cid in self.order:
            c = self.companies[cid]
            table.add_row(
                cid,
                f"{c['done']}/{c['total']}",
                f"{c['success']}/{c['failed']}",
                c["status"],
                c["current"],
            )

        parts = [header, table]
        if self.events:
            parts.append(Panel("\n".join(self.events[-6:]), title="事件"))
        try:
            return Group(*parts)
        except Exception:
            return header

    def start_live(self) -> None:
        if not self.enabled or not RICH_AVAILABLE:
            return
        self._live = Live(self.build_layout(), console=console, refresh_per_second=4, screen=False)
        self._live.start()

    def refresh(self) -> None:
        if self._live:
            self._live.update(self.build_layout())

    def stop_live(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None


def load_mm_dataset(dataset_dir: Path, company_id: str) -> dict | None:
    path = dataset_dir / f"{company_id}_multimodal.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def image_path_for(dataset_dir: Path, refs: list[str | None]) -> Path | None:
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


def key_facts_from_extracted_text(extracted_text: str) -> list[str]:
    facts: list[str] = []
    for m in re.finditer(r"\d+(?:\.\d+)?(?:%|万元|亿元|万|个|家|项|笔|天|人)", extracted_text):
        token = m.group(0)
        if token not in facts and len(token) >= 2:
            facts.append(token)
    return facts[:8]


def fact_hit_rate(facts: list[str], items: list[dict]) -> tuple[int, int, list[str]]:
    if not facts:
        return 0, 0, []
    joined = "\n".join(json.dumps(it, ensure_ascii=False) for it in items)
    hit = sum(1 for f in facts if f in joined)
    missed = [f for f in facts if f not in joined]
    return hit, len(facts), missed


def seen_paths(seen_dir: Path, company_id: str) -> tuple[Path, Path]:
    company_seen_dir = seen_dir / company_id
    company_seen_dir.mkdir(parents=True, exist_ok=True)
    return company_seen_dir / "result.log", company_seen_dir


def write_seen(seen_result: Path, record: dict) -> None:
    with seen_result.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_seen_success(seen_result: Path) -> set[str]:
    done: set[str] = set()
    if not seen_result.exists():
        return done
    with seen_result.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("status") == "success":
                done.add(str(rec.get("session_id")))
    return done


def write_company(
    company_id: str,
    data: dict,
    args: argparse.Namespace,
    dataset_dir: Path,
    board: ProgressBoard,
    stop_event: threading.Event,
) -> None:
    company_name = data.get("company_name", company_id)
    ctx = data.get("context", {})
    images = ctx.get("images", [])
    sessions = ctx.get("sessions", [])
    id2path = {im.get("image_id"): im.get("path") for im in images}

    seen_result, _ = seen_paths(Path(args.seen_dir), company_id)
    done_before = load_seen_success(seen_result)

    board.register(company_id, company_name, len(sessions))
    board.mark_running(company_id)
    board.refresh()

    user_id = f"{args.id_prefix}-{company_id}"
    agent_id = f"{args.id_prefix}-{company_id}-agent"
    errors: list[str] = []
    fact_stats: list[tuple[int, int]] = []

    try:
        backend = Backend(args.server, args.connect_timeout, args.read_timeout)
        backend.health()
    except Exception as ex:
        board.mark_finished(company_id, error=str(ex))
        board.add_event(f"{company_id}: 后端连接失败 {ex}")
        return

    try:
        if check_llm_api()[0] is False and not stop_event.is_set():
            # LLM 额度不可用时直接停止（与文本写入工具行为一致）
            board.mark_finished(company_id, error="LLM API 不可用")
            board.add_event(f"{company_id}: LLM API 额度不可用，停止写入")
            return

        for idx, session in enumerate(sessions, 1):
            if stop_event.is_set():
                break
            sid = str(session.get("session_id"))
            board.update(company_id, idx - 1, idx - 1 - len(errors), len(errors), sid)
            board.refresh()

            if sid in done_before:
                board.add_event(f"{company_id}-{sid}: 已成功，跳过")
                continue

            turn = (session.get("turns") or [{}])[0]
            turn_text = str(turn.get("content", ""))
            ts = parse_instant(turn.get("timestamp"))
            image_ref = turn.get("image_ref")
            if image_ref:
                image_ref = id2path.get(image_ref, image_ref)
            img_path = image_path_for(dataset_dir, [image_ref])

            if not img_path:
                msg = f"{company_id}-{sid}: 图片缺失"
                errors.append(msg)
                board.add_event(msg)
                write_seen(seen_result, {"session_id": sid, "status": "failed", "error": "image missing", "ts": datetime.now(timezone.utc).isoformat()})
                continue

            image_b64, image_bytes = load_image_b64(img_path)
            source_client = f"{args.source_client}-{company_id}-{sid}"
            message = {
                "role": "USER",
                "content": [
                    {"type": "text", "text": turn_text},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
                    },
                ],
                "timestamp": ts,
            }

            t0 = time.time()
            try:
                backend.add_message(user_id, agent_id, message, source_client)
                add_s = time.time() - t0
            except Exception as ex:
                msg = f"{company_id}-{sid}: add 失败 {ex}"
                errors.append(msg)
                board.add_event(msg)
                write_seen(seen_result, {"session_id": sid, "status": "failed", "error": str(ex), "ts": datetime.now(timezone.utc).isoformat()})
                if "403" in str(ex) or "429" in str(ex):
                    stop_event.set()
                continue

            t1 = time.time()
            try:
                backend.commit(user_id, agent_id, source_client)
                commit_s = time.time() - t1
            except Exception as ex:
                msg = f"{company_id}-{sid}: commit 失败 {ex}"
                errors.append(msg)
                board.add_event(msg)
                write_seen(seen_result, {"session_id": sid, "status": "failed", "error": str(ex), "ts": datetime.now(timezone.utc).isoformat()})
                if "403" in str(ex) or "429" in str(ex):
                    stop_event.set()
                continue

            items = []
            try:
                items = backend.query_items(user_id, agent_id, source_client, limit=50)
            except Exception as ex:
                board.add_event(f"{company_id}-{sid}: query 失败 {ex}")

            im_meta = next((im for im in images if im.get("image_id") == turn.get("image_ref")), None)
            extracted = str((im_meta or {}).get("extracted_text", "") or "")
            hit, total_facts, missed = fact_hit_rate(key_facts_from_extracted_text(extracted), items)
            if total_facts:
                fact_stats.append((hit, total_facts))

            record = {
                "session_id": sid,
                "status": "success",
                "image": img_path.name,
                "image_kb": round(image_bytes / 1024, 1),
                "add_s": round(add_s, 2),
                "commit_s": round(commit_s, 2),
                "items": len(items),
                "fact_hit": hit,
                "fact_total": total_facts,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            write_seen(seen_result, record)
            board.add_event(
                f"{company_id}-{sid}: 成功 commit={commit_s:.1f}s items={len(items)}"
                + (f" 事实命中 {hit}/{total_facts}" if total_facts else "")
            )

        board.update(company_id, len(sessions), len(sessions) - len(errors), len(errors), "")
    except Exception as ex:  # pragma: no cover
        errors.append(f"{company_id}: 未预期异常 {ex}")
        board.add_event(f"{company_id}: 未预期异常 {ex}")
    finally:
        board.mark_finished(company_id, error="; ".join(errors[-2:]))
        board.update(company_id, len(sessions), len(sessions) - len(errors), len(errors), "")
        board.refresh()

    # 公司级事实入忆率
    if fact_stats:
        hit_sum = sum(h for h, _ in fact_stats)
        total_sum = sum(t for _, t in fact_stats)
        rate = round(hit_sum / total_sum * 100, 1) if total_sum else None
        board.add_event(f"{company_id}: 图片事实入忆率 {rate}% ({hit_sum}/{total_sum})")


def main() -> int:
    args = parse_args()
    load_env()

    dataset_dir = Path(args.dataset)
    if not dataset_dir.exists():
        log(f"[FATAL] 数据集不存在: {dataset_dir.resolve()}")
        return 1

    company_ids = [c.upper() for c in args.companies]
    if not company_ids:
        # 默认全部公司（按数据集文件扫描）
        company_ids = sorted(p.name.split("_")[0] for p in dataset_dir.glob("C*_multimodal.json"))
    if not company_ids:
        log("[FATAL] 未找到任何公司数据")
        return 1

    ok, msg = check_llm_api()
    log(f"LLM API: {'可用' if ok else '不可用: ' + msg}")
    if not ok:
        log("[FATAL] LLM API 不可用，停止写入")
        return 1

    board = ProgressBoard(enabled=sys.stdout.isatty())
    stop_event = threading.Event()

    log("=" * 100)
    log(f"数据集: {dataset_dir.resolve()}")
    log(f"公司({len(company_ids)}): {' '.join(company_ids)}")
    log(f"并行度: {args.parallel}")
    log(f"记忆前缀: {args.id_prefix}")
    log(f"输出: {args.out_dir}")
    log(f"seen: {args.seen_dir}")
    log("=" * 100)

    board.start_live()

    started = time.time()
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
            futures = {}
            for cid in company_ids:
                data = load_mm_dataset(dataset_dir, cid)
                if not data:
                    board.register(cid, cid, 0)
                    board.mark_finished(cid, error="数据文件缺失")
                    continue
                futures[pool.submit(write_company, cid, data, args, dataset_dir, board, stop_event)] = cid
            for fut in as_completed(futures):
                fut.result()
    except KeyboardInterrupt:
        stop_event.set()
        board.add_event("收到中断信号，等待线程退出…")
    finally:
        board.stop_live()

    elapsed = time.time() - started
    summary = {
        "dataset": str(dataset_dir.resolve()),
        "companies": company_ids,
        "elapsed_s": round(elapsed, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "company_status": {
            cid: {
                "status": board.companies.get(cid, {}).get("status"),
                "done": board.companies.get(cid, {}).get("done"),
                "success": board.companies.get(cid, {}).get("success"),
                "failed": board.companies.get(cid, {}).get("failed"),
            }
            for cid in company_ids
        },
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    log("=" * 100)
    log(f"写入结束，总耗时 {fmt_duration(elapsed)}")
    for cid in company_ids:
        c = board.companies.get(cid, {})
        log(
            f"  {cid}: {c.get('status', '?')}  成功 {c.get('success', 0)}/{c.get('total', 0)}  失败 {c.get('failed', 0)}"
            + (f"  ({c.get('error')})" if c.get("error") else "")
        )
    log(f"汇总: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

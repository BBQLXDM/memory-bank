#!/usr/bin/env python3
"""
公司级并行记忆写入（带终端进度面板）。

与 generate_benchmark_memories.py 的区别：
- 公司之间并行（--parallel N），公司内部仍串行，保持同一公司的会话顺序
- 终端实时进度：TTY 下显示汇总面板（每家公司已完成数/总数/成功/失败/当前 session），
  非 TTY（输出重定向到日志）时退化为每家公司完成一行的普通日志
- LLM API 额度耗尽时通过 stop_event 通知所有公司线程提前退出

复用 generate_benchmark_memories.py 的写入与验证逻辑，保证行为一致：
- 跳过逻辑：seen 记录 success + 服务器已有 item 双重判断
- 每个 session 写入后立即 verify，并写 seen 记录

用法示例：
    python3 dev-tools/python/parallel_write_companies.py \
        --server http://127.0.0.1:8366 \
        --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
        --companies C006 C007 C008 C009 C010 \
        --id-prefix zh-memory-v1 --source-client zh-memory-v1 \
        --parallel 2
"""

from __future__ import annotations

import argparse
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
        from rich.group import Group  # rich >= 10.x
    except ImportError:
        from rich.console import Group  # 旧版 rich

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from generate_benchmark_memories import (
    Backend,
    check_llm_api,
    has_successful_response,
    load_company,
    project_id_for,
    write_csv,
    write_json,
)
from memind_shared import company_entries, load_env

DEFAULT_SERVER = "http://127.0.0.1:8366"
DEFAULT_OUT_DIR = "benchmark-results/memory-generation-parallel"
DEFAULT_SEEN_DIR = "benchmark-results/memory-generation-seen"
DEFAULT_BENCHMARK_DIR = "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"


if RICH_AVAILABLE:
    console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="并行记忆写入（公司级，带进度面板）")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--benchmark-dir", default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--seen-dir", default=DEFAULT_SEEN_DIR)
    parser.add_argument("--source-client", default="zh-memory-v1")
    parser.add_argument("--id-prefix", default="zh-memory-v1")
    parser.add_argument("--parallel", type=int, default=2, help="并行的公司数（默认 2）")
    parser.add_argument("--session-limit", type=int, default=0)
    parser.add_argument("--refresh-interval", type=float, default=2.0, help="进度面板刷新间隔(秒)")
    parser.add_argument("--no-panel", action="store_true", help="强制关闭终端面板（仅日志行）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--companies", nargs="*", default=[])
    parser.add_argument("--sessions", nargs="*", default=[])
    return parser.parse_args()


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m:02d}分"
    if m:
        return f"{m}分{s:02d}秒"
    return f"{s}秒"


class ProgressBoard:
    """线程安全的进度状态 + rich Live 面板渲染。"""

    def __init__(self, enabled: bool, refresh_interval: float) -> None:
        self.lock = threading.Lock()
        self.enabled = enabled
        self.refresh_interval = refresh_interval
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
                "finished": False,
                "error": "",
            }
            self.order.append(company_id)

    def mark_running(self, company_id: str) -> None:
        with self.lock:
            if company_id in self.companies:
                self.companies[company_id]["status"] = "写入中"

    def update(self, company_id: str, done: int, success: int, failed: int, current: str) -> None:
        with self.lock:
            c = self.companies.get(company_id)
            if c:
                c["done"] = done
                c["success"] = success
                c["failed"] = failed
                c["current"] = current

    def mark_finished(self, company_id: str, error: str = "") -> None:
        with self.lock:
            c = self.companies.get(company_id)
            if c:
                c["finished"] = True
                c["status"] = "完成" if not error else "异常"
                c["error"] = error

    def add_event(self, message: str) -> None:
        with self.lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.events.append(f"[{ts}] {message}")
            if len(self.events) > 20:
                self.events = self.events[-20:]
        if not self.enabled:
            self._print(message)

    def _print(self, message: str) -> None:
        with self.print_lock:
            sys.stdout.write(f"{message}\n")
            sys.stdout.flush()

    def start_live(self) -> None:
        if not self.enabled or not RICH_AVAILABLE:
            return
        self._live = Live(
            self.build_layout(),
            console=console,
            refresh_per_second=4,
            screen=False,
        )
        self._live.start()

    def refresh(self) -> None:
        if self._live:
            self._live.update(self.build_layout())

    def stop_live(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def build_layout(self):
        """构造 rich 面板（线程安全，返回新对象）。"""
        with self.lock:
            finished = sum(1 for c in self.companies.values() if c["finished"])
            grand = sum(c["total"] for c in self.companies.values())
            done_total = sum(c["done"] for c in self.companies.values())
            success_total = sum(c["success"] for c in self.companies.values())
            failed_total = sum(c["failed"] for c in self.companies.values())
            events_snapshot = list(self.events[-6:])
            elapsed = fmt_duration(time.time() - self.start_time)

        # 总进度条
        overall = Progress(
            SpinnerColumn(),
            TextColumn("[bold]总体[/bold]"),
            BarColumn(bar_width=24),
            TextColumn("{task.percentage:>3.0f}%  {task.completed}/{task.total}"),
        )
        overall.add_task("", total=grand, completed=done_total)

        # 头部汇总
        header = (
            f"完成公司 {finished}/{len(self.order)}    "
            f"已用 {elapsed}    "
            f"总成功 [green]{success_total}[/green]    "
            f"总失败 [red]{failed_total}[/red]"
        )

        # 每家公司一行
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("公司")
        table.add_column("进度", justify="left")
        table.add_column("完成", justify="right")
        table.add_column("成功", justify="right")
        table.add_column("失败", justify="right")
        table.add_column("状态")
        table.add_column("当前")
        for cid in self.order:
            c = self.companies[cid]
            pct = (c["done"] / c["total"] * 100) if c["total"] else 100.0
            filled = int(pct / 5)
            bar = "█" * filled + "░" * (20 - filled)
            status_color = "green" if c["status"] == "完成" else ("yellow" if c["status"] == "写入中" else "dim")
            table.add_row(
                f"[bold]{cid}[/bold]",
                bar,
                f"{c['done']}/{c['total']}",
                f"[green]{c['success']}[/green]",
                f"[red]{c['failed']}[/red]" if c["failed"] else "0",
                f"[{status_color}]{c['status']}[/{status_color}]",
                c["current"],
            )

        # 底部事件日志
        log_block = "\n".join(events_snapshot) if events_snapshot else "[dim]暂无事件[/dim]"

        body = Group(header, overall, table, Panel(log_block, title="事件日志", border_style="dim"))
        return Panel(
            body,
            title="并行记忆写入 · 方案A (rich)",
            border_style="cyan",
        )


def run_company_worker(
    backend: Backend,
    company: dict,
    sessions: list[dict],
    args: argparse.Namespace,
    out_dir: Path,
    seen_dir: Path,
    board: ProgressBoard,
    stop_event: threading.Event,
) -> tuple[dict, list[dict]]:
    """并行写入一家公司，返回 (company_row, session_rows)。"""
    company_id = company["company_id"]
    company_name = company["company_name"]
    board.mark_running(company_id)

    company_dir = out_dir / company_id
    company_dir.mkdir(parents=True, exist_ok=True)
    seen_company_dir = seen_dir / company_id
    seen_company_dir.mkdir(parents=True, exist_ok=True)

    success_sessions = 0
    failed_sessions = 0
    session_rows: list[dict] = []

    for session in sessions:
        if stop_event.is_set():
            board.add_event(f"{company_id} 提前停止（LLM API 失效或外部要求停止）")
            break
        session_id = session["session_id"]
        if args.sessions and session_id not in [s.upper() for s in args.sessions]:
            continue
        request_path = company_dir / f"{session_id}.request.json"
        response_path = company_dir / f"{session_id}.response.json"
        seen_response_path = seen_company_dir / f"{session_id}.response.json"
        session_source_client = (
            f"{args.source_client}-{company_id}-{session_id}"
        )

        if not args.dry_run:
            if has_successful_response(
                seen_response_path,
                company["project_id"],
                session_id,
                session_source_client,
                company["user_id"],
                company["agent_id"],
            ):
                session_rows.append(
                    {
                        "company_id": company_id,
                        "company_name": company_name,
                        "session_id": session_id,
                        "status_code": 208,
                        "success": True,
                        "error": "skipped_existing_success",
                        "request_path": str(request_path),
                        "response_path": str(seen_response_path),
                    }
                )
                success_sessions += 1
                board.update(
                    company_id,
                    success_sessions + failed_sessions,
                    success_sessions,
                    failed_sessions,
                    f"{session_id} (已存在)",
                )
                continue

            try:
                items = backend.query_items(
                    company["user_id"], company["agent_id"], session_source_client
                )
                if items:
                    session_rows.append(
                        {
                            "company_id": company_id,
                            "company_name": company_name,
                            "session_id": session_id,
                            "status_code": 208,
                            "success": True,
                            "error": "skipped_already_present",
                            "request_path": str(request_path),
                            "response_path": str(response_path),
                        }
                    )
                    success_sessions += 1
                    board.update(
                        company_id,
                        success_sessions + failed_sessions,
                        success_sessions,
                        failed_sessions,
                        f"{session_id} (服务端已有)",
                    )
                    continue
            except Exception as ex:
                board.add_event(f"{company_id}-{session_id}: 探测查询失败 {ex}")

        if args.dry_run:
            session_rows.append(
                {
                    "company_id": company_id,
                    "company_name": company_name,
                    "session_id": session_id,
                    "status_code": 0,
                    "success": True,
                    "error": "dry_run",
                    "request_path": "",
                    "response_path": "",
                }
            )
            board.update(
                company_id,
                len(session_rows),
                len(session_rows),
                failed_sessions,
                session_id,
            )
            continue

        api_ok, api_msg = check_llm_api()
        if not api_ok:
            board.add_event(f"[FATAL] {company_id}: LLM API 失效，停止写入: {api_msg}")
            board.add_event(f"[INFO] {company_id}: 已成功写入 {success_sessions} 个 session")
            stop_event.set()
            break

        from generate_benchmark_memories import run_session

        result = run_session(
            backend,
            company,
            session,
            company["project_id"],
            session_source_client,
            company_dir,
            seen_company_dir,
            120,
            5,
        )
        session_rows.append(result)
        if result["success"]:
            success_sessions += 1
        else:
            failed_sessions += 1
            board.add_event(f"{company_id}-{session_id}: {result['error']}")
        board.update(
            company_id,
            success_sessions + failed_sessions,
            success_sessions,
            failed_sessions,
            session_id,
        )

    company_row = {
        "company_id": company_id,
        "company_name": company_name,
        "sessions": len(sessions),
        "turns": sum(len(s["turns"]) for s in sessions),
        "success_sessions": success_sessions,
        "failed_sessions": failed_sessions,
    }
    board.mark_finished(company_id)
    board.add_event(
        f"[完成] {company_id} {company_name}: {success_sessions} 成功 / {failed_sessions} 失败"
    )
    return company_row, session_rows


def main() -> None:
    args = parse_args()
    load_env()
    project_id = project_id_for(args.benchmark_dir)

    all_companies = company_entries()
    companies = [
        c
        for c in all_companies
        if not args.companies or c["company_id"] in [x.upper() for x in args.companies]
    ]
    for c in companies:
        c["user_id"] = f"{args.id_prefix}-{c['company_id']}"
        c["agent_id"] = f"{args.id_prefix}-{c['company_id']}-agent"
        c["project_id"] = f"{project_id}-{c['company_id']}"

    if not companies:
        print("No matching companies selected")
        sys.exit(1)

    if args.parallel < 1:
        print("[FATAL] --parallel 必须 >= 1")
        sys.exit(2)

    out_dir = Path(args.out_dir)
    seen_dir = Path(args.seen_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen_dir.mkdir(parents=True, exist_ok=True)

    panel_enabled = sys.stdout.isatty() and not args.no_panel
    board = ProgressBoard(panel_enabled, args.refresh_interval)
    stop_event = threading.Event()

    print(f"Server: {args.server}")
    print(f"Benchmark dir: {args.benchmark_dir}")
    print(f"ProjectId: {project_id}")
    print(f"Parallel companies: {args.parallel}")
    print(f"Companies: {', '.join(c['company_id'] for c in companies)}")
    print(f"Dry run: {args.dry_run}")
    print(f"Session limit: {args.session_limit}")
    print(f"进度面板: {'启用 (TTY)' if panel_enabled else '未启用（日志模式）'}")
    print("加载公司数据...")

    loaded: list[dict] = []
    for company in companies:
        company_file = Path(args.benchmark_dir) / company["file_name"]
        if not company_file.exists():
            print(f"[WARN] missing benchmark file: {company_file}")
            company["_skip"] = True
            board.register(company["company_id"], company["company_name"], 0)
            continue
        sessions = load_company(company_file)
        sessions.sort(key=lambda s: s["started_at"])
        if args.session_limit > 0:
            sessions = sessions[: args.session_limit]
        loaded.append({**company, "sessions": sessions})
        board.register(company["company_id"], company["company_name"], len(sessions))

    with Backend(args.server, 5, 180) as backend:
        try:
            print(f"Health: {backend.health()}")
        except Exception as ex:
            print(f"[FATAL] backend health check failed: {ex}")
            sys.exit(1)

        print("=" * 100)
        if panel_enabled:
            board.start_live()

        planned = [c for c in loaded if not c.get("_skip")]
        company_rows: list[dict] = []
        session_rows: list[dict] = []

        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(
                    run_company_worker,
                    backend,
                    company,
                    company["sessions"],
                    args,
                    out_dir,
                    seen_dir,
                    board,
                    stop_event,
                ): company
                for company in planned
            }
            while True:
                done = all(f.done() for f in futures)
                board.refresh()
                if done:
                    break
                time.sleep(args.refresh_interval)
            for future in as_completed(futures):
                company_row, sess_rows = future.result()
                company_rows.append(company_row)
                session_rows.extend(sess_rows)

    board.stop_live()

    summary = {
        "server": args.server,
        "benchmark_dir": args.benchmark_dir,
        "project_id": project_id,
        "out_dir": args.out_dir,
        "source_client": args.source_client,
        "parallel": args.parallel,
        "dry_run": args.dry_run,
        "companies": company_rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(out_dir / "summary.json", summary)
    write_csv(out_dir / "summary.csv", session_rows)

    board.add_event(f"Done. Summary written to: {out_dir / 'summary.json'}")
    board.add_event(f"CSV written to: {out_dir / 'summary.csv'}")
    print("=" * 100)
    print("Done. Summary written to: %s" % (out_dir / "summary.json"))
    print("CSV written to: %s" % (out_dir / "summary.csv"))


if __name__ == "__main__":
    main()
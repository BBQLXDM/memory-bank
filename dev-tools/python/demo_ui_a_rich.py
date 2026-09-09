#!/usr/bin/env python3
"""
方案 A 演示：rich 原地刷新进度面板（简洁行式布局）。

不连后端，纯模拟进度增长，用于查看终端显示效果。
运行：python3 dev-tools/python/demo_ui_a_rich.py
退出：Ctrl+C
"""

import random
import threading
import time
from datetime import datetime

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

COMPANIES = [
    {"id": "C011", "total": 82},
    {"id": "C012", "total": 85},
    {"id": "C013", "total": 83},
    {"id": "C014", "total": 81},
    {"id": "C015", "total": 79},
]

console = Console()

state = {c["id"]: {"done": 0, "success": 0, "failed": 0, "current": "", "status": "排队中"} for c in COMPANIES}
logs: list[str] = []
lock = threading.Lock()


def simulate() -> None:
    """模拟各公司进度增长并写事件日志（持续运行，直到手动退出）。"""
    while True:
        with lock:
            for c in COMPANIES:
                cid = c["id"]
                st = state[cid]
                if st["done"] >= c["total"]:
                    st["status"] = "完成"
                    continue
                st["status"] = "写入中"
                step = random.randint(1, 3)
                st["done"] = min(c["total"], st["done"] + step)
                ok = random.random() < 0.95
                if ok:
                    st["success"] = st["done"]
                else:
                    st["failed"] = st["done"] - st["success"]
                st["current"] = f"S{random.randint(1, 90):03d}"
                logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {cid}-{st['current']} 提交成功 itemIds={random.randint(1, 6)}")
                if len(logs) > 8:
                    logs.pop(0)
        time.sleep(1.0)


def build_layout() -> Panel:
    with lock:
        done_total = sum(st["done"] for st in state.values())
        grand = sum(c["total"] for c in COMPANIES)
        success_total = sum(st["success"] for st in state.values())
        failed_total = sum(st["failed"] for st in state.values())

        # 总进度条
        overall = Progress(
            SpinnerColumn(),
            TextColumn("[bold]总体[/bold]"),
            BarColumn(bar_width=24),
            TextColumn("{task.percentage:>3.0f}%  {task.completed}/{task.total}"),
        )
        task = overall.add_task("", total=grand, completed=done_total)

        # 每家公司一行
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("公司")
        table.add_column("进度", justify="left")
        table.add_column("完成", justify="right")
        table.add_column("成功", justify="right")
        table.add_column("失败", justify="right")
        table.add_column("状态")
        table.add_column("当前")
        for c in COMPANIES:
            st = state[c["id"]]
            pct = st["done"] / c["total"] * 100 if c["total"] else 100
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            status_color = "green" if st["status"] == "完成" else ("yellow" if st["status"] == "写入中" else "dim")
            table.add_row(
                f"[bold]{c['id']}[/bold]",
                bar,
                f"{st['done']}/{c['total']}",
                f"[green]{st['success']}[/green]",
                f"[red]{st['failed']}[/red]" if st["failed"] else "0",
                f"[{status_color}]{st['status']}[/{status_color}]",
                st["current"],
            )

        # 底部事件日志（最新在前）
        log_block = "\n".join(logs[-6:]) or "[dim]暂无事件[/dim]"

        body = Group(overall, table, Panel(log_block, title="事件日志", border_style="dim"))
        return Panel(body, title="并行记忆写入 · 方案A (rich)", border_style="cyan")


def main() -> None:
    console.print("[bold cyan]方案 A 演示：rich 原地刷新进度面板[/bold cyan]")
    console.print("持续运行，Ctrl+C 或直接关闭终端即可退出\n")
    t = threading.Thread(target=simulate, daemon=True)
    t.start()
    try:
        with Live(build_layout(), console=console, refresh_per_second=4, screen=False) as live:
            while t.is_alive():
                live.update(build_layout())
                time.sleep(0.25)
    except KeyboardInterrupt:
        console.print("\n[yellow]已退出演示[/yellow]")


if __name__ == "__main__":
    main()

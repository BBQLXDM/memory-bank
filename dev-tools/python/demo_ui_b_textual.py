#!/usr/bin/env python3
"""
方案 B 演示：textual 控制台仪表盘（卡片网格布局）。

不连后端，纯模拟进度增长，用于查看终端显示效果。
先安装：pip install textual
运行：python3 dev-tools/python/demo_ui_b_textual.py
退出：按 q
"""

import random
import time

from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, Log, Static

COMPANIES = [
    {"id": "C011", "total": 82},
    {"id": "C012", "total": 85},
    {"id": "C013", "total": 83},
    {"id": "C014", "total": 81},
    {"id": "C015", "total": 79},
]


class CompanyCard(Static):
    """一家公司的仪表卡片。"""

    done: reactive[int] = reactive(0)
    total: reactive[int] = reactive(82)
    success: reactive[int] = reactive(0)
    failed: reactive[int] = reactive(0)
    current: reactive[str] = reactive("-")
    status: reactive[str] = reactive("排队中")

    def render(self) -> str:
        pct = self.done / self.total * 100 if self.total else 100.0
        width = 12
        filled = int(pct / 100 * width)
        bar = "█" * filled + "░" * (width - filled)
        return "\n".join(
            [
                f"[b]{self.id}[/b]   {self.status}",
                f"{self.done} / {self.total}  ({pct:>3.0f}%)",
                bar,
                f"成功 {self.success}   失败 {self.failed}",
                f"当前 {self.current}",
            ]
        )


class Dashboard(App):
    """textual 仪表盘 demo。"""

    CSS = """
    #grid {
        grid-size: 3 2;
        grid-gutter: 1;
        height: 10;
    }
    CompanyCard {
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #log {
        height: 8;
        border: round $secondary;
    }
    #topbar {
        height: 1;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("", id="topbar")
        with Grid(id="grid"):
            for c in COMPANIES:
                yield CompanyCard(c["id"], id=c["id"])
        yield Log(id="log")
        yield Footer()

    def on_mount(self) -> None:
        self._started = time.time()
        self.cards = {c["id"]: self.query_one(f"#{c['id']}") for c in COMPANIES}
        self.log_widget = self.query_one("#log", Log)
        self.topbar = self.query_one("#topbar", Label)
        self.log_widget.write_line("事件日志就绪，按 q 退出")
        self.set_interval(1.0, self.tick)

    def tick(self) -> None:
        for c in COMPANIES:
            card = self.cards[c["id"]]
            if card.done >= c["total"]:
                card.status = "完成"
                continue
            card.status = "写入中"
            card.done = min(c["total"], card.done + random.randint(1, 3))
            card.success = card.done if random.random() < 0.95 else card.success
            card.failed = card.done - card.success
            card.current = f"S{random.randint(1, 90):03d}"
            if random.random() < 0.3:
                self.log_widget.write_line(f"{c['id']}-{card.current} 提交成功 itemIds={random.randint(1, 6)}")
        done = sum(self.cards[c["id"]].done for c in COMPANIES)
        grand = sum(c["total"] for c in COMPANIES)
        self.topbar.update(f"已用 {time.time() - self._started:.0f}s    总体 {done}/{grand}    ({done/grand*100:.0f}%)")


if __name__ == "__main__":
    Dashboard().run()

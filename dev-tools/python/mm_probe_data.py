#!/usr/bin/env python3
"""
多模态探测数据生成器（独立工具，不影响现有代码）。

生成一组含图片的对话消息（企业场景表格/单据图片），用于探测后端多模态链路：
1. 图片以 base64 内嵌到 message content 的 image block
2. 每张图片附带对应的文字描述（表格关键值），便于核对记忆抽取是否包含图片信息

生成图片尺寸小（约 480x320 PNG），控制请求体积。

用法：
    python3 dev-tools/python/mm_probe_data.py --out benchmark-results/mm-probe/data.json
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
]

# 探测样本：每个样本是一张图片 + 配套文字消息
SAMPLES = [
    {
        "id": "mm001",
        "question": "表格中2026年第一季度的营业收入是多少？",
        "rows": [
            ("项目", "金额(万元)"),
            ("2026年第一季度营业收入", "3260"),
            ("2026年第一季度净利润", "412"),
            ("2026年第一季度毛利率", "18.6%"),
        ],
        "caption": "华通路桥2026年第一季度财务简表",
        "expected_fact": "2026年第一季度营业收入3260万元",
    },
    {
        "id": "mm002",
        "question": "单据显示的银行承兑汇票到期日是哪天？",
        "rows": [
            ("票据类型", "银行承兑汇票"),
            ("票面金额", "800万元"),
            ("出票日", "2026-01-12"),
            ("到期日", "2026-07-12"),
        ],
        "caption": "银行承兑汇票主要信息单",
        "expected_fact": "银行承兑汇票到期日2026年7月12日",
    },
    {
        "id": "mm003",
        "question": "拜访纪要里客户对利率的诉求是多少？",
        "rows": [
            ("拜访时间", "2026-03-05"),
            ("拜访对象", "恒泰建材 财务总监"),
            ("核心诉求", "贷款年化利率不高于3.5%"),
            ("下次跟进", "2026-03-20"),
        ],
        "caption": "客户拜访纪要摘要",
        "expected_fact": "客户要求贷款年化利率不高于3.5%",
    },
]


def load_font(size: int = 22):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_table_image(rows: list[tuple[str, str]], caption: str) -> bytes:
    """渲染一张简单的中文表格 PNG，返回字节。"""
    font = load_font(22)
    title_font = load_font(24)
    col_w = [260, 240]
    row_h = 44
    margin = 16
    title_h = 52
    width = sum(col_w) + margin * 2
    height = title_h + row_h * len(rows) + margin * 2

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # 标题
    draw.text((margin, margin), caption, fill="black", font=title_font)

    # 表格线与文字
    y0 = title_h
    for r, (k, v) in enumerate(rows):
        y = y0 + r * row_h
        x = margin
        draw.rectangle([x, y, x + col_w[0], y + row_h], outline="black")
        draw.rectangle([x + col_w[0], y, x + col_w[0] + col_w[1], y + row_h], outline="black")
        draw.text((x + 8, y + 10), k, fill="black", font=font)
        draw.text((x + col_w[0] + 8, y + 10), v, fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_message(sample: dict, image_b64: str) -> dict:
    """构造含 image block 的 message（后端 ContentBlock 协议：type=text / type=image）。"""
    text_part = (
        f"（客户经理上传了图片：{sample['caption']}）\n"
        f"请查看图片中的{sample['caption']}内容。"
    )
    return {
        "role": "USER",
        "content": [
            {"type": "text", "text": text_part},
            {
                "type": "image",
                "source": {
                    "media_type": "image/png",
                    "data": image_b64,
                },
            },
        ],
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成多模态探测数据（独立工具）")
    parser.add_argument("--out", default="benchmark-results/mm-probe/data.json")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    items = []
    for sample in SAMPLES:
        png = render_table_image(sample["rows"], sample["caption"])
        b64 = base64.b64encode(png).decode("ascii")
        message = build_message(sample, b64)
        items.append(
            {
                "id": sample["id"],
                "image_bytes": len(png),
                "question": sample["question"],
                "expected_fact": sample["expected_fact"],
                "message": message,
            }
        )

    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已生成 {len(items)} 个多模态探测样本 -> {out_path}")
    for item in items:
        print(f"  {item['id']}: 图片 {item['image_bytes']} bytes  问题: {item['question']}")
        print(f"    期望事实: {item['expected_fact']}")
    print("\n下一步: 用 mm_write_probe.py 写入并验证记忆抽取（等待实现）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""共享工具：公司 ID <-> 名称/文件名映射、.env 加载。

所有脚本统一从 companies.json 读取公司映射，避免各脚本各自维护硬编码列表。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
COMPANIES_FILE = _SCRIPT_DIR / "companies.json"


def load_companies() -> dict[str, str]:
    """返回 {company_id: company_name}，如 {"C015": "科锐科创技术有限公司"}。"""
    with open(COMPANIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def company_file_name(company_id: str) -> str:
    """返回公司对应的 benchmark 文件名（含 .json 后缀）。"""
    companies = load_companies()
    name = companies.get(company_id)
    if not name:
        raise KeyError(f"unknown company {company_id}, choose from {sorted(companies)}")
    return f"{name}.json"


def company_entries() -> list[dict]:
    """返回 [{company_id, company_name, file_name}, ...]，按 ID 排序。"""
    companies = load_companies()
    entries = []
    for cid in sorted(companies, key=lambda x: int(x[1:])):
        entries.append(
            {
                "company_id": cid,
                "company_name": companies[cid],
                "file_name": f"{companies[cid]}.json",
            }
        )
    return entries


def load_env(path: str = ".env") -> None:
    """加载 .env，用 .env 的值覆盖（优先）已有同名环境变量，避免残留旧值。"""
    env_path = Path(path)
    if not env_path.exists():
        # 尝试相对于脚本所在目录
        env_path = _SCRIPT_DIR.parent.parent / path
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value
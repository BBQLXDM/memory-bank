#!/usr/bin/env python3
"""
复刻 GenerateBenchmarkMemories.java 的 Python 版本。

功能：
- 读取 benchmark 数据（context.sessions -> session_id/started_at/turns）
- 逐公司、逐 session 调用后端：
    POST /open/v1/memory/sync/add-message
    POST /open/v1/memory/sync/commit
- 跳过逻辑（前后程序可复用）：
    1. seen 目录已有 status=success 记录 -> 跳过
    2. 后端按 sessionSourceClient 查 items 已有数据 -> 跳过
- 参数与 Java 版一致：--companies --sessions --session-limit --id-prefix 等

用法示例：
    python3 dev-tools/python/generate_benchmark_memories.py \
        --server http://127.0.0.1:8366 \
        --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
        --companies C017 \
        --session-limit 3 \
        --source-client test0725 \
        --id-prefix test0725
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

DEFAULT_SERVER = os.environ.get("MEMIND_SERVER", "http://127.0.0.1:8366")
DEFAULT_BENCHMARK_DIR = os.environ.get(
    "BENCHMARK_DIR", "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"
)
DEFAULT_OUT_DIR = os.environ.get(
    "OUT_DIR",
    os.path.join(os.getcwd(), "benchmark-results", "memory-generation-python"),
)
DEFAULT_SEEN_DIR = os.environ.get(
    "SEEN_DIR",
    os.path.join(os.getcwd(), "benchmark-results", "memory-generation-seen"),
)
DEFAULT_SOURCE_CLIENT = os.environ.get("SOURCE_CLIENT", "benchmark-v107")
DEFAULT_ID_PREFIX = os.environ.get("ID_PREFIX", "benchmark-v107")

from memind_shared import company_entries, load_env  # noqa: E402

COMPANIES = company_entries()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark memory generation (Python)")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--benchmark-dir", default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--seen-dir", default=DEFAULT_SEEN_DIR)
    parser.add_argument("--source-client", default=DEFAULT_SOURCE_CLIENT)
    parser.add_argument("--id-prefix", default=DEFAULT_ID_PREFIX)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--session-limit", type=int, default=0)
    parser.add_argument("--connect-timeout-seconds", type=int, default=5)
    parser.add_argument("--read-timeout-seconds", type=int, default=180)
    parser.add_argument("--verify-timeout-seconds", type=int, default=120)
    parser.add_argument("--verify-poll-interval-seconds", type=int, default=5)
    parser.add_argument("--companies", nargs="*", default=[])
    parser.add_argument("--sessions", nargs="*", default=[])
    parser.add_argument(
        "--parallel-companies",
        type=int,
        default=1,
        help="公司级并行写入的并发公司数（默认 1=串行；2+ 时同一终端内多公司同时写入）",
    )
    return parser.parse_args()


def project_id_for(benchmark_dir: str) -> str:
    normalized = os.path.abspath(benchmark_dir)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return f"{os.path.basename(normalized)}-{digest[:6]}"


def parse_instant(value: str | None) -> str:
    """把各种时间格式规整成 ISO-8601（后端接受带 T 的格式）。"""
    if not value:
        return "1970-01-01T00:00:00Z"  # 与 Java 版 Instant.EPOCH 一致
    text = str(value).strip()
    if not text:
        return "1970-01-01T00:00:00Z"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return text


def load_company(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    sessions = data.get("context", {}).get("sessions", [])
    result = []
    for s in sessions:
        session_id = s.get("session_id", "unknown-session")
        started_at = s.get("started_at")
        turns = []
        for t in s.get("turns", []):
            role = str(t.get("role", "user")).lower()
            turns.append(
                {
                    "role": "ASSISTANT" if role == "assistant" else "USER",
                    "content": str(t.get("content", "")),
                    "timestamp": parse_instant(t.get("timestamp")),
                }
            )
        result.append(
            {
                "session_id": session_id,
                "started_at": parse_instant(started_at),
                "turns": turns,
            }
        )
    return result


class Backend:
    def __init__(self, server: str, connect_timeout: int, read_timeout: int) -> None:
        self.server = server.rstrip("/")
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def __enter__(self) -> "Backend":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        pass

    def _url(self, path: str) -> str:
        return f"{self.server}{path}"

    def _post(self, path: str, payload: dict, timeout: int | None = None) -> requests.Response:
        return requests.post(
            self._url(path),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=(self.connect_timeout, timeout or self.read_timeout),
        )

    def health(self) -> str:
        resp = requests.get(
            self._url("/open/v1/health"), timeout=(self.connect_timeout, 30)
        )
        resp.raise_for_status()
        data = resp.json()
        inner = data.get("data", data)
        return str(inner.get("status", inner))

    def add_message(self, user_id: str, agent_id: str, message: dict, source_client: str) -> None:
        payload = {
            "userId": user_id,
            "agentId": agent_id,
            "message": message,
            "sourceClient": source_client,
        }
        resp = self._post("/open/v1/memory/sync/add-message", payload)
        resp.raise_for_status()

    def commit(self, user_id: str, agent_id: str, source_client: str) -> None:
        payload = {
            "userId": user_id,
            "agentId": agent_id,
            "sourceClient": source_client,
        }
        resp = self._post("/open/v1/memory/sync/commit", payload, timeout=900)
        resp.raise_for_status()

    def query_items(
        self, user_id: str, agent_id: str, source_client: str, limit: int = 1
    ) -> list[dict]:
        payload = {
            "userId": user_id,
            "agentId": agent_id,
            "sourceClients": [source_client],
            "limit": limit,
        }
        resp = self._post("/open/v1/memory/items/query", payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("items", [])


def estimate_session_timeout(turns: int) -> int:
    return max(180, min(180 + turns * 30, 600))


def check_llm_api() -> tuple[bool, str]:
    """检测写库所用的 LLM API 是否可用。

    调用 DashScope 兼容接口发一个最小请求：
    - 200          -> 可用
    - 403 额度耗尽  -> 不可用（返回 False 和错误信息）
    - 其他错误      -> 视为不可用并返回错误信息

    读取 .env 中的 OPENAI_CHAT_MODEL / OPENAI_API_KEY / OPENAI_BASE_URL。
    """
    load_env()
    model = os.environ.get("OPENAI_CHAT_MODEL", "")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    if not model:
        return False, "OPENAI_CHAT_MODEL 未设置（.env 未加载？）"
    if not api_key:
        return False, "OPENAI_API_KEY 未设置（.env 未加载？）"

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=(10, 30),
        )
    except Exception as ex:
        return False, f"LLM API 请求失败: {ex}"

    if resp.status_code == 200:
        return True, f"模型 {model} 可用"
    try:
        error = resp.json().get("error", {})
        message = error.get("message", resp.text)
        code = error.get("code", "")
    except Exception:
        message = resp.text
        code = ""
    if resp.status_code == 403 and "quota" in (message + code).lower():
        return False, f"LLM API 额度耗尽 (HTTP {resp.status_code}): {message}"
    return False, f"LLM API 检测失败 (HTTP {resp.status_code}): {message}"


def has_successful_response(
    response_path: Path, project_id: str, session_id: str, source_client: str, user_id: str, agent_id: str
) -> bool:
    if not response_path.exists():
        return False
    try:
        with open(response_path, encoding="utf-8") as f:
            root = json.load(f)
    except Exception:
        return False
    if str(root.get("status", "")).lower() != "success":
        return False
    if root.get("sessionId") != session_id:
        return False
    existing_project = str(root.get("projectId", "")).strip()
    if existing_project and existing_project != project_id:
        return False
    existing_source = str(root.get("sourceClient", "")).strip()
    if existing_source and existing_source != source_client:
        return False
    existing_user = str(root.get("userId", "")).strip()
    if existing_user and existing_user != user_id:
        return False
    existing_agent = str(root.get("agentId", "")).strip()
    if existing_agent and existing_agent != agent_id:
        return False
    return True


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "company_id",
        "company_name",
        "session_id",
        "status_code",
        "success",
        "error",
        "request_path",
        "response_path",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def run_session(
    backend: Backend,
    company: dict,
    session: dict,
    project_id: str,
    source_client: str,
    company_dir: Path,
    seen_company_dir: Path,
    verify_timeout_seconds: int,
    verify_poll_interval_seconds: int,
) -> dict:
    session_id = session["session_id"]
    user_id = company["user_id"]
    agent_id = company["agent_id"]

    request_path = company_dir / f"{session_id}.request.json"
    seen_response_path = seen_company_dir / f"{session_id}.response.json"
    write_json(
        request_path,
        {
            "mode": "messages",
            "project_id": project_id,
            "session_id": session_id,
            "turns": len(session["turns"]),
            "source_client": source_client,
        },
    )

    timing = {
        "turns": len(session["turns"]),
        "add_message_seconds": 0.0,
        "commit_seconds": 0.0,
        "verify_seconds": 0.0,
        "total_seconds": 0.0,
        "verify_loops": 0,
        "status": "ok",
    }
    t_total = time.time()

    try:
        t_add = time.time()
        for turn in session["turns"]:
            text = f"[project={project_id}][session={session_id}] {turn['content']}"
            message = {
                "role": turn["role"],
                "content": [{"type": "text", "text": text}],
                "timestamp": turn["timestamp"],
            }
            backend.add_message(user_id, agent_id, message, source_client)
        timing["add_message_seconds"] = time.time() - t_add

        t_commit = time.time()
        backend.commit(user_id, agent_id, source_client)
        timing["commit_seconds"] = time.time() - t_commit

        t_verify = time.time()
        verified = False
        deadline = time.time() + max(1, verify_timeout_seconds)
        while time.time() < deadline:
            timing["verify_loops"] += 1
            try:
                items = backend.query_items(user_id, agent_id, source_client, limit=5)
                if items:
                    verified = True
                    break
            except Exception:
                pass
            time.sleep(max(1, verify_poll_interval_seconds))
        timing["verify_seconds"] = time.time() - t_verify

        if not verified:
            raise RuntimeError("commit returned success but no items were observed during verification")

        write_json(
            seen_response_path,
            {
                "status": "success",
                "userId": user_id,
                "agentId": agent_id,
                "projectId": project_id,
                "sessionId": session_id,
                "sourceClient": source_client,
            },
        )
        timing["total_seconds"] = time.time() - t_total
        return {
            "company_id": company["company_id"],
            "company_name": company["company_name"],
            "session_id": session_id,
            "status_code": 200,
            "success": True,
            "error": "",
            "request_path": str(request_path),
            "response_path": str(seen_response_path),
            "timing": timing,
        }
    except Exception as ex:
        write_json(
            seen_response_path,
            {
                "error": str(ex),
                "sessionId": session_id,
                "sourceClient": source_client,
            },
        )
        timing["status"] = "error"
        timing["total_seconds"] = time.time() - t_total
        return {
            "company_id": company["company_id"],
            "company_name": company["company_name"],
            "session_id": session_id,
            "status_code": 400,
            "success": False,
            "error": str(ex),
            "request_path": str(request_path),
            "response_path": str(seen_response_path),
            "timing": timing,
        }


def process_company(
    backend: Backend,
    company: dict,
    args: argparse.Namespace,
    out_dir: Path,
    seen_dir: Path,
    stop_event: threading.Event | None = None,
) -> tuple[dict | None, list[dict]]:
    """处理单个公司：加载会话并按顺序写入，返回 (company_row, session_rows)。

    公司数据文件缺失时返回 (None, [])。
    传入 stop_event 后，API 失效或外部要求停止时本线程会提前退出（并行模式）。
    """
    company_file = Path(args.benchmark_dir) / company["file_name"]
    if not company_file.exists():
        print(f"[WARN] missing benchmark file: {company_file}")
        return None, []

    sessions = load_company(company_file)
    sessions.sort(key=lambda s: s["started_at"])
    if args.session_limit > 0:
        sessions = sessions[: args.session_limit]

    company_dir = out_dir / company["company_id"]
    company_dir.mkdir(parents=True, exist_ok=True)
    seen_company_dir = seen_dir / company["company_id"]
    seen_company_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print(f"Company: {company['company_id']} {company['company_name']}")
    print(f"ProjectId: {company['project_id']}")
    print(f"Sessions: {len(sessions)}")

    success_sessions = 0
    failed_sessions = 0
    total_turns = 0
    session_rows: list[dict] = []

    for session in sessions:
        if stop_event is not None and stop_event.is_set():
            print(f"[INFO] {company['company_id']} 提前停止（LLM API 已失效或外部要求停止）")
            break
        session_id = session["session_id"]
        if args.sessions and session_id not in [s.upper() for s in args.sessions]:
            print(f"[SESSION] {session_id} filtered by --sessions, skipped")
            continue
        total_turns += len(session["turns"])
        request_path = company_dir / f"{session_id}.request.json"
        response_path = company_dir / f"{session_id}.response.json"
        seen_response_path = seen_company_dir / f"{session_id}.response.json"
        session_source_client = (
            f"{args.source_client}-{company['company_id']}-{session_id}"
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
                        "company_id": company["company_id"],
                        "company_name": company["company_name"],
                        "session_id": session_id,
                        "status_code": 208,
                        "success": True,
                        "error": "skipped_existing_success",
                        "request_path": str(request_path),
                        "response_path": str(seen_response_path),
                    }
                )
                success_sessions += 1
                print(
                    f"[SESSION] {session_id} skipped existing success seen={seen_response_path}"
                )
                continue

            try:
                items = backend.query_items(
                    company["user_id"], company["agent_id"], session_source_client
                )
                if items:
                    session_rows.append(
                        {
                            "company_id": company["company_id"],
                            "company_name": company["company_name"],
                            "session_id": session_id,
                            "status_code": 208,
                            "success": True,
                            "error": "skipped_already_present",
                            "request_path": str(request_path),
                            "response_path": str(response_path),
                        }
                    )
                    success_sessions += 1
                    print(
                        f"[SESSION] {session_id} skipped, already present on server "
                        f"(sourceClient={session_source_client})"
                    )
                    continue
            except Exception as ex:
                print(f"[WARN] server probe query failed for {session_source_client}: {ex}")

        if args.dry_run:
            session_rows.append(
                {
                    "company_id": company["company_id"],
                    "company_name": company["company_name"],
                    "session_id": session_id,
                    "status_code": 0,
                    "success": True,
                    "error": "dry_run",
                    "request_path": "",
                    "response_path": "",
                }
            )
            print(f"[SESSION] {session_id} dry_run turns={len(session['turns'])}")
            continue

        # 真正写入前检测 LLM API 是否可用，防止额度耗尽导致假成功
        api_ok, api_msg = check_llm_api()
        if not api_ok:
            print(f"[FATAL] LLM API 失效，停止写入: {api_msg}")
            print(f"[INFO] 已成功写入 {success_sessions} 个 session（本公司在本次运行内）")
            if stop_event is not None:
                stop_event.set()
            else:
                sys.exit(2)
            break

        timeout = estimate_session_timeout(len(session["turns"]))
        result = run_session(
            backend,
            company,
            session,
            company["project_id"],
            session_source_client,
            company_dir,
            seen_company_dir,
            args.verify_timeout_seconds,
            args.verify_poll_interval_seconds,
        )
        session_rows.append(result)
        if result["success"]:
            success_sessions += 1
        else:
            failed_sessions += 1

        print(
            f"[SESSION] {session_id} timeout={timeout}s status={result['status_code']} "
            f"turns={len(session['turns'])} error={result['error']}"
        )

    company_row = {
        "company_id": company["company_id"],
        "company_name": company["company_name"],
        "sessions": len(sessions),
        "turns": total_turns,
        "success_sessions": success_sessions,
        "failed_sessions": failed_sessions,
    }
    return company_row, session_rows


def main() -> None:
    args = parse_args()
    project_id = project_id_for(args.benchmark_dir)

    companies = [
        c
        for c in COMPANIES
        if not args.companies or c["company_id"] in [x.upper() for x in args.companies]
    ]
    for c in companies:
        c["user_id"] = f"{args.id_prefix}-{c['company_id']}"
        c["agent_id"] = f"{args.id_prefix}-{c['company_id']}-agent"
        c["project_id"] = f"{project_id}-{c['company_id']}"

    if not companies:
        print("No matching companies selected")
        sys.exit(1)

    out_dir = Path(args.out_dir)
    seen_dir = Path(args.seen_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen_dir.mkdir(parents=True, exist_ok=True)

    print(f"Server: {args.server}")
    print(f"Benchmark dir: {args.benchmark_dir}")
    print(f"ProjectId: {project_id}")
    print(f"Output dir: {args.out_dir}")
    print(f"Seen dir: {args.seen_dir}")
    print(f"Mode: messages")
    print(f"Id prefix: {args.id_prefix}")
    print(f"Source client: {args.source_client}")
    print(f"Companies: {', '.join(c['company_id'] for c in companies)}")
    print(f"Dry run: {args.dry_run}")
    print(f"Session limit: {args.session_limit}")
    print(f"Sessions: {', '.join(args.sessions) if args.sessions else 'ALL'}")
    print("=" * 100)

    company_rows = []
    session_rows = []

    with Backend(args.server, args.connect_timeout_seconds, args.read_timeout_seconds) as backend:
        try:
            print(f"Health: {backend.health()}")
        except Exception as ex:
            print(f"[FATAL] backend health check failed: {ex}")
            sys.exit(1)
        print("Skip rule: success + sessionId + optional projectId/sourceClient/userId/agentId match")

        stop_event = threading.Event() if args.parallel_companies > 1 else None

        def run_company(company: dict) -> tuple[dict | None, list[dict]]:
            return process_company(backend, company, args, out_dir, seen_dir, stop_event)

        if args.parallel_companies > 1:
            print(f"Parallel mode: {args.parallel_companies} company workers in this terminal")
            with ThreadPoolExecutor(max_workers=args.parallel_companies) as executor:
                futures = [executor.submit(run_company, company) for company in companies]
                for future in as_completed(futures):
                    company_row, sess_rows = future.result()
                    if company_row is not None:
                        company_rows.append(company_row)
                    session_rows.extend(sess_rows)
        else:
            for company in companies:
                company_row, sess_rows = run_company(company)
                if company_row is not None:
                    company_rows.append(company_row)
                session_rows.extend(sess_rows)

    summary = {
        "server": args.server,
        "benchmark_dir": args.benchmark_dir,
        "project_id": project_id,
        "out_dir": args.out_dir,
        "mode": "messages",
        "source_client": args.source_client,
        "dry_run": args.dry_run,
        "companies": company_rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(out_dir / "summary.json", summary)
    write_csv(out_dir / "summary.csv", session_rows)

    print("=" * 100)
    print(f"Done. Summary written to: {out_dir / 'summary.json'}")
    print(f"CSV written to: {out_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()

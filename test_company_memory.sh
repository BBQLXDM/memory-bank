# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8366}"
STRATEGY="${STRATEGY:-DEEP}"
TRACE="${TRACE:-true}"
OUT_DIR="${OUT_DIR:-./company-memory-results}"
BENCHMARK_FILE="${BENCHMARK_FILE:-/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/core/鑫科精密零部件制造有限公司.json}"
USER_ID="${USER_ID:-benchmark-v107-c019-isolated}"
AGENT_ID="${AGENT_ID:-benchmark-v107-C019-isolated-agent}"
MAX_QUESTIONS="${MAX_QUESTIONS:-0}"

mkdir -p "${OUT_DIR}"

if [[ ! -f "${BENCHMARK_FILE}" ]]; then
  echo "Benchmark file not found: ${BENCHMARK_FILE}" >&2
  exit 1
fi

if ! [[ "${MAX_QUESTIONS}" =~ ^[0-9]+$ ]]; then
  echo "MAX_QUESTIONS must be a non-negative integer." >&2
  exit 1
fi

records_file="${OUT_DIR}/company-qa-records.tsv"
summary_file="${OUT_DIR}/summary.jsonl"
: > "${records_file}"
: > "${summary_file}"

python3 - "${BENCHMARK_FILE}" "${records_file}" "${MAX_QUESTIONS}" <<'PY'
import json
import sys
from pathlib import Path

benchmark_file = Path(sys.argv[1])
out_file = Path(sys.argv[2])
max_questions = int(sys.argv[3])
obj = json.loads(benchmark_file.read_text(encoding="utf-8"))
qas = obj.get("qa") or obj.get("qas") or []
if not isinstance(qas, list):
    raise SystemExit("Unexpected benchmark format: qa list not found")

with out_file.open("w", encoding="utf-8") as f:
    count = 0
    for item in qas:
        if not isinstance(item, dict):
            continue
        qa_id = str(item.get("qa_id") or "")
        question = str(item.get("question") or "")
        answer_type = str(item.get("answer_type") or "")
        capability = str(item.get("capability") or "")
        sub_type = str(item.get("capability_sub_type") or "")
        if not question:
            continue
        f.write(f"{qa_id}\t{answer_type}\t{capability}\t{sub_type}\t{question}\n")
        count += 1
        if max_questions > 0 and count >= max_questions:
            break
PY

run_one() {
  local idx="$1"
  local qa_id="$2"
  local answer_type="$3"
  local capability="$4"
  local sub_type="$5"
  local query="$6"

  local slug="${qa_id:-question-${idx}}"
  slug="${slug//[^a-zA-Z0-9._-]/_}"
  local out_file="${OUT_DIR}/${idx}-${slug}.json"

  echo "============================================================"
  echo "#${idx} ${qa_id}"
  echo "type=${answer_type} capability=${capability} sub_type=${sub_type}"
  echo "query=${query}"
  echo "memory=${USER_ID}:${AGENT_ID}"
  echo "saving=${out_file}"

  curl -fsS -X POST "${BASE_URL}/open/v1/memory/retrieve" \
    -H "Content-Type: application/json" \
    -d "$(python3 - "${USER_ID}" "${AGENT_ID}" "${query}" "${STRATEGY}" "${TRACE}" <<'PY'
import json
import sys

user_id, agent_id, query, strategy, trace = sys.argv[1:6]
print(json.dumps({
    "userId": user_id,
    "agentId": agent_id,
    "query": query,
    "strategy": strategy,
    "trace": trace.lower() == "true",
}, ensure_ascii=False))
PY
)" | tee "${out_file}" >/dev/null

  python3 - "${out_file}" "${summary_file}" "${idx}" "${qa_id}" "${answer_type}" "${capability}" "${sub_type}" <<'PY'
import json
import sys
from pathlib import Path

response_file = Path(sys.argv[1])
summary_file = Path(sys.argv[2])
idx = sys.argv[3]
qa_id = sys.argv[4]
answer_type = sys.argv[5]
capability = sys.argv[6]
sub_type = sys.argv[7]

obj = json.loads(response_file.read_text(encoding="utf-8"))
result = obj.get("data") or obj.get("result") or obj
items = result.get("items") or []
first = items[0] if items else {}
summary = {
    "index": int(idx),
    "qa_id": qa_id,
    "answer_type": answer_type,
    "capability": capability,
    "capability_sub_type": sub_type,
    "status": result.get("status"),
    "query": result.get("query"),
    "items": len(items),
    "top1": {
        "id": first.get("id"),
        "text": first.get("text"),
        "finalScore": first.get("finalScore"),
    } if first else None,
    "insights": len(result.get("insights") or []),
    "rawData": len(result.get("rawData") or []),
    "evidences": result.get("evidences") or [],
    "hasTrace": result.get("trace") is not None,
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
with summary_file.open("a", encoding="utf-8") as f:
    f.write(json.dumps(summary, ensure_ascii=False) + "\n")
PY
}

idx=0
while IFS=$'\t' read -r qa_id answer_type capability sub_type query; do
  idx=$((idx + 1))
  run_one "$idx" "$qa_id" "$answer_type" "$capability" "$sub_type" "$query"
done < "${records_file}"

echo "============================================================"
echo "Done. Raw responses are under: ${OUT_DIR}"
echo "Summary file: ${summary_file}"

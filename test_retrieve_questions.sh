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
OUT_DIR="${OUT_DIR:-./question-test-results}"

questions=(
  "更正之后，研发贷的当前利率是多少？"
  "鑫科精密零部件制造有限公司在2024年的授信额度是多少？"
  "抵押物复评贬值对授信额度有何影响？"
  "鑫科精密零部件制造有限公司的配套设施、车间、实验室和仓储情况怎样？"
  "现场是否禁止拍照和录音？"
)

user_ids=(
  "benchmark-v107-c016-isolated"
  "benchmark-v107-c017-isolated"
  "benchmark-v107-c018-isolated"
  "benchmark-v107-c019-isolated"
  "benchmark-v107-c020-isolated"
)

agent_ids=(
  "benchmark-v107-C016-isolated-agent"
  "benchmark-v107-C017-isolated-agent"
  "benchmark-v107-C018-isolated-agent"
  "benchmark-v107-C019-isolated-agent"
  "benchmark-v107-C020-isolated-agent"
)

if [[ ${#questions[@]} -ne ${#user_ids[@]} || ${#questions[@]} -ne ${#agent_ids[@]} ]]; then
  echo "Questions and memory id arrays must have the same length." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

run_one() {
  local idx="$1"
  local query="$2"
  local user_id="$3"
  local agent_id="$4"
  local out_file="${OUT_DIR}/question-${idx}.json"

  echo "============================================================"
  echo "Q${idx}: ${query}"
  echo "Memory ID: ${user_id}:${agent_id}"
  echo "Saving response to ${out_file}"

  curl -fsS -X POST "${BASE_URL}/open/v1/memory/retrieve" \
    -H "Content-Type: application/json" \
    -d "$(python3 - "${user_id}" "${agent_id}" "${query}" "${STRATEGY}" "${TRACE}" <<'PY'
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

  echo "Response summary:"
  python3 - "${out_file}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
obj = json.loads(path.read_text(encoding="utf-8"))
result = obj.get("data") or obj.get("result") or obj
items = result.get("items") or []
print(json.dumps({
    "status": result.get("status"),
    "strategy": result.get("strategy"),
    "query": result.get("query"),
    "items": len(items),
    "topItems": [
        {
            "id": item.get("id"),
            "text": item.get("text"),
            "finalScore": item.get("finalScore"),
        }
        for item in items[:3]
    ],
    "insights": len(result.get("insights") or []),
    "rawData": len(result.get("rawData") or []),
    "evidences": result.get("evidences") or [],
    "hasTrace": result.get("trace") is not None,
}, ensure_ascii=False, indent=2))
PY
}

for i in "${!questions[@]}"; do
  run_one "$((i + 1))" "${questions[$i]}" "${user_ids[$i]}" "${agent_ids[$i]}"
done

echo "============================================================"
echo "Done. Raw responses are under: ${OUT_DIR}"

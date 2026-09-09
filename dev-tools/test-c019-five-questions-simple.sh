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

cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://127.0.0.1:8366}"
STRATEGY="${STRATEGY:-SIMPLE}"
TRACE="${TRACE:-true}"
OUT_DIR="${OUT_DIR:-$PWD/benchmark-results/c019-five-questions-simple}"
USER_ID="benchmark-v107-C019-isolated"
AGENT_ID="benchmark-v107-C019-isolated-agent"

mkdir -p "$OUT_DIR"

queries=(
  "更正之后，研发贷的当前利率是多少？"
  "鑫科精密零部件制造有限公司在2024年的授信额度是多少？"
  "抵押物复评贬值对授信额度有何影响？"
  "配套设施完善情况怎样？"
  "生产车间精密加工区域是否允许现场拍照或录音？"
)

expectations=(
  "LPR下浮50个基点"
  "2024年的授信额度"
  "抵押物复评贬值会影响授信额度"
  "配套设施完善"
  "禁止现场拍照录音"
)

if [[ ${#queries[@]} -ne ${#expectations[@]} ]]; then
  echo "queries and expectations must have the same length" >&2
  exit 1
fi

run_one() {
  local idx="$1"
  local query="$2"
  local expect="$3"
  local out_file="$OUT_DIR/${idx}.json"

  echo "============================================================"
  echo "Q${idx}: ${query}"
  echo "Expect: ${expect}"

  python3 - "$BASE_URL" "$USER_ID" "$AGENT_ID" "$STRATEGY" "$TRACE" "$query" "$out_file" <<'PY'
import json
import sys
import urllib.request

base_url, user_id, agent_id, strategy, trace, query, out_file = sys.argv[1:8]
payload = {
    "userId": user_id,
    "agentId": agent_id,
    "query": query,
    "strategy": strategy,
    "trace": trace.lower() == "true",
}
req = urllib.request.Request(
    f"{base_url}/open/v1/memory/retrieve",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=60) as resp:
    text = resp.read().decode("utf-8")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(text)
    obj = json.loads(text)
    result = obj.get("data") or obj.get("result") or obj
    items = result.get("items") or []
    print(json.dumps({
        "status": result.get("status"),
        "items": len(items),
        "hasTrace": result.get("trace") is not None,
    }, ensure_ascii=False, indent=2))
    print("Top 15:")
    for rank, item in enumerate(items[:15], 1):
        text = item.get("text") or ""
        print(f"{rank:02d}. score={item.get('finalScore')} | {text}")
PY

  python3 - "$out_file" "$expect" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expect = sys.argv[2]
obj = json.loads(path.read_text(encoding='utf-8'))
result = obj.get('data') or obj.get('result') or obj
items = result.get('items') or []
rank = None
matched_text = None
for idx, item in enumerate(items, 1):
    text = (item.get('text') or '') + ' ' + json.dumps(item, ensure_ascii=False)
    if expect in text:
        rank = idx
        matched_text = item.get('text')
        break
print('Match:', rank if rank is not None else 'NOT_FOUND')
if matched_text:
    print('Matched text:', matched_text)
PY
}

for i in "${!queries[@]}"; do
  run_one "$((i + 1))" "${queries[$i]}" "${expectations[$i]}"
done

echo "============================================================"
echo "Done. Raw responses are under: $OUT_DIR"

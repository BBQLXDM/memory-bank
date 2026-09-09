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

JACKSON_CP="$(find "$HOME/.m2/repository/com/fasterxml/jackson/core" -name '*.jar' | paste -sd:)"
RESULT_DIR="$PWD/benchmark-results/c019-five-new-questions"
USER_ID="benchmark-v107-c019-isolated"
AGENT_ID="benchmark-v107-C019-isolated-agent"
FILE="/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/core/鑫科精密零部件制造有限公司.json"

mkdir -p "$RESULT_DIR"

python3 - <<'PY' "$FILE" "$RESULT_DIR/questions.jsonl"
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
out = Path(sys.argv[2])
obj = json.loads(src.read_text(encoding='utf-8'))
qas = obj.get('qa_items') or obj.get('qa') or obj.get('qas') or []
selected = [
    'std_鑫科精密_u0284',
    'std_鑫科精密_a0288',
    'std_鑫科精密_t0268',
    'std_鑫科精密_t0075',
    'std_鑫科精密_t0275',
]
by_id = {item.get('qa_id'): item for item in qas if isinstance(item, dict)}
missing = [qa_id for qa_id in selected if qa_id not in by_id]
if missing:
    raise SystemExit(f"Missing QA ids in benchmark file: {missing}")
with out.open('w', encoding='utf-8') as f:
    for qa_id in selected:
        item = by_id[qa_id]
        payload = {
            'qa_id': qa_id,
            'question': item.get('question', ''),
            'answer': item.get('answer', ''),
            'answer_type': item.get('answer_type', ''),
            'capability': item.get('capability', ''),
            'capability_sub_type': item.get('capability_sub_type', ''),
        }
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')
PY

idx=0
while IFS= read -r line; do
  idx=$((idx + 1))
  qa_id="$(python3 - <<'PY' "$line"
import json, sys
print(json.loads(sys.argv[1])['qa_id'])
PY
)"
  question="$(python3 - <<'PY' "$line"
import json, sys
print(json.loads(sys.argv[1])['question'])
PY
)"
  out_file="$RESULT_DIR/${idx}-${qa_id}.json"
  echo "============================================================"
  echo "#${idx} ${qa_id}"
  echo "query=${question}"
  curl -fsS -X POST "http://127.0.0.1:8366/open/v1/memory/retrieve" \
    -H "Content-Type: application/json" \
    -d "$(python3 - <<'PY' "$USER_ID" "$AGENT_ID" "$question"
import json, sys
print(json.dumps({
    'userId': sys.argv[1],
    'agentId': sys.argv[2],
    'query': sys.argv[3],
    'strategy': 'DEEP',
    'trace': True,
}, ensure_ascii=False))
PY
)" | tee "$out_file" >/dev/null
  python3 - <<'PY' "$out_file"
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
obj = json.loads(p.read_text(encoding='utf-8'))
result = obj.get('data') or obj.get('result') or obj
items = result.get('items') or []
print(json.dumps({
    'status': result.get('status'),
    'items': len(items),
    'top1': items[0].get('text') if items else None,
    'hasTrace': result.get('trace') is not None,
}, ensure_ascii=False, indent=2))
PY
  echo ""
done < "$RESULT_DIR/questions.jsonl"

echo "Done. Results in: $RESULT_DIR"

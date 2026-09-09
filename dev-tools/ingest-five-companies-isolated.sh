#!/usr/bin/env bash
#
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
#

set -euo pipefail

cd "$(dirname "$0")/.."

JACKSON_CP="$(find "$HOME/.m2/repository/com/fasterxml/jackson/core" -name '*.jar' | paste -sd:)"
DATA_DIR="/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"
RESULT_DIR="$PWD/benchmark-results/isolated"
USER_PREFIX="benchmark-v107"

companies=(
  "鑫科精密零部件制造有限公司"
  "锐科航空装备股份有限公司"
  "鑫源精密机械制造有限公司"
  "联科绿筑新型建材有限公司"
  "绿能新源装备有限公司"
)

mkdir -p "$RESULT_DIR"

run_company() {
  local company="$1"
  local file="$DATA_DIR/$company.json"
  local meta
  meta="$(python3 - <<'PY' "$file"
import json
import sys
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text(encoding='utf-8'))
print(d['company_id'])
print(d['company_name'])
PY
)"
  local company_id company_name
  company_id="$(printf '%s\n' "$meta" | sed -n '1p')"
  company_name="$(printf '%s\n' "$meta" | sed -n '2p')"

  local user_id="${USER_PREFIX}-${company_id}-isolated"
  local agent_id="${USER_PREFIX}-${company_id}-isolated-agent"
  local progress_file="$RESULT_DIR/${company_id}.jsonl"

  printf '\n============================================================\n'
  printf '开始处理：%s (%s)\n' "$company_name" "$company_id"
  printf '记忆隔离：userId=%s\n' "$user_id"
  printf '============================================================\n'

  java -cp "dev-tools/java:$JACKSON_CP" MemindBenchmarkRunner \
    --file "$file" \
    --max-sessions 999 \
    --max-qa 0 \
    --mode ingest \
    --user-id "$user_id" \
    --agent-id "$agent_id" \
    --async-ingest \
    --wait-seconds 300 \
    --poll-seconds 5 \
    --progress-file "$progress_file"
}

for company in "${companies[@]}"; do
  if ! run_company "$company"; then
    printf '\n%s 写入失败，批量任务已停止。\n' "$company" >&2
    printf '修复后重新运行同一脚本，已完成的 Session 会自动跳过。\n' >&2
    exit 1
  fi
done

printf '\n五家公司已按独立记忆写入完成。\n'
printf '结果目录：%s\n' "$RESULT_DIR"

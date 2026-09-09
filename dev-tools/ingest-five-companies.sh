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

set -u

cd "$(dirname "$0")/.." || exit 1

JACKSON_CP="$(find "$HOME/.m2/repository/com/fasterxml/jackson/core" -name '*.jar' | paste -sd:)"
DATA_DIR="/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick"
PROGRESS_FILE="$PWD/benchmark-results/shared-timeline-v1.jsonl"
USER_ID="benchmark-v107-shared"
AGENT_ID="benchmark-v107-shared-timeline-v1"

companies=(
  "鑫科精密零部件制造有限公司"
  "锐科航空装备股份有限公司"
  "鑫源精密机械制造有限公司"
  "联科绿筑新型建材有限公司"
  "绿能新源装备有限公司"
)

for company in "${companies[@]}"; do
  file="$DATA_DIR/$company.json"
  printf '\n============================================================\n'
  printf '开始处理：%s\n' "$company"
  printf '============================================================\n'

  java -cp "dev-tools/java:$JACKSON_CP" MemindBenchmarkRunner \
    --file "$file" \
    --max-sessions 999 \
    --max-qa 0 \
    --mode ingest \
    --user-id "$USER_ID" \
    --agent-id "$AGENT_ID" \
    --async-ingest \
    --wait-seconds 300 \
    --poll-seconds 5 \
    --progress-file "$PROGRESS_FILE"

  status=$?
  if (( status != 0 )); then
    printf '\n%s 写入失败（退出码 %d），批量任务已停止。\n' "$company" "$status" >&2
    printf '修复后重新运行本脚本，已完成的 Session 会自动跳过。\n' >&2
    exit "$status"
  fi
done

printf '\n五家公司写入完成。\n'
printf '进度文件：%s\n' "$PROGRESS_FILE"

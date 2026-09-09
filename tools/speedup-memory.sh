#!/usr/bin/env bash
# 加速 benchmark：关闭 Insight 抽取（默认开启但每个 session 抽 0 条，纯耗时）
# 用法: bash tools/speedup-memory.sh [on|off]
set -euo pipefail

SERVER="${MEMIND_SERVER:-http://127.0.0.1:8366}"
ACTION="${1:-off}"
VALUE="false"
if [ "$ACTION" = "on" ]; then
  VALUE="true"
fi

TMP_GET="$(mktemp)"
TMP_PUT="$(mktemp)"

echo "== 1. GET 当前配置 =="
curl -s -o "$TMP_GET" -w "HTTP %{http_code}\n" \
  "$SERVER/admin/v1/config/memory-options"
VERSION="$(python3 -c "import json;print(json.load(open('$TMP_GET'))['data']['version'])" 2>/dev/null || echo "")"
if [ -z "$VERSION" ]; then
  echo "读取配置失败，GET 响应如下："
  cat "$TMP_GET"
  exit 1
fi
echo "当前版本: $VERSION"

echo "== 2. 修改 extraction.insight.enabled = $VALUE =="
python3 - "$TMP_GET" "$VALUE" "$TMP_PUT" <<'PY'
import json, sys
src, value, dst = sys.argv[1], sys.argv[2], sys.argv[3]
value = json.loads(value)  # 转成 boolean
data = json.load(open(src))
cfg = data["data"]["config"]
changed = False
for group, items in cfg.items():
    for item in items:
        if item.get("key") == "extraction.insight.enabled":
            item["value"] = value
            changed = True
if not changed:
    print("警告: 未找到 extraction.insight.enabled，将整个配置原样 PUT")
out = {"expectedVersion": data["data"]["version"], "config": cfg}
json.dump(out, open(dst, "w"), ensure_ascii=False, indent=2)
print("PUT body 已生成:", dst)
PY

echo "== 3. PUT 更新 =="
curl -s -w "\nHTTP %{http_code}\n" \
  -X PUT "$SERVER/admin/v1/config/memory-options" \
  -H "Content-Type: application/json" \
  --data-binary "@$TMP_PUT"

echo
rm -f "$TMP_GET" "$TMP_PUT"

#!/usr/bin/env bash
# 测试记忆体：写入 -> commit -> 查询
set -euo pipefail

SERVER="${MEMIND_SERVER:-http://127.0.0.1:8366}"
USER_ID="memory-test-001"
AGENT_ID="memory-test-001-agent"
SOURCE_CLIENT="memory-test-client"

echo "== 1. 写入一条消息 (add-message) =="
cat > /tmp/test-add-message.json <<'EOF'
{
  "userId": "memory-test-001",
  "agentId": "memory-test-001-agent",
  "message": {
    "role": "USER",
    "content": [
      {"type": "text", "text": "我的名字是测试记忆体，喜欢的颜色是蓝色，住在北京。"}
    ],
    "timestamp": "2026-08-13T10:30:00Z"
  },
  "sourceClient": "memory-test-client"
}
EOF
curl -s -w "\nHTTP %{http_code}\n" --max-time 60 \
  -X POST "$SERVER/open/v1/memory/sync/add-message" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/test-add-message.json

echo
echo "== 2. commit =="
cat > /tmp/test-commit.json <<'EOF'
{
  "userId": "memory-test-001",
  "agentId": "memory-test-001-agent",
  "sourceClient": "memory-test-client"
}
EOF
curl -s -w "\nHTTP %{http_code}\n" --max-time 90 \
  -X POST "$SERVER/open/v1/memory/sync/commit" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/test-commit.json

echo
echo "== 3. 查询记忆 =="
cat > /tmp/test-query.json <<'EOF'
{
  "userId": "memory-test-001",
  "agentId": "memory-test-001-agent",
  "limit": 10,
  "sourceClients": ["memory-test-client"]
}
EOF
curl -s -w "\nHTTP %{http_code}\n" --max-time 60 \
  -X POST "$SERVER/open/v1/memory/items/query" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/test-query.json

# 记忆写入：LLM API 与向量化问题排查

> 本文档记录记忆写入过程中遇到的 **LLM 模型 / API 额度 / 向量化 / 代码修改生效** 相关问题，
> 以及写入前的 API 有效性检测机制。是 `memory-write-guide.md`（写入流程与基础踩坑）的补充。

适用版本：benchmark_v1_0_7，C017/C018/C020，`id-prefix=benchmark-v107`。

---

## 一、写入前 API 有效性检测（check_llm_api）

### 为什么需要

写入的 commit 阶段依赖 LLM 抽取（MemoryItem）。如果 LLM API 额度耗尽，commit 会：

- 返回 `SUCCESS` 但 `itemIds=[]`（抽取失败，**假成功**）——**seen 记录为 success 但服务器无 item**
- 或返回 500 `Retries exhausted`（真实失败）

假成功会污染 `seen` 记录，重跑也无法自动补齐。因此在**每个 session 真正写入前**检测 LLM API。

### 实现位置

`dev-tools/python/generate_benchmark_memories.py`

```python
def check_llm_api() -> tuple[bool, str]:
    """检测写库所用的 LLM API 是否可用。

    调用 DashScope 兼容接口发一个最小请求：
    - 200          -> 可用
    - 403 额度耗尽  -> 不可用（返回 False 和错误信息）
    - 其他错误      -> 视为不可用并返回错误信息

    读取 .env 中的 OPENAI_CHAT_MODEL / OPENAI_API_KEY / OPENAI_BASE_URL。
    """
```

- 读取 `.env` 的 `OPENAI_CHAT_MODEL` / `OPENAI_API_KEY` / `OPENAI_BASE_URL`
- 发 `POST {base_url}/chat/completions`，`{"model": ..., "messages":[{"role":"user","content":"ping"}], "max_tokens":5}`
- 200 → 可用；403 且含 `quota` → 「额度耗尽」；其他 → 报错

### 调用位置

`main()` 的 session 循环内，**跳过逻辑之后、`run_session` 之前**：

```python
# 真正写入前检测 LLM API 是否可用，防止额度耗尽导致假成功
api_ok, api_msg = check_llm_api()
if not api_ok:
    print(f"[FATAL] LLM API 失效，停止写入: {api_msg}")
    print(f"[INFO] 已成功写入 {success_sessions} 个 session（本公司在本次运行内）")
    sys.exit(2)
```

- 跳过的 session 不触发检测（不浪费请求）
- 检测失败：打印 FATAL + 已成功数量，`exit(2)`

### 检测命令（手动 curl）

```bash
cd /home/zzx/py/Memind-Local-Dev
set -a; source .env; set +a

resp=$(curl -s --max-time 30 \
  https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d "{\"model\":\"$OPENAI_CHAT_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":100}")
echo "$resp" | python3 -c "
import json, sys
r = json.load(sys.stdin)
msg = (r.get('choices') or [{}])[0].get('message', {})
content = msg.get('content', '')
reasoning = msg.get('reasoning_content', '')
print(f'content={content!r}')
print(f'reasoning={reasoning[:50]!r}')
print('判定:', '✅ content 非空，可用' if content.strip() else '❌ content 为空，不可用')
"
```

> **重要**：HTTP 200 不等于可用。必须同时检查 `content` 非空（见下节"推理模型坑"）。

---

## 二、问题 1：HTTP 200 但 content 为空（推理模型坑）

### 现象

- 模型检测 `HTTP 200`，但响应 `content=""`，只有 `reasoning_content`
- 写入 commit 返回 `itemIds=[]`，`verify items found: 0`

### 根因

`deepseek-v4-flash-0731`、`glm-5.2` 等是**推理模型**（带思考链）：

```json
{"message":{"content":"","reasoning_content":"1.  **An...","role":"assistant"},"finish_reason":"length"}
```

- token 全部花在思考（reasoning）上，正式输出 `content` 为空
- commit 阶段 MemoryItem 结构化抽取依赖 content 文本 → 拿不到 → `itemIds=[]`

### 教训

**给 memory 抽取选模型时，不要选带推理链（reasoning）的模型**。要 flash/turbo/max 等直接输出 content 的模型，且检测时验证 content 非空。

---

## 三、问题 2：LLM API 额度耗尽

### 现象

```json
{"error":{"message":"Free quota exhausted. To continue accessing the model on a paid basis,
  please add funds or disable the \"use free tier only\" mode in the management console.",
  "type":"AllocationQuota.FreeTierOnly","code":"AllocationQuota.FreeTierOnly"}}
```

- 手工 curl：`HTTP 403` + `AllocationQuota.FreeTierOnly`
- 后端 commit：`403: Free quota exhausted`（`LlmContextCommitDetector` 日志）
- 写入侧：`LLM HTTP 403`（QA 回答）或假成功（写入）

### 根因

DashScope 免费额度（100 万 tokens/模型）耗尽。批量写入非常耗额度：
约 229 个 session × 每个 commit 多次 LLM 调用 → flash 模型额度很快用完。

### 处理

1. 到阿里云百炼控制台**关闭 "use free tier only"** 或充值
2. 或换一个仍有额度的模型（检测后才能确认，每个模型额度独立）
3. 换模型后**必须重启后端**并重新 source `.env`（见问题 3）
4. 删除受影响 session 的 seen 记录后重跑（假成功后无法自动跳过）

### 各模型额度检测（2026-08-14 实测）

| 模型 | HTTP | 结论 |
|---|---|---|
| `qwen3.7-flash` | 403 | 额度耗尽 |
| `qwen3.7-plus` | 403 | 额度耗尽 |
| `qwen3.7-max-preview` | 200 + content 非空 | ✅ 可用 |
| `deepseek-v4-flash-0731` | 200 | 可用但推理模型，不适合 |
| `glm-5.2` | 200 | content 空（推理模型），不适合 |

---

## 四、问题 3：后端启动未加载 .env → 用的旧模型

### 现象

- `.env` 已改为 `OPENAI_CHAT_MODEL=qwen3.7-max-preview`
- 后端启动时间在 `.env` 修改之后
- 但 commit 仍报 `403 Free quota exhausted`（DashScope 的旧模型错误）

### 根因

**mvn 启动命令没有 `set -a; source .env; set +a`**：

```bash
# 错误：裸 mvn，读不到 .env
mvn -pl memind-server spring-boot:run

# 正确：先加载 .env 环境变量
source "$HOME/.sdkman/bin/sdkman-init.sh"; sdk env
set -a; source .env; set +a
mvn -pl memind-server spring-boot:run
```

Spring AI 的 `OPENAI_CHAT_MODEL` 等从环境变量读取，不读 `.env` 文件。裸 mvn 会用 `application.yml` 默认值或 shell 残留的旧值。

### 验证后端是否用了正确模型

看后端日志 commit 阶段是否仍报 `403` / `batch size`；或写入后 `verify items found > 0`。

---

## 五、问题 4：embedding 批量超限（batch size > 10）

### 现象

commit 返回：

```json
{"error":{"code":"internal_error","message":"Memory extraction failed",
  "details":{"operation":"commit","reason":"Retries exhausted: 3/3"}}}
```

后端日志：

```text
WARN SpringAiMemoryVector: Vector operation failed, retrying 1 time:
  400: InternalError.Algo.InvalidParameter: Value error, batch size is invalid,
  it should not be larger than 10.: input.contents
```

### 根因

`FileSimpleVectorStore.doAdd()` 原来的实现**一次性批量 embedding 全部 documents**：

```java
List<float[]> embeddings = this.embeddingModel.embed(texts);  // 全部一起，不分批
```

当抽取出的 item 数量 > 10（S021 有 13 条），DashScope `text-embedding-v4` 单请求批量上限 10 → 拒绝 → 重试 3 次全失败 → commit 500。

### 修复

`memind-plugins/memind-plugin-ai-spring-ai/src/main/java/com/openmemind/ai/memory/plugin/ai/spring/FileSimpleVectorStore.java`

`doAdd()` 改为分批处理（每批 ≤ `MAX_EMBEDDING_BATCH_SIZE = 10`），与 `embedAll()` 保持一致：

```java
@Override
public void doAdd(List<Document> documents) {
    Objects.requireNonNull(documents, "Documents list cannot be null");
    if (documents.isEmpty()) {
        return;
    }
    synchronized (persistLock) {
        for (int start = 0; start < documents.size(); start += MAX_EMBEDDING_BATCH_SIZE) {
            List<Document> batch =
                    documents.subList(
                            start,
                            Math.min(start + MAX_EMBEDDING_BATCH_SIZE, documents.size()));
            List<String> texts =
                    batch.stream()
                            .map(this.embeddingModel::getEmbeddingContent)
                            .map(text -> Objects.requireNonNullElse(text, ""))
                            .toList();
            List<float[]> embeddings = this.embeddingModel.embed(texts);
            if (embeddings.size() != batch.size()) {
                throw new IllegalStateException(
                        "EmbeddingModel returned "
                                + embeddings.size()
                                + " embeddings for "
                                + batch.size()
                                + " documents");
            }
            for (int i = 0; i < batch.size(); i++) {
                Document document = batch.get(i);
                putStoreContent(document.getId(), newStoreContent(document, embeddings.get(i)));
            }
        }
        persistLocked();
    }
}
```

**修复后**：commit 正常，后端日志 `MemoryItem completed: newItems=13`。

---

## 六、问题 5：修改源码后必须重新 install，后端才生效

### 现象

改完 `FileSimpleVectorStore.java` 重启后端，问题依旧（仍报 batch size）。

### 根因

后端以 `~/.m2` 的 **jar 形式**依赖插件模块：

```text
/home/zzx/.m2/repository/com/openmemind/ai/memind-plugin-ai-spring-ai/
  0.2.0-SNAPSHOT/memind-plugin-ai-spring-ai-0.2.0-SNAPSHOT.jar   ← 后端加载这份
```

只改源码不重新 install，后端仍加载旧 jar。

### 处理

```bash
cd /home/zzx/py/Memind-Local-Dev

mvn -pl memind-plugins/memind-plugin-ai-spring-ai install -DskipTests -q
echo $?   # 0 = 成功

# 确认 jar 时间更新
ls -la ~/.m2/repository/com/openmemind/ai/memind-plugin-ai-spring-ai/0.2.0-SNAPSHOT/memind-plugin-ai-spring-ai-0.2.0-SNAPSHOT.jar
```

确认时间变成现在 → 再重启后端。

### 坑：Spotless 格式检查

`mvn install` 会先跑 spotless 格式检查，格式不过直接失败：

```text
[ERROR] Failed to execute goal com.diffplug.spotless:spotless-maven-plugin...check
[ERROR] Run 'mvn spotless:apply' to fix these violations.
```

处理：

```bash
mvn -pl memind-plugins/memind-plugin-ai-spring-ai spotless:apply
# 或直接按错误提示手动改格式
mvn -pl memind-plugins/memind-plugin-ai-spring-ai install -DskipTests -q
```

注意**行宽**：超长的行（如 `List<Document> batch = documents.subList(...)`）必须手动换行成多行格式。

---

## 七、verify "items found: 0" 的误判说明

### 现象

commit 返回 `status=SUCCESS` 且 `itemIds` 非空（13 条），但 `verify items found: 0`。

### 原因

`items/query` 按 `sourceClients` 过滤，而这次写入**复用了旧 rawData**（`rawDataId` 与第一次一致），item 关联的 sourceClient 可能是旧标识，用 `-r2` 新标识查不到。

### 确认数据真实存在的方法

1. 后端日志：`MemoryItem completed: newItems=13`（✅ 已落库）
2. 不按 sourceClient 过滤直接查：
   ```bash
   curl -s -X POST http://127.0.0.1:8366/open/v1/memory/items/query \
     -H "Content-Type: application/json" \
     -d '{"userId":"benchmark-v107-C017","agentId":"benchmark-v107-C017-agent","limit":20}'
   ```
3. 检索验证（最有说服力）：
   ```bash
   curl -s -X POST http://127.0.0.1:8366/open/v1/memory/retrieve \
     -H "Content-Type: application/json" \
     -d '{"userId":"benchmark-v107-C017","agentId":"benchmark-v107-C017-agent",
          "query":"实际控制人李伟持股比例","strategy":"SIMPLE","trace":true}'
   ```

> verify 的 WARNING 不代表写入失败，需要结合后端日志 + 直接查询确认。

---

## 八、写库验证脚本

`dev-tools/python/verify_recent_memories.py`：检查最近写入 session 在服务器上是否真有 item。

```bash
python3 dev-tools/python/verify_recent_memories.py \
  --server http://127.0.0.1:8366 \
  --companies C017 C018 C020 \
  --last 5
```

判据：item 数量 > 0 且文本长度 ≥ 50、不含错误标记（`internal error` / `quota exhausted` 等）→ 有效。

---

## 九、经验总结

1. 模型选择：**非推理模型**（避免内容为空），检测时验证 `content` 非空
2. 额度管理：批量写入前检测 API；注意每个模型额度独立
3. 环境变量：重启后端必须 `source .env`，否则用旧配置
4. embedding 批量：DashScope 上限 10，分批写入
5. 源码修改：改插件模块后必须 `mvn install` + 重启，并注意 Spotless 格式
6. HTTP 200 不等于成功：结合后端日志（`MemoryItem completed`）判断真实落库

---

## 十、QA 倍测链路（问答检测与评测）

问答检测与评测分两个脚本（详见 `docs/qa-evaluation-guide.md`）：

**1. 检索 + 回答生成（generate_qa_answers.py）**

```bash
python3 dev-tools/python/generate_qa_answers.py \
  --server http://127.0.0.1:8366 \
  --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
  --companies C017 \
  --id-prefix benchmark-v107 \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model qwen3.7-max-preview \
  --llm-api-key sk-xxx
```

- 输出：`benchmark-results/qa-answers/<时间戳>/results.json`（每题的检索响应 + 上下文 + LLM 回答）

**2. 评测（evaluate_qa_results.py）**

```bash
python3 dev-tools/python/evaluate_qa_results.py \
  benchmark-results/qa-answers/<时间戳>/results.json \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model qwen3.7-max-preview \
  --llm-api-key sk-xxx \
  --top-k 5
```

- 检索：Hit@5 + MRR（字符串匹配；`--llm-hit` 时未命中再用 LLM 判定，解决中英文语义匹配）
- 回答：JUDGE_PROMPT 打分 0/1/2
- 输出：`benchmark-results/qa-evaluation/<时间戳>/summary.json` + `summary.csv`

> 注意：评测的 LLM 裁判也消耗 DashScope 额度，用与写入相同的 `qwen3.7-max-preview`（有额度）。

### 中英文匹配设计（已实现）

- `normalize_answer()`：标准答案展开为多个变体（完整串、分隔符前缀、含数字片段）
- `_to_english_variants()`：中文金额/日期转英文形态（"2100万元"→"21,000,000"/"21 million"，"2025年12月"→"December 2025"/"2025-12"），中文数字金额（"三千五百万"→"35,000,000"）
- 纯数字短片段（<4）不作为变体，避免长编号中的 `01` 类误命中
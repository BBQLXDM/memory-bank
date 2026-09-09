# 记忆写入工作记录

> 本文档记录 benchmark_v1_0_7 各公司的记忆写入工作：脚本、流程、遇到的问题与解决方案。
> 与 `benchmark_memory_generation_summary.md`（规划总结）互补，本文聚焦**实际执行过程与踩坑记录**。
> LLM API / 额度 / 向量化相关问题见 `docs/memory-write-llm-issues-guide.md`。

- 服务地址：`http://127.0.0.1:8366`
- 写入对象：benchmark_v1_0_7 中选定的公司（全量支持 C001-C020）
- 命名空间：`id-prefix=benchmark-v107`（`userId=benchmark-v107-C017`，`agentId=benchmark-v107-C017-agent`）

---

## 公司 ID 与文件对应关系

`company_id`（C001-C020）来自 `standard/review_manifest.jsonl` 的 `company_id` / `company_name` 字段，与 `standard/quick` 下的文件一一对应：

| ID | 公司名 |
|---|---|
| C001 | 中路城建工程有限公司 |
| C002 | 佳诚优品商贸有限公司 |
| C003 | 华信精密机械有限公司 |
| C004 | 华泰重型装备制造有限公司 |
| C005 | 华芯半导体科技有限公司 |
| C006 | 华通路桥建设有限公司 |
| C007 | 恒信达工贸有限公司 |
| C008 | 恒泰建材有限公司 |
| C009 | 恒盛重型装备制造有限公司 |
| C010 | 恒锐精密机械制造有限公司 |
| C011 | 星驰新能源科技有限公司 |
| C012 | 智联芯科微电子有限公司 |
| C013 | 汇鑫源商贸有限公司 |
| C014 | 盛泰新型建材有限公司 |
| C015 | 科锐科创技术有限公司 |
| C016 | 绿能新源装备有限公司 |
| C017 | 联科绿筑新型建材有限公司 |
| C018 | 鑫源精密机械制造有限公司 |
| C019 | 鑫科精密零部件制造有限公司 |
| C020 | 锐科航空装备股份有限公司 |

规则：
- `userId = benchmark-v107-{company_id}`
- `agentId = benchmark-v107-{company_id}-agent`
- `sourceClient = benchmark-v107-{company_id}-{session_id}`
- `projectId = quick-6c4883-{company_id}`（快速集目录 + hash 前 6 位）

`dev-tools/python/generate_benchmark_memories.py` 和 `generate_qa_answers.py` 的 `COMPANIES` 列表已包含全部 20 家。

---

## 一、写入流程

### 写入方式（messages 模式）

每家公司、每个 session 按以下步骤写入：

1. 按 `started_at` 排序 session，逐 session 处理
2. 对 session 内每个 turn 调用 `/open/v1/memory/sync/add-message`（角色 USER/ASSISTANT 交替）
3. 全部 turn 入队后调用 `/open/v1/memory/sync/commit` 触发抽取
4. commit 成功后写入 `seen` 目录（`memory-generation-seen/<公司>/<session>.response.json`）作为跳过记录

### 主要脚本

| 脚本 | 用途 |
|---|---|
| `dev-tools/python/generate_benchmark_memories.py` | 全量写入主脚本（带跳过逻辑、失败记录） |
| `dev-tools/python/write_one_session.py` | 单 session 直写（绕过跳过逻辑，逐条打印状态） |

### 全量写入命令

```bash
cd /home/zzx/py/Memind-Local-Dev

python3 dev-tools/python/generate_benchmark_memories.py \
  --server http://127.0.0.1:8366 \
  --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
  --companies C017 C018 C020
```

### 输出目录

| 目录 | 内容 |
|---|---|
| `benchmark-results/memory-generation-python/<公司>/` | 每个 session 的 request 记录 |
| `benchmark-results/memory-generation-seen/<公司>/` | 每个 session 的结果记录（success / error），即**跳过与重试依据** |

---

## 二、跳过与重试机制

### 跳过条件（两个，任一满足即跳过）

1. **seen 记录匹配**：`seen` 目录存在 status=success 且 `sessionId`、`projectId`、`sourceClient`、`userId`、`agentId` 全部匹配的记录
2. **服务器已有数据**：调用 `/memory/items/query` 按 `sourceClient` 查询到 items

### 重试机制

- 失败的 session 在 seen 里是 error 记录（不是 success），服务器也查不到数据
- 两个跳过条件都不满足 → **重跑同一命令时自动重新执行失败 session**
- 因此全量跑完后，直接重跑一遍命令即可补齐失败部分，无需额外脚本

---

## 三、遇到的问题与解决方案

### 问题 1：commit 长时间卡住（600 秒超时）

**现象**：commit 请求挂起，最终 `500 internal_error / Timeout on blocking read for 600000000000 NANOSECONDS`。

**原因（两个叠加）**：

1. **Insight 抽取未关闭**：每次 commit 后 Insight 还要跑一轮 LLM，拖慢整个链路
2. **模型选错**：`deepseek-v4-flash-0731` 是**推理模型**，返回的 `content` 为空、只有 `reasoning_content`，结构化抽取无法正常完成，单次调用极慢

**解决**：

```bash
# 关闭 Insight（写库配置，生效后 commit 不再跑 Insight）
bash /home/zzx/py/Memind-Local-Dev/tools/speedup-memory.sh off
```

```bash
# 换模型：deepseek-v4-flash-0731 -> qwen3.7-flash
# 修改 .env
OPENAI_CHAT_MODEL=qwen3.7-flash
```

**效果**：commit 从 600 秒超时降到 **约 58 秒** 完成（见 qa 测试验证）。

**教训**：给 memory 抽取选模型时，**不要选带推理链（reasoning）的模型**，结构化抽取任务要非推理模型（flash / turbo 类）。

### 问题 2：add-message 返回 400 malformed_json

**现象**：`write_one_session.py` 的 add-message 全部 400，`{"error":{"code":"malformed_json",...}}`。

**原因**：后端的 `Message.timestamp` 是 `java.time.Instant`，Jackson 要求**带时区后缀**：

- 无时区字符串 `"2026-01-01T09:00:00"` → 解析失败 → `malformed_json`
- 正确格式：`"2026-01-01T09:00:00Z"`（或带 `+08:00`）

**修复**：脚本统一用时间规范化函数，把无时区时间补成 UTC+`Z`。

### 问题 3：timestamp 缺失时发送空字符串

**现象**：主脚本 `parse_instant()` 在时间缺失时返回 `""`，若某 turn 无 timestamp 就会发送 `"timestamp": ""`，同样触发 `malformed_json`。

**原因**：空字符串无法解析为 `Instant`。

**修复**：缺失时间统一返回 `"1970-01-01T00:00:00Z"`（`Instant.EPOCH`，与 Java SDK 行为一致）。

```python
def parse_instant(value):
    if not value:
        return "1970-01-01T00:00:00Z"  # 与 Java Instant.EPOCH 一致
    ...
```

### 问题 4：后端进程被暂停，脚本卡住无响应

**现象**：脚本卡在某个 commit 上，端口 8366 不通；查看进程发现后端是 `T`（stopped）状态。

**原因**：后端终端被 `Ctrl+Z` 暂停，进程挂起但还在占用端口。

**处理**：

```bash
kill -CONT <pid>        # 恢复进程
# 或直接重启后端
mvn -pl memind-server spring-boot:run
```

### 问题 5：测试标识与正式标识不匹配导致不跳过

**现象**：之前用 `test0725` 前缀测试，seen 里是 `test0725-C017-S001`；正式跑 `benchmark-v107` 时该 session 不跳过（sourceClient 不匹配）。

**说明**：这是**正常行为**——跳过逻辑要求命名空间一致。`test0725` 的测试记录不影响 `benchmark-v107` 正式写入。

### 问题 6：.env 中 REQUEST_TIMEOUT 重复

**现象**：`.env` 里出现两行 `MEMIND_REQUEST_TIMEOUT_SECONDS`（300 和 600），`source .env` 时后一行覆盖前一行。

**处理**：保留需要的值，删除多余行，避免配置不确定。

### 问题 7：embedding 批量超限（batch size > 10）

**现象**：commit 返回 `500 Retries exhausted: 3/3`，后端日志报 `batch size is invalid, it should not be larger than 10`。

**原因**：`FileSimpleVectorStore.doAdd()` 一次性批量 embedding 全部 documents，超过 10 条时被 DashScope `text-embedding-v4` 拒绝。

**修复**：改为分批（每批 ≤ 10）。详见 `docs/memory-write-llm-issues-guide.md` 第五节。

### 问题 8：修改源码后必须重新 `mvn install` 到本地仓库

**现象**：改了插件模块代码重启后端，问题依旧。

**原因**：后端通过 `~/.m2` 的 jar 依赖插件，只改源码不重新 install 不生效。

**处理**：

```bash
mvn -pl memind-plugins/memind-plugin-ai-spring-ai install -DskipTests -q
```

注意 Spotless 格式检查，不过直接 `mvn -pl memind-plugins/memind-plugin-ai-spring-ai spotless:apply`。

---

## 四、假成功清理（purge_fake_success.py）

LLM 额度耗尽时，commit 可能返回 `SUCCESS` 但 `itemIds=[]`（抽取失败），seen 记录为 success 但服务器无 item——称为**假成功**。这类记录会骗过跳过逻辑，导致无法自动补齐。

`dev-tools/python/purge_fake_success.py`：扫描 seen success 记录，逐个调 `/memory/items/query` 验证服务器是否有 item，无 item 的移为 `.bak.json`（或删除）。

```bash
python3 dev-tools/python/purge_fake_success.py --dry-run --companies C015 C016 C018 C019 C020  # 先看
python3 dev-tools/python/purge_fake_success.py --companies C015 C016 C018 C019 C020            # 实际清理
```

清理后重跑主脚本即可重新写入。验证用 `dev-tools/python/verify_company.py`：

```bash
python3 dev-tools/python/verify_company.py --server http://127.0.0.1:8366 --companies C017
```

输出 ✅ 有效 / ⚠ 部分问题 / ❌ 无效（假成功）三类统计。

---

## 五、当前状态（截至 2026-08-14）

### 第一轮（C017 / C018 / C020）

| 公司 | session 总数 | 状态 |
|---|---|---|
| C017 | 76 | ✅ 全部有效（68 条 seen success 验证通过 + 服务器已有数据跳过） |
| C018 | 77 | 已写入，部分假成功（额度耗尽期间）待第二轮补齐 |
| C020 | 76 | 已写入，部分假成功待补齐 |

### 第二轮（C015 / C016 / C018 / C019 / C020）

2026-08-14 起写入 5 家公司（不含 C017），C018/C020 同时补齐假成功。C015/C016/C019 为全新写入。

状态：进行中（C015 已完成 S002/S003 等）。

### 关键时间线

- 08-13 晚：第一轮 C017/C018/C020 写入，C017 大部分成功
- 08-14 09:00 前后：`qwen3.7-flash` 额度耗尽 → C018/C020 出现大量假成功
- 08-14 10:00-10:30：换 `qwen3.7-max-preview`、修复 embedding 分批、`mvn install` 新 jar
- 08-14 10:40：C017 补齐完成并验证 68+ 个 session 全部有效
- 08-14 10:50 起：第二轮 5 家公司写入 + A/B 测试

### 确认写入有效的方法

1. 后端日志：`MemoryItem completed: newItems=N`（N>0 表示抽取落库）
2. `verify_company.py` / `verify_recent_memories.py` 查服务器 items
3. 检索验证：`/memory/retrieve` 按问题检索看是否命中正确记忆

---

## 六、经验总结

1. **JSON 格式是最大坑**：`role` 大小写、`timestamp` 时区、缺失值处理，任何一个不对都会 `malformed_json`。详见 `docs/memory-api-format.md`
2. **模型选择影响巨大**：推理模型不适合结构化抽取，速度差 10 倍以上
3. **Insight 默认开启**：批量写入前必须关闭，否则每个 commit 都拖慢
4. **进程状态要盯**：后端被暂停时脚本会无提示卡住，先查进程状态再排查业务
5. **seen 目录是断点续跑的关键**：保持 seen 记录准确，重跑即可自动补差
6. **HTTP 200 不等于写入成功**：commit 返回 SUCCESS 但 `itemIds=[]` 是假成功，要用 items/query 或后端日志验证
7. **额度耗尽产生假成功**：`check_llm_api()` 在每个 session 写入前检测，失效自动停止（`exit(2)`）

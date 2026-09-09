# 协作排查清单

## 目标

在服务器环境中，和其他人一起定位问题到底出在写入、检索、生成、存储还是评测口径。

## 排查原则

- 先固定版本，再排查问题
- 所有参与者使用同一组输入样本和同一组命令
- 每次只改一个变量，避免同时改多处导致无法归因
- 先看事实数据，再看日志，再看代码

## 推荐排查顺序

### 1. 先确认服务健康

```bash
ss -ltnp | rg 8366
curl -s http://localhost:8366/open/v1/memory/retrieve \
  -H 'Content-Type: application/json' \
  -d '{
    "userId":"benchmark-v107-C019-isolated",
    "agentId":"benchmark-v107-C019-isolated-agent",
    "query":"资金往来是否全部通过工行账户结算？",
    "strategy":"SIMPLE",
    "trace":false
  }' | jq
```

### 2. 再看是否存在 SQLite 锁

重点搜索日志关键字：

- `SQLITE_BUSY`
- `database is locked`
- `TimeoutException`
- `Retrieval failed, returning degraded result`

示例：

```bash
rg -n "SQLITE_BUSY|database is locked|TimeoutException|Retrieval failed, returning degraded result" <日志文件>
```

### 3. 再验证写入是否正确

选择一条具体问题，查看原始记忆是否真的保存了关键事实。

### 4. 再验证检索是否正确

对同一条样本，直接用关键事实做检索，确认是否能命中。

### 5. 再验证生成是否正确

确认模型拿到正确证据后，是否还能生成正确答案。

## 分工建议

### A. 环境负责人

负责：

- 部署
- 启动
- 端口
- 依赖
- 日志收集

### B. 数据负责人

负责：

- 挑选样本
- 核对原始记忆
- 核对标准答案
- 确认样本是否适合复现

### C. 检索负责人

负责：

- 验证检索结果
- 看 Top-K 排序
- 看是否命中关键事实
- 看是否存在召回不足

### D. 生成负责人

负责：

- 看模型输出是否正确
- 看是否偏保守
- 看是否存在答非所问

## 最小复现模板

每次只保留以下信息：

- 公司/数据集名称
- QA 编号
- 问题文本
- 标准答案
- 检索结果 Top-5
- 模型最终回答
- 是否拒答
- 是否命中证据
- 是否出现异常日志

## 协作时最重要的输出

每个问题最后都要落成一句话：

- 是写入问题
- 是检索问题
- 是生成问题
- 是存储问题
- 还是评测口径问题

## 建议的会议节奏

1. 先部署并稳定运行
2. 再做单题复现
3. 再做小批量复现
4. 再做并发/压力测试
5. 最后再讨论优化方向

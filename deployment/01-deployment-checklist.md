# Ubuntu 服务器部署清单

## 目标

把当前可运行版本迁移到指定 Ubuntu 服务器，先建立一套稳定、可复现的运行环境，用作后续排查和协作基线。

## 部署前确认

- 确认服务器 IP、SSH 用户名、目标部署路径
- 确认服务器上有足够磁盘空间
- 确认对外网络可访问所需模型与依赖服务
- 确认本地与服务器使用的是同一 Git 提交或同一分支状态

## 需要的基础环境

- Java 21
- Maven 3.9+ 或项目兼容版本
- Git
- Bash
- 视情况安装 SDKMAN

## 建议的部署顺序

### 1. 创建工作目录

在服务器上先创建一个专用目录，例如：

```bash
mkdir -p <目标路径>/memind-server-work
```

### 2. 拉取代码

如果服务器可直连仓库：

```bash
git clone <仓库地址> <目标路径>/memind-server-work
```

如果已经有代码目录：

```bash
cd <目标路径>/memind-server-work
git fetch --all
git checkout <目标分支或提交>
```

### 3. 准备环境变量

把本地可用配置迁移到服务器的 `.env`，至少确认以下内容：

- 数据库配置
- OpenAI / 兼容模型配置
- Embedding 配置
- Rerank 配置
- 端口配置

### 4. 安装依赖并编译

```bash
cd <目标路径>/memind-server-work
mvn -pl memind-server -DskipTests compile
```

### 5. 启动后端

```bash
cd <目标路径>/memind-server-work
source "$HOME/.sdkman/bin/sdkman-init.sh"
sdk env
set -a
source .env
set +a
mvn -pl memind-server spring-boot:run
```

### 6. 验证端口

```bash
ss -ltnp | rg 8366
```

### 7. 验证检索接口

```bash
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

## 部署后验收

- 8366 端口监听正常
- `retrieve` 接口返回 `status=success`
- 选择 1~3 条典型问题，确认结果与本地一致
- 日志中无明显 `SQLITE_BUSY` 或大面积超时

## 失败时先检查

- `.env` 是否缺失
- 端口是否被占用
- 数据库文件权限是否正确
- 模型服务是否可访问
- 服务器上是否存在旧进程
- SQLite 是否出现锁冲突

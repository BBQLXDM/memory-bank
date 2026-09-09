# Memind 本地开发计划

## 工作名称

- 名称：Memind Local Development
- 本地目录：`/home/zzx/py/Memind-Local-Dev`
- 上游仓库：`https://github.com/openmemind/memind.git`
- 主分支：`main`

## 目标

在 WSL2 中以本地开发方式运行 Memind，分别启动 `memind-server` 和 `memind-ui`，便于阅读源码、断点调试、修改后端，以及使用前端热更新。Docker Compose 暂不作为主启动方式，但可用于后续对照验证。

## 已完成

- 已确认可以在 WSL2 中执行开发环境检查命令。
- 已将上游仓库克隆到本地开发目录。
- 已确认仓库处于 `main` 分支并跟踪 `origin/main`。
- 已将目录重命名为大写开头的 `Memind-Local-Dev`。
- 已确认根 `pom.xml` 要求 Java 21。
- 已确认当前 Node.js 20.20.1 满足 README 所述的 20.19+ 要求。

## 当前环境

| 工具 | 当前版本 | 状态 |
| --- | --- | --- |
| WSL2 | Linux x86_64 | 可用 |
| Git | 2.43.0 | 可用 |
| Java | OpenJDK 17.0.19 | 不满足 Java 21 要求 |
| Maven | 3.8.7 | 已安装，但当前使用 Java 17 |
| Node.js | 20.20.1 | 满足要求 |
| Corepack | 0.34.6 | 可用 |
| pnpm | 10.32.0 | 已安装，待实际安装依赖验证 |

## 环境管理约定

参考现有 Conda 的显式环境管理习惯，Java 使用 SDKMAN 管理：

- 保留系统默认 Java 17。
- 通过 SDKMAN 安装独立的 Java 21。
- 在项目中使用 `.sdkmanrc` 固定 JDK 21 的准确版本。
- 进入项目后手动执行 `sdk env`，暂不开启自动切换。
- 离开项目环境时可执行 `sdk env clear`。
- 不使用 `update-alternatives` 修改全局默认 Java。

## 后续计划

### 1. 检查仓库声明

- 检查是否存在 Maven Wrapper、`.nvmrc`、`.node-version` 或现有 SDKMAN 配置。
- 检查 `.env.example`、`application.yml`、UI 的 Vite 代理和数据存储路径。
- 以仓库实际声明为准确认 Maven、pnpm 和 Node.js 的使用方式。

### 2. 配置项目级 Java 21

- 安装 SDKMAN（如果尚未安装）。
- 查询并安装当前可用的 Temurin JDK 21。
- 创建项目级 `.sdkmanrc`。
- 执行 `sdk env` 后验证 `java -version` 和 `mvn -version` 均使用 Java 21。

### 3. 配置模型凭据

本地开发至少需要兼容 OpenAI 协议的 Chat 和 Embedding 模型配置：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_CHAT_MODEL`
- `OPENAI_EMBEDDING_MODEL`

密钥只放在被 Git 忽略的本地配置或当前终端环境中，不写入本说明文件，不提交到仓库。配置前先根据实际模型供应商核对 Base URL 和模型名称。

### 4. 启动后端

激活项目 Java 21 环境后，在仓库根目录运行：

```bash
mvn -pl memind-server -am spring-boot:run
```

启动后验证：

```bash
curl http://localhost:8366/open/v1/health
```

健康检查通过只表示服务已启动，不代表模型凭据已经通过真实调用验证。

### 5. 启动前端

另开终端并运行：

```bash
cd /home/zzx/py/Memind-Local-Dev/memind-ui
pnpm install
pnpm dev
```

默认访问地址为 `http://localhost:5173`，前端开发服务器将管理 API 请求代理到本地 `8366` 端口。

### 6. 最小功能验证

- 检查后端健康状态。
- 检查管理 UI 是否加载正常。
- 写入一条最小测试记忆。
- 等待并检查 extraction 结果。
- 检索该记忆，确认 Chat 和 Embedding 模型均可用。
- 检查后端日志和 UI 请求，无误后再进入日常开发。

## 安全与操作原则

- 先检查后安装，避免无必要的全局升级。
- 不删除系统 Java 17，不修改全局 Java 默认版本。
- 不在命令输出、文档或 Git 中暴露 API Key。
- 不主动执行 `docker compose down -v` 等删除持久数据的命令。
- 不运行完整 benchmark，除非单独准备数据集并确认模型费用。
- 遇到失败先检查日志、版本和网络，不盲目重复执行。

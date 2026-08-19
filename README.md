# Codex Dev Orchestrator

一个完全在本机运行的 AI 研发编排台。它把研发目标拆成有依赖关系的任务，让多个 Codex 执行单元分别承担规划、实现、独立评审和 QA，并在关键节点等待人工拍板。

它不调用 OpenAI API，也不读取或转发 API Key。执行端直接调用本机已经用 ChatGPT 登录的 Codex CLI，因此使用的是 Codex 订阅访问能力。

## 已有能力

- 中文 AI Kanban：待处理、可执行、执行中、等待确认、评审、阻塞、失败和完成；
- 多角色工作流：技术协调者、方案规划者、实现工程师、独立评审者和质量验证者；
- 父子任务、显式依赖、循环依赖检查和完成后的自动解锁；
- 协调 Agent 可提出最多 20 个子任务及其依赖；
- 每个任务使用独立 Git worktree 和 `agent/...` 本地分支；
- 下游任务从上游分支继续；有多个依赖时在自己的 worktree 中本地整合；
- Codex JSONL 事件、结构化结论、token 使用量、会话 ID 和交接说明持久化；
- 人工批准、拒绝、评审通过、要求修改以及同一 Codex 会话继续执行；
- SQLite 本地存储，服务重启后可恢复任务；
- 完成变更后自动创建本地提交，但绝不自动 push、合并或部署。

运行时仅使用 Python 3.9+ 标准库，没有第三方 Python 运行依赖。

## 运行条件

1. macOS 或其他具备 Python 3.9+、Git 和 Codex CLI 的本机环境；
2. 目标代码目录已经初始化为 Git 仓库，并至少有一个提交；
3. Codex CLI 已通过 ChatGPT 登录。

先做一次检查：

```bash
cd "/Users/wangxian/Documents/ChatGPT/AI学习/codex-dev-orchestrator"
python3 run.py doctor
```

成功输出应包含：

```json
{
  "ok": true,
  "auth_status": "Logged in using ChatGPT",
  "api_keys_forwarded": false
}
```

如果尚未登录，先在终端运行 `codex login`，选择 ChatGPT 登录。不要配置 `OPENAI_API_KEY`；编排器启动执行单元时也会主动移除相关 API 环境变量。

## 启动

最简单的方式是双击 `启动控制台.command`。

也可以在终端启动：

```bash
cd "/Users/wangxian/Documents/ChatGPT/AI学习/codex-dev-orchestrator"
python3 run.py serve --open
```

默认地址为 [http://127.0.0.1:8765](http://127.0.0.1:8765)。服务默认只监听本机回环地址。

## 第一次使用

1. 点击左侧“＋”，填写项目名称、Git 仓库绝对路径和基准分支；
2. 新建一个“技术协调者”任务，描述完整目标；
3. 勾选“允许拆分子任务”和“需要人工批准”；
4. 先保持手动调度，并勾选“立即启动”；
5. 协调 Agent 会只读分析仓库，提出实现、评审和 QA 子任务；
6. 在“等待确认”栏审核方案。批准后，下游第一个任务进入“可执行”；
7. 逐项启动或为项目开启自动调度；
8. 在任务详情中查看摘要、交接、对话、运行记录和完整 Diff；
9. 最终代码仍留在 `agent/...` 本地分支，由人决定如何合入。

推荐第一个验证目标：

> 为现有项目增加一个只读版本信息端点。请先分析代码并拆成实现、独立评审和 QA 三步；实现必须有自动化测试，不得新增依赖、对外通信、push、合并或部署。方案先等我批准。

## 两种调度方式

- 手动调度：任务依赖满足后进入“可执行”，由人点击“启动 Codex”。适合首次使用和高风险工程；
- 自动调度：项目或单个任务开启后，依赖满足即自动进入执行队列。人工审批和评审节点仍然生效。

执行器默认有 2 个并发工位。并发任务各自拥有独立 worktree，不会在同一个工作目录互相覆盖。

## 配置

通过环境变量调整：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ORCH_DATA_DIR` | 工程内 `.data` | SQLite、运行结果和 worktree 的位置 |
| `ORCH_HOST` | `127.0.0.1` | Web 服务监听地址 |
| `ORCH_PORT` | `8765` | Web 服务端口 |
| `ORCH_MAX_WORKERS` | `2` | 同时运行的 Codex 任务数 |
| `ORCH_CODEX_BINARY` | `codex` | Codex CLI 路径或命令名 |
| `ORCH_CODEX_MODEL` | 空 | 可选的 Codex 模型覆盖；空表示使用本机默认配置 |
| `ORCH_RUN_TIMEOUT_SECONDS` | `3600` | 单次执行超时，最低 60 秒 |

例如换端口：

```bash
ORCH_PORT=8877 python3 run.py serve
```

## 安全边界

编排器的安全模型是“本机控制面 + 隔离工作树 + 人工闸门”：

- 只接受本机已经通过 ChatGPT 登录的 Codex CLI；
- 不提供 OpenAI API 调用代码，也不向子进程传递常见 API Key；
- 规划和协调角色使用只读沙箱；
- 实现、评审和 QA 使用 `workspace-write`，但工作目录限定为该任务的独立 worktree；评审角色的提示明确禁止修改代码，允许写是为了让测试能够创建临时文件和缓存；
- Agent 提示明确禁止 push、merge、删分支、发布、部署和联系外部人员；
- Git 服务只创建 worktree、本地分支、本地提交，并可在下游 worktree 中整合依赖分支；
- 架构、安全策略、破坏性迁移、含糊产品决定和最终合并应保留人工确认；
- `.data` 可能包含代码副本、任务描述和运行输出，应按本地研发资料保护，不要提交到 Git。

这不是强隔离的远程多租户执行平台。不要把监听地址开放到不可信网络，也不要把不可信仓库与敏感本机凭据混在同一用户环境中运行。

## 测试

```bash
PYTHONPYCACHEPREFIX=/tmp/codex-orchestrator-pycache \
python3 -m unittest discover -s tests -v
```

测试使用模拟 Codex，不消耗订阅额度。真实 Codex 端到端验收需要从看板创建任务执行。

## 数据与清理

运行数据位于 `.data/`：

- `orchestrator.sqlite3`：项目、任务、依赖、消息、审批、事件和运行记录；
- `runs/`：每次 Codex 的结构化最终结果；
- `worktrees/`：任务隔离工作树。

停止服务不会删除这些数据；再次启动时，之前中断的运行会标记为失败，可在看板中重试。若要清理，先停止服务，并确认相应 worktree 和本地 Agent 分支不再需要。编排器当前刻意不提供“一键删除全部”功能。

更详细的内部结构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

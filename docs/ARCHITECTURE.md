# 架构说明

## 目标

这个工程不是一个“大包大揽的超级 Agent”，而是一个本地控制面：让多个强执行 Agent 在明确角色、任务依赖、隔离代码空间和人工决策点下协作。

```mermaid
flowchart LR
    H[人：目标与关键决策] --> B[AI Kanban 控制台]
    B --> S[调度器]
    S --> C[协调 / 规划 Agent]
    S --> I[实现 Agent]
    S --> R[独立评审 Agent]
    S --> Q[QA Agent]
    C --> D[(SQLite 任务图与消息)]
    I --> W1[独立 worktree / 分支]
    R --> W2[独立 worktree / 分支]
    Q --> W3[独立 worktree / 分支]
    C & I & R & Q --> CLI[ChatGPT 登录的 Codex CLI]
    I & R & Q --> G[本地 Git 提交与 Diff]
    G --> H
```

## 组件

| 组件 | 职责 |
| --- | --- |
| Web 控制台 | 项目接入、任务创建、状态看板、消息、审批、评审和 Diff 展示 |
| SQLite 数据库 | 保存项目、任务图、运行、事件、消息、会话、审批和交接信息 |
| 调度器 | 并发工位、领取任务、依赖解锁、自动调度、启动恢复和结果状态机 |
| 执行器注册表 | 按任务解析执行器、分别做可用性预检、把一个执行器的故障隔离在它自己的任务上 |
| 执行公共内核 | 进程监管、硬超时、事件抽取、环境变量剥离、提示词脱敏、结构化结果提取与校验 |
| Codex 执行适配器 | ChatGPT 登录预检、构造 `codex exec`、JSONL 事件、原生 `--output-schema` 输出、会话恢复 |
| Claude Code 执行适配器 | 构造 `claude -p`、按角色收紧工具白名单、stream-json 事件、在本地强制结果契约 |
| Git 服务 | 仓库校验、安全 ref、worktree、本地任务分支、依赖分支整合、Diff 和本地提交 |
| 结构化结果协议 | 约束 Agent 的结果、交接、测试、子任务提议、消息和下一状态 |
| MCP 桥接层 | 把受控的一小组工具通过 stdio 暴露给任意 MCP 客户端；无状态，只转发到本机 HTTP API |

## 一次典型功能的状态流

```mermaid
sequenceDiagram
    participant H as 人
    participant O as 编排器
    participant C as 协调 Agent
    participant I as 实现 Agent
    participant R as 评审 Agent
    participant Q as QA Agent

    H->>O: 提交目标，要求先审批方案
    O->>C: 只读分析仓库
    C-->>O: 实现 → 评审 → QA 任务图
    O-->>H: 等待确认
    H->>O: 批准方案
    O->>I: 创建 worktree 并执行
    I-->>O: 测试、摘要、本地提交、建议评审
    O-->>H: 展示 Diff，等待人工评审
    H->>O: 评审通过
    O->>R: 从实现分支创建下游 worktree
    R-->>O: 独立审查结论
    O->>Q: 从已评审代码创建下游 worktree
    Q-->>O: 回归与端到端验证结果
    O-->>H: 保留本地分支，等待最终合并决定
```

## 任务图与代码继承

每个任务可以有父任务和任意数量的前置依赖。只有全部依赖进入 `done`，任务才从 `blocked` 变为 `ready`。

执行时：

1. 没有代码依赖的任务从项目基准分支创建 worktree；
2. 有一个已完成依赖时，从该依赖的任务分支创建；
3. 有多个依赖时，以第一个分支为基线，在新 worktree 中依次本地合并其余依赖分支；
4. 冲突会让任务失败并保留证据，不会修改主工作目录；
5. Agent 结束后，编排器把变更提交到当前任务分支；
6. 编排器不 push，也不把任务分支合入项目基准分支。

协调 Agent 产生的子任务默认依赖协调任务本身，所以必须先完成方案审批，子任务才会释放。子任务之间还能按提议标题建立额外依赖。

## Agent 通信

通信不是多个终端自由聊天，而是经过控制面的持久消息：

- 人可以在任务详情中给 Agent 留消息；
- Agent 可向同项目、且已出现在上下文里的任务 ID 发送交接消息；
- 消息随下一次执行或会话恢复进入任务上下文；
- 每个任务另有 `summary` 和 `handoff`，供人和下游任务核对；
- Codex 会话 ID 保存到任务，退回修改时在同一会话和 worktree 中继续。

这种机制牺牲了无约束群聊的自由度，换来可审计、可重放和明确的任务所有权。

## 监察与对抗

闸门只有在人拿到足够信息、且被审查的一方无法自行消除证据时才有意义。三条机制服务于此：

**评审者不得修改产品代码。** 评审角色需要可写工作树来跑测试，但一旦它改动了已跟踪文件，编排器拒绝提交、记录 `review.contract_violation` 事件并让任务失败，改动留在 worktree 里作为证据。理由是这条边界一旦松开会双重失效：本该写成结论的问题被就地改掉，人看到的 diff 里实现与评审混在一起，无法分辨。QA 不受此限，它的职责本就包含补测试。

**独立评审换执行器。** 协调者拆出的子任务默认继承父任务的执行器，评审角色例外——只要还有另一个可用执行器，评审就交给它。同一个模型评审自己的产出是整条闸门链里最弱的一环。单执行器部署时自动回退，`ORCH_CROSS_REVIEW=0` 可关闭。

**自报检查进入账本并呈现在闸门上。** Agent 结果里的 `tests` 写入任务的 `evidence` 字段，在任务详情里单列一节。这些是**声明而非证明**——编排器不会替 Agent 重跑测试。把它们存下来的价值在于可审计：一个声称完成实现却报告空检查列表的任务，那个空列表本身就是信号，界面会显著标出。

尚未做到的：编排器不独立验证测试是否真的执行过，也不强制"完成前必须经过评审或 QA"。前者需要在受控环境里重跑并比对，后者需要工作流模板约束协调者的拆解。这两条目前依赖人工判断。

## 人工闸门

有三类停点：

1. 任务预设 `requires_approval`：Agent 完成后等待批准，批准才标记完成；
2. Agent 主动返回 `needs_approval`：遇到架构、安全、迁移或产品歧义时提出具体问题；
3. 实现返回 `recommended_stage=review`：进入人工代码评审，可通过或带说明退回。

拒绝或要求修改不会创建新任务，而是给原任务追加人工消息并恢复同一个 Codex 会话。这样保留上下文、Diff 和责任边界。

## Codex 执行契约

首次执行大致等价于：

```text
codex exec --json --sandbox <read-only|workspace-write> \
  --cd <task-worktree> --output-schema <schema> \
  -o <run-final.json> <prompt>
```

编排器记录 JSONL 事件中的会话 ID和 token 使用量，并读取最终结构化 JSON。提示词对所有角色施加共同边界，再添加角色职责和任务上下文。

协调/规划使用 `read-only`。实现/评审/QA 使用 `workspace-write`；评审需要可写测试临时文件，但被限定在独立 worktree，且角色契约禁止编辑产品代码。

Claude Code 没有等价的沙箱开关，因此同一条边界由工具白名单实现：只读角色拿到的白名单里根本没有编辑类工具。见"执行器"一节。

## 执行器

执行器是可替换的，工程保证不是。任何 CLI 接进来都拿到同一套东西：

```mermaid
flowchart TB
    T[任务] --> R[执行器注册表]
    R -->|任务指定 → 项目默认 → 部署默认| E[选中的执行器]
    subgraph K[编排器持有的保证]
        W[独立 worktree 作为 cwd]
        P[按角色收紧的写权限]
        V[剥离 API Key 的环境]
        C[结构化结果契约]
        O[硬超时与事件账本]
    end
    E --> K
    K --> X1[Codex CLI]
    K --> X2[Claude Code CLI]
```

差别只在于每个 CLI 各自能原生提供多少，以及缺的部分由谁补。

结果契约两边都是原生的：Codex 用 `--output-schema` 加 `-o`，Claude Code 用 `--json-schema`，并把校验过的对象放在最终 `result` 事件的 `structured_output` 字段里。执行器优先读它，只有该字段缺失时才退回到从回答文本里提取 JSON——那条降级路径留给行为不同的 CLI 版本，不是常规路径。无论走哪条，缺必填字段的运行一律失败，不会有自然语言结论直接驱动任务状态。

只读隔离两边不同。Codex 有 `--sandbox read-only`；Claude Code 没有等价开关，所以边界改用工具白名单表达：协调和规划角色拿到的白名单里根本没有编辑类工具。实测中，一个被明确要求写文件的规划任务，写入被 CLI 按策略拒绝，`permission_denials` 进入运行记录，worktree 保持干净。

还有一处 Claude Code 特有的收口：任务进程不继承操作者配置的 MCP 服务（`--mcp-config '{"mcpServers":{}}' --strict-mcp-config`）。否则那是一片工具白名单看不见的工具面，包括网络出口。

新增执行器要实现的只有三件事：`preflight()`、`build_command()` 和一个把该 CLI 的 stdout 变成事件的解析器。进程监管、超时、环境剥离、提示词脱敏都在 `agent_base.py` 里共享。

`preflight()` 必须真的检查登录，而不只是检查文件存在：`codex login status` 要求 ChatGPT 登录，`claude auth status` 返回 JSON 并校验 `loggedIn`。订阅访问由剥离 API Key 环境变量强制，预检只是让问题在任务开跑前就暴露。

一个执行器不可用时，注册表仍然是就绪的，只要还有别的执行器可用。指定了缺失执行器的任务会带着原因单独失败，自动调度不会因此停摆。

## MCP 边界

`orchestrator/mcp_server.py` 让外部对话式客户端（Claude Code、Codex CLI、LobeHub 等）能够驱动这个内核，而不需要它们理解 worktree、依赖图或审批状态机。

```mermaid
flowchart LR
    C[任意 MCP 客户端] -->|stdio JSON-RPC| M[MCP 桥接进程]
    M -->|回环 HTTP，白名单路径| S[serve 进程]
    S --> D[(SQLite 事实源)]
    S --> W[Git worktree 与分支]
    S --> X[Codex 执行单元]
    S --> B[本地看板]
    B --> H[人：审批、评审、合并]
    M -.深链.-> H
```

三条不变量：

1. **桥接进程无状态。** 它不打开数据库、不启动调度器、不启动 Codex，因此不存在第二个调度器和 worktree 竞争。`serve` 仍然是唯一写入方。
2. **接口白名单。** 桥接层持有一份硬编码的 `(方法, 路径)` 白名单，并且拒绝非回环地址。HTTP API 比白名单宽，这是刻意的。
3. **闸门不进工具面。** 审批、拒绝、评审决定、项目登记、人工消息一律不是工具。需要人决定时，工具返回 `http://127.0.0.1:8765/#/task/<id>` 深链，人在看板上决定。

`plan_workflow` 会强制把协调者任务的 `allow_delegation` 和 `requires_approval` 设为真，调用方无法关闭：模型可以提出任务图，但任务图在人批准之前不会解锁。

MCP 是拉取式的，桥接层没有向客户端主动推送的通道。因此本地看板不是可以删掉的冗余——它仍然是实时监看 Diff 和执行审批的地方；MCP 客户端是对话入口，不是监控面板。

## 失败处理

- Codex 非零退出、结构化结果缺失、Git 冲突或超时：任务进入 `failed`；
- 依赖未完成：任务保持 `blocked`；
- Agent 报告外部阻塞：任务进入 `blocked` 并显示原因；
- 服务在任务运行时退出：下次启动把该任务和运行标记为失败；
- 失败或阻塞任务可以从看板重试；已有 worktree 和会话会被复用。

## 当前刻意没有做的事

- 不自动 push、开 PR、合并、发布或部署；
- 不接任何模型 API，也不管理 API Key；
- 不给任何执行器 `danger-full-access` 或 `--dangerously-skip-permissions`；
- 不接 GitHub、Linear、Slack 等云服务；
- 不通过 MCP 暴露审批、评审、合并、部署和项目登记；
- 不修改或重新分发外部 Agent 平台的源码，只通过 MCP/CLI 集成；
- 不提供远程多用户鉴权；
- 不自动删除 worktree 和 Agent 分支；
- 不把自然语言 Agent 聊天当成可靠状态，所有关键状态都进入数据库。

这些边界让第一版更适合作为单机研发团队控制台。后续可以在不破坏安全模型的前提下增加 PR/CI 触发器和更多执行端。

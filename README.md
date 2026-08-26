# AI Delivery Governance V2.3

这条分支把原来的“自研多 Agent 看板”重构为一层薄而确定的 AI 交付治理能力。它不再复制成熟产品已经做好的 UI、任务、worktree、会话和 CI，而是把真正缺失的部分做成可复用协议：

- 开发前由“意图确认员”固化结果、开发执行器、产品运行时、技术选型、验收样例和风险边界；
- 在意图确认前强制完成社区实践、近期高质量学术研究、开源框架和官方一手资料四路调研，并由全新只读“调研质检员”检查来源、方法、时效、许可证、安全、维护和适配性；
- 当 2–3 条路径都可行且存在文档无法消除的真实未知时，由人决定是否开启有界技术赛马，冻结统一数据、测试、评估维度、时间/成本预算、停止条件和融合权限；
- 赛道使用互不可见的独立上下文/worktree；全新只读“统一赛马评测员”只建议保留、融合明确优点或全部淘汰，最终路线由人再次签署；
- 由全新只读上下文的“意图检查员”对照原始对话、调研、意图简报和拟定契约，阻断目标偷换、遗漏、供应商混淆和未确认默认；
- 把 Spec Kit 或人类确认过的需求编译成不可静默漂移的工作契约；
- 用 Question Ledger 记录决策 ID、影响、验收关联、后果、默认方案、负责人、可逆性和答案；
- 根据风险决定保持单一 Codex 上下文，还是开启哪些独立监察通道；
- 为每个监察通道生成最小、只读、彼此隔离的上下文；
- 过滤低置信、不可复现、非本次引入和重复问题；
- 把有效问题汇总成一次返修包，交回原开发上下文；
- 用环境 capsule 在开工前核对 cwd、真实 Diff 根、权限、PATH、端口和锁；
- 用控制器签名的原子阶段账本恢复中断，并用真实产物不变量决定是否推进；
- 用中文短名、实时进展、当前困难、依赖、心跳和来源任务统一观察 Agent，且把“执行状态”和“交付裁决”分开；
- 默认启用领域语义、状态/信任边界、测试预言机三条正交监察，再按风险扩展；
- 全量复验后由一个看不到旧对话和旧发现的全新只读 verifier 盲审 must-kill 反例；
- 把人工确认的 Bad Case 编译成隐藏回归案例，新/变更 Inspector 先影子运行并用 Good/Bad Case 校准；
- 从签名账本生成 Review Packet，让人看到证据哈希、阻塞点、影子发现和仍需作出的决定；
- 第二次仍不收敛、存在争议或即将产生外部/不可逆效果时交给人；
- 通过无状态 MCP 同时服务 LobeHub、Kandev、Symphony 和 Codex。

它自身不调用模型 API，不接受模型 API Key，也不持有任务、审批、分支或 Agent 会话。这不等于禁止被交付产品使用经人确认的模型 API：产品只能从指定环境变量读取密钥，密钥不得进入治理输入、Prompt、日志或 Git。V2.3 的窄运行账本由外部可信控制器持有，只保存阶段产物摘要、签名事件、Agent 进展和恢复指标；控制令牌绝不交给 Agent。

## 组合架构

| 成熟组件 | 在系统中的职责 |
| --- | --- |
| LobeHub | 人类入口、项目与任务、对话、审批、记忆和日报 |
| Kandev | 研发工作台、Agent CLI、worktree、Diff、代码审查和交接 |
| OpenAI Symphony | 长时间工单调度、重试、协调、恢复和 `WORKFLOW.md` 语义 |
| GitHub Spec Kit | 项目宪法、需求澄清、规格、技术计划和任务分解 |
| GitHub Actions / gh-aw | 确定性 CI、仓库事件、权限隔离和安全输出 |
| Codex CLI/app-server | 使用本机已登录订阅的主要执行端 |
| 本仓库 | 契约、风险路由、上下文隔离协议、证据裁决和集成边界 |

默认执行路径是：

```text
LobeHub 接收目标和人类决定
  → 技术调研员执行社区 / 近期学术 / 开源 / 官方四路搜索
  → 全新只读调研质检员检查证据质量和候选适配性
  → 意图确认员整理可见结果、技术选择和验收样例
  → 全新只读意图检查员对照原始对话、调研、简报和拟定契约独立反证
  → 本治理层编译 Question Ledger、契约和风险计划
  → 人类答案形成 delta；如意图字段改变则重新独立检查
  → 人确认最终展示内容和单路/赛马策略，可信控制器签名绑定 research / strategy / intent / inspection / contract / plan / 同一外部任务
  → 环境 capsule 证明 cwd、权限、工具、端口、锁和真实 Diff 根
  → 如启用赛马：2–3 条隔离路径并行 → 统一盲评 → 人签署保留/融合/全部淘汰
  → Kandev 或 Symphony 驱动本地 Codex
  → 确定性 CI 先运行
  → 三条正交监察 + 风险扩展通道独立反证
  → 新/变更 Inspector 先以 shadow 模式校准
  → 本治理层跨通道合并根因并生成一次返修包
  → 全量复验 + 全新上下文盲审 must-kill 反例
  → 人决定最终合并或发布
```

## 快速体验治理内核

技术调研门禁先于意图确认运行：

```bash
python3 run.py governance research --input technology-research-source.json \
  --output /tmp/technology-research.json
```

把通过质检的 `technology_research` 和人的 `technology_strategy` 放入意图输入后运行：

```bash
python3 run.py governance intent --input examples/legal-billing-intent-source.json \
  --output /tmp/legal-billing-intent.json
```

仓库中的法律计费文件刻意保留为“研究前模板”，不会伪造真实社区/学术来源；直接运行会得到 `needs_clarification`。测试会注入冻结研究 fixture 来验证协议，真实项目必须把 `governance research` 的通过产物和人的策略选择写入副本后再编译。

`governance inspect-intent` 再接收该简报、原始调研、拟定契约、逐项 coverage 和全新只读检查员的 findings。只有 `PASS` 且无阻断项才能绑入契约。

将通过的 brief/inspection 放入 `intent_alignment` 后编译工作契约：

```bash
python3 run.py governance compile --input aligned-contract-source.json \
  --output /tmp/legal-billing-contract.json
```

选择检测通道：

```bash
python3 run.py governance route --input /tmp/legal-billing-contract.json \
  --output /tmp/legal-billing-verification.json
```

对真实目标仓库做只读环境预检（输入包含 `repo`、已编译 `contract`）：

```bash
python3 run.py governance preflight --input contract-and-repo.json \
  --output /tmp/environment-capsule.json
```

把契约和计划编译成 Kandev/Codex 可消费、但尚未执行任何外部写操作的交接清单：

```bash
python3 run.py governance handoff --input contract-and-plan.json \
  --output /tmp/legal-billing-handoff.json
```

生成一个可提交到目标仓库的治理包：

```bash
python3 run.py governance init \
  --target /absolute/path/to/a/git/repository \
  --input examples/legal-billing-contract-source.json
```

该命令只创建 `.ai-delivery/`，且拒绝覆盖已有文件。V2.3 额外生成 `technology-research.json`、`intent-brief.json`、`intent-inspection.json`、`runtime-protocol.json`、`bad-case-registry.json` 和 `calibration-policy.json`，记录开发前调研、技术策略、意图证据、赛马、问题恢复、原子检查点、实时监控、遥测、盲审和学习闭环协议。

## 接入 LobeHub 或 Kandev

启动无状态治理 MCP：

```bash
python3 run.py governance-mcp
```

MCP 暴露十二个纯计算工具：

| 工具 | 作用 |
| --- | --- |
| `compile_technology_research` | 编译四路调研、框架适配矩阵、多条技术路径和独立质检结果，但不联网或替人选路 |
| `compile_intent_brief` | 编译意图确认简报和人类问题，但不代替人确认 |
| `compile_intent_inspection` | 编译全新只读检查员的 coverage、反例和 PASS/BLOCKED 结果 |
| `compile_work_contract` | 契约编译、追问缺口、风险信号 |
| `propose_contract_resolution` | 把人类/专家答案编译成待可信控制器签名的契约 delta |
| `compile_bad_case_registry` | 把人工确认的 Good/Bad Case 编译成版本化隐藏回归表 |
| `calibrate_inspector` | 计算召回、误报、人机一致和独立贡献，决定 shadow/blocking 资格 |
| `plan_delivery` | 动态选择确定性和语义监察通道 |
| `build_inspector_contexts` | 生成最小只读隔离上下文 |
| `build_delivery_handoff` | 生成 owner、监察任务、Profile 边界、顺序和人类闸门的 Kandev/Codex 清单 |
| `adjudicate_delivery` | 去重、过滤、阻塞判断和一次返修包 |
| `get_integration_blueprint` | 返回各成熟组件的所有权边界 |

这组工具不能运行 Agent、修改任务、批准、写代码、push、合并或部署。契约 delta 的最终 HMAC attestation、运行账本、实时快照和 Review Packet 只在可信控制器运行库中完成，不进入 MCP。LobeHub 继续持有人类界面，Kandev 继续持有研发任务和 worktree。

Kandev 0.91.0 可安装仓库内的 [原生智能体监控插件](integrations/kandev-agent-observer/README.md)。它在 Kandev 侧边栏、任务卡、任务详情顶栏和任务面板显示中文 Agent 职责、进展、困难、依赖、下一步、心跳、执行状态与交付评审，不建立第二套任务数据或看板。

项目级 Codex Skill 位于 `.agents/skills/ai-delivery-governance/`，把契约、验证和证据裁决规则带入兼容 Agent。

详细说明见：

- [V2.3 版本说明](CHANGELOG.md)
- [V2.3 技术调研与有界赛马](docs/V2_3_TECH_RESEARCH_RACE.md)
- [V2.2 意图对齐实现](docs/V2_2_INTENT_ALIGNMENT.md)
- [新架构](docs/ARCHITECTURE.md)
- [V2.1 融合实现与德国计费回放](docs/V2_IMPLEMENTATION.md)
- [产品与质量需求](docs/AI_NATIVE_DELIVERY_REQUIREMENTS.md)
- [成熟组件集成](docs/INTEGRATION.md)
- [从旧编排台迁移](docs/MIGRATION.md)

## 兼容保留：旧本地编排台

旧 Python 看板、SQLite 调度器和执行适配器暂时保留，用于回归与迁移验证，不再作为新产品能力的落点。旧服务仍可用：

```bash
python3 run.py serve
```

以下章节记录旧版能力和使用方法。

## 已有能力

- 中文 AI Kanban：待处理、可执行、执行中、等待确认、评审、阻塞、失败和完成；
- 多角色工作流：技术协调者、方案规划者、实现工程师、独立评审者和质量验证者；
- 父子任务、显式依赖、循环依赖检查和完成后的自动解锁；
- 协调 Agent 可提出最多 20 个子任务及其依赖；审批恢复时按“同一父任务 + 规范化标题”复用旧任务，不重复建卡；
- 子任务必须明确是否写文件；协调、规划和评审只读，文件交付必须交给实现或 QA 并列出预期路径；
- 可为实现/QA 任务声明“必须交付的文件”；Agent 报完成后系统逐项确认文件存在且非空，验收失败不会释放下游；
- 异构执行器：同一个任务图里，实现可以走 Codex，独立评审可以走 Claude Code；
- 额度感知调度：读取登录账号的剩余比例和刷新时间，在执行器与高/中/经济模型之间自动分配；
- 每个任务使用独立 Git worktree 和 `agent/...` 本地分支；
- 下游任务从上游分支继续；有多个依赖时在自己的 worktree 中本地整合；
- Codex JSONL 事件、结构化结论、token 使用量、会话 ID 和交接说明持久化；
- 任务详情聚合运行失败、阻塞、额度等待、评审契约违规和 CLI stderr；重复告警自动归并，已知安装噪声不遮挡真正异常；
- 独立评审自动分配给另一个执行器；评审者修改产品代码会被拒绝提交并判失败；
- Agent 自报的检查记入账本并在闸门上呈现，空检查列表会显著标出；
- 人工批准、拒绝、评审通过、要求修改以及同一会话继续执行；
- SQLite 本地存储，服务重启后可恢复任务；
- 完成变更后自动创建任务分支的本地提交，但执行器绝不 push、合并或部署；只有人在最终审批门明确批准写任务后，控制面才会用 `ff-only` 将任务分支合入本地基准分支；
- 可作为 MCP 服务接入任意 MCP 客户端（Claude Code、Codex CLI、LobeHub 等），但审批和评审仍然只能由人在本地看板完成。

运行时仅使用 Python 3.9+ 标准库，没有第三方 Python 运行依赖。

## 运行条件

1. macOS 或其他具备 Python 3.9+ 和 Git 的本机环境；
2. 至少一个已登录的执行器 CLI：Codex CLI（ChatGPT 登录）或 Claude Code CLI；
3. 目标代码目录已经初始化为 Git 仓库，并至少有一个提交。

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
5. 协调 Agent 会只读分析仓库，提出实现、评审和 QA 子任务，并为文件型任务声明必需产物；
6. 在“等待确认”栏审核方案。批准后，下游第一个任务进入“可执行”；
7. 逐项启动或为项目开启自动调度；
8. 在任务详情中查看摘要、交接、对话、运行记录和完整 Diff；
9. 最终代码仍留在 `agent/...` 本地分支，由人决定如何合入。

任务详情会每 3 秒刷新。“运行异常与警告”会把原始事件账本里的失败、阻塞、额度等待、独立评审违规及 stderr 聚合出来；重复行显示次数，完整原始事件仍保存在 SQLite。没有异常时会明确显示绿色状态，而不是留一个容易误解的空白区域。

推荐第一个验证目标：

> 为现有项目增加一个只读版本信息端点。请先分析代码并拆成实现、独立评审和 QA 三步；实现必须有自动化测试，不得新增依赖、对外通信、push、合并或部署。方案先等我批准。

## 两种调度方式

- 手动调度：任务依赖满足后进入“可执行”，由人点击“智能启动”。适合首次使用和高风险工程；
- 自动调度：项目或单个任务开启后，依赖满足即自动进入执行队列。人工审批和评审节点仍然生效。

执行器默认有 2 个并发工位。并发任务各自拥有独立 worktree，不会在同一个工作目录互相覆盖。

## 智能额度分配

任务不指定执行器时，调度器会比较当前可用执行器的订阅额度，再决定由谁执行以及使用哪一档模型。它不是用账号价格硬猜额度：

- Codex 通过本机 `codex app-server` 的 `account/rateLimits/read` 读取 ChatGPT 套餐、已用比例、额度窗口和 `resetsAt`；不会读取 OAuth Token，也不会发送模型请求；
- Claude Code 从 CLI/Agent SDK 的 `rate_limit_event` 读取 `utilization`、额度类型与 `resets_at`，只把不含凭据的快照写入 `.data/quota/claude-code.json`；
- Claude 尚未运行、没有可验证快照时显示“额度待观测”，并按谨慎档处理，而不是伪造一个精确数字；
- 额度触顶的自动任务保持 `ready`，等刷新后重新判断，不会被误记为代码执行失败；
- 人在任务上明确指定执行器，或项目固定默认执行器时，人工选择优先；已有会话固定沿用原执行器和模型，避免跨平台恢复错误。

策略分四档：充裕时允许高档模型处理复杂规划、实现和评审；适中时按角色选择；偏紧时降档；进入保留区时只用经济模型。离刷新不足 30 分钟时会适度放宽，因为同样的剩余额度在窗口末尾比窗口开头更值得使用。调度器不会自动购买额外用量，也不会自动消耗 Codex 的 rate-limit reset credit。

默认模型档位：

| 档位 | Codex | Claude Code |
| --- | --- | --- |
| 高 | `gpt-5.6-sol` | `opus` |
| 中 | `gpt-5.6-terra` | `sonnet` |
| 经济 | `gpt-5.6-luna` | `haiku` |

Claude 官方从 2026-06-15 起把订阅账号上的 `claude -p` / Agent SDK 用量计入独立的月度 Agent SDK credit；本工程调用的是 `claude -p`，所以调度依据应以它实际返回的 rate-limit event 为准，而不能拿交互界面的 5 小时进度条代替。

## 作为 MCP 服务接入其他客户端

编排器可以把自己暴露成一个标准 MCP（Model Context Protocol）服务，让外部的对话式客户端读取和扩展任务图、启动任务、查看 Diff。这一层是客户端无关的：Claude Code、Codex CLI、Cursor、LobeHub 都能连。

先确保 `python3 run.py serve` 正在运行，然后：

```bash
python3 run.py mcp
```

它通过 stdio 说 JSON-RPC，只向本机 `127.0.0.1` 的编排器 HTTP API 转发请求。这个进程**不持有数据库、不启动调度器、不调用 Codex**，也拒绝连接任何非回环地址。

在 MCP 客户端里的典型配置：

```json
{
  "mcpServers": {
    "codex-orchestrator": {
      "command": "python3",
      "args": ["run.py", "mcp"],
      "cwd": "/Users/wangxian/Documents/ChatGPT/AI学习/codex-dev-orchestrator"
    }
  }
}
```

### 暴露的工具

| 工具 | 作用 |
| --- | --- |
| `list_projects` | 列出人工登记过的仓库 |
| `get_status` | 读取任务图：不带参数看健康状况，带 `project_id` 看分栏汇总，带 `task_id` 看单任务详情 |
| `get_diff` | 读取某个任务分支相对基准分支的本地 Diff |
| `list_pending_approvals` | 列出所有等待人工决定的审批和待评审任务，附看板深链 |
| `plan_workflow` | 为一个研发目标创建协调者任务；`allow_delegation` 和 `requires_approval` 被强制为真 |
| `create_task` | 向任务图追加一个任务，可用 `executor` 指定执行器 |
| `add_dependency` | 建立依赖，循环由编排器拒绝 |
| `run_task` | 把任务排入执行队列，在独立 worktree 中运行 |
| `retry_task` | 重试失败或阻塞的任务 |

### 刻意不暴露的操作

批准、拒绝、评审通过、要求修改、登记新项目，以及给任务留人工消息——这些都不是工具。MCP 只会返回形如 `http://127.0.0.1:8765/#/task/tsk_xxxx` 的深链，由人打开本地看板决定。

原因是这个工程的价值就在于闸门是确定性状态机，而不是提示词里的约定。把 `approve_task` 做成模型可调用的工具，等于把审批降级成"提示词里说要人类确认"；MCP 客户端的确认弹窗也不等于一条有身份、有理由、可审计的审批记录。同理，`message` 通道会让模型写入的内容以 `Human` 身份进入下游任务上下文，因此一并排除。

转发层还有一份硬编码的接口白名单：即使以后新增工具，也无法构造出白名单以外的请求路径。

## 配置

通过环境变量调整：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ORCH_DATA_DIR` | 工程内 `.data` | SQLite、运行结果和 worktree 的位置 |
| `ORCH_HOST` | `127.0.0.1` | Web 服务监听地址 |
| `ORCH_PORT` | `8765` | Web 服务端口 |
| `ORCH_MAX_WORKERS` | `2` | 同时运行的 Agent 任务数 |
| `ORCH_CODEX_BINARY` | `codex` | Codex CLI 路径或命令名 |
| `ORCH_CODEX_MODEL` | 空 | 固定所有 Codex 任务的模型；空表示允许智能分档 |
| `ORCH_CODEX_MODEL_HIGH` | `gpt-5.6-sol` | Codex 高档模型 |
| `ORCH_CODEX_MODEL_BALANCED` | `gpt-5.6-terra` | Codex 中档模型 |
| `ORCH_CODEX_MODEL_ECONOMY` | `gpt-5.6-luna` | Codex 经济模型 |
| `ORCH_EXECUTORS` | `codex` | 启用的执行器，逗号分隔：`codex`、`claude-code` |
| `ORCH_DEFAULT_EXECUTOR` | 列表第一个 | 额度相近或未知时优先的执行器 |
| `ORCH_CLAUDE_BINARY` | `claude`，回退 `CLAUDE_CODE_EXECPATH` | Claude Code CLI 路径或命令名 |
| `ORCH_CLAUDE_MODEL` | 空 | 固定所有 Claude Code 任务的模型；空表示允许智能分档 |
| `ORCH_CLAUDE_MODEL_HIGH` | `opus` | Claude 高档模型 |
| `ORCH_CLAUDE_MODEL_BALANCED` | `sonnet` | Claude 中档模型 |
| `ORCH_CLAUDE_MODEL_ECONOMY` | `haiku` | Claude 经济模型 |
| `ORCH_QUOTA_SCHEDULING` | `1` | 是否启用额度感知的执行器与模型选择 |
| `ORCH_QUOTA_CACHE_SECONDS` | `60` | Codex 实时额度快照缓存时间 |
| `ORCH_CROSS_REVIEW` | `1` | 评审子任务是否自动换用另一个执行器 |
| `ORCH_RUN_TIMEOUT_SECONDS` | `3600` | 单次执行超时，最低 60 秒 |

例如换端口：

```bash
ORCH_PORT=8877 python3 run.py serve
```

## 执行器

默认只启用 Codex。要同时启用两个：

```bash
ORCH_EXECUTORS="codex,claude-code" python3 run.py serve
```

选择顺序是**现有会话固定 → 任务指定 → 项目默认 → 额度智能比较**。部署默认只在额度接近或不可观测时作为偏好。新建任务保持“智能分配（推荐）”即可，也可以通过 MCP 的 `executor` 参数固定执行器。

| | Codex CLI | Claude Code CLI |
| --- | --- | --- |
| 只读角色（协调、规划、评审） | `--sandbox read-only` | 工具白名单只有 `Read,Grep,Glob`，并要求原生 Bash sandbox 可用 |
| 写入角色（实现、QA） | `--sandbox workspace-write` | 白名单加上 `Edit,Write,Bash` 等，Bash 仍在原生 sandbox 内运行 |
| 工作目录 | `--cd <worktree>` | 进程 cwd 固定为该任务 worktree |
| 结构化结果 | CLI 原生 `--output-schema` | CLI 原生 `--json-schema`，结果从 `structured_output` 读取 |
| 网络与发布 | 沙箱限制网络 | 原生 sandbox 禁止本机回环访问和本地端口监听；工具策略另拒绝 Web 工具及 Git 发布操作 |
| 外部工具面 | 沙箱内无 MCP | `--mcp-config '{"mcpServers":{}}' --strict-mcp-config` 切断继承来的 MCP 服务 |
| 会话恢复 | `exec resume <session>` | `--resume <session>` |
| 登录检查 | `codex login status` 必须是 ChatGPT 登录 | `claude auth status` 返回 JSON，校验 `loggedIn` 并报告登录方式与订阅类型 |

以上参数对照 Codex CLI `0.148.0-alpha.21` 和 Claude Code `2.1.237` 实测确认。两条容易踩的坑：`--json-schema` 不接受带 `$schema` 草案引用的 schema（编排器会自动剥掉），并且 `--allowedTools` 这类变长参数会吞掉紧随其后的提示词，所以提示词前必须有 `--` 结束符。

两条永远不变的规则：任何执行器都不会拿到 `danger-full-access` 或 `--dangerously-skip-permissions`；任何执行器都不能 push、合并或部署。

### CLI 不在 PATH 上

两个 CLI 都可能装在非标准位置。Codex 桌面版在 `/Applications/ChatGPT.app/Contents/Resources/codex`；Claude Code 的 IDE 扩展版在 `~/.cursor/extensions/anthropic.claude-code-*/resources/native-binary/claude` 或 VS Code 的对应目录。用环境变量指过去即可：

```bash
ORCH_CODEX_BINARY="/Applications/ChatGPT.app/Contents/Resources/codex" \
ORCH_EXECUTORS="codex,claude-code" python3 run.py doctor
```

`claude` 不在 PATH 且 `ORCH_CLAUDE_BINARY` 未设置时，编排器会回退到 `CLAUDE_CODE_EXECPATH`（从 IDE 终端启动时通常已经导出）。`doctor` 会明确告诉你哪个执行器找不到。

某个执行器不可用不会拖垮看板——`doctor` 会分别列出每个执行器的状态，只要还有一个可用，其余任务照常运行；指定了不可用执行器的任务会单独失败并说明原因。

```bash
ORCH_EXECUTORS="codex,claude-code" python3 run.py doctor
```

## 安全边界

编排器的安全模型是“本机控制面 + 隔离工作树 + 人工闸门”：

- 只接受本机已经登录的执行器 CLI；
- 不提供任何模型 API 调用代码，也不向子进程传递常见 API Key（OpenAI 与 Anthropic 两侧都剥离）；
- 规划、协调和评审角色使用只读沙箱；
- 实现和 QA 可以写，但工作目录限定为该任务的独立 worktree；Claude 的 Bash 还要求原生 OS sandbox 成功启动，并禁止访问本机控制面；
- 评审角色只报告发现，不修复文件；即使某个执行器越过只读约束产生改动，编排器仍会记录契约违规、拒绝提交并让任务失败；
- Agent 提示明确禁止 push、merge、删分支、发布、部署和联系外部人员；
- Git 服务只创建 worktree、本地分支、本地提交，并可在下游 worktree 中整合依赖分支；
- 架构、安全策略、破坏性迁移、含糊产品决定和最终合并应保留人工确认；
- 本机 HTTP 控制面只信任回环 Host；写请求要求 JSON 和同源浏览器上下文，以降低 DNS rebinding 与 CSRF 风险；
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

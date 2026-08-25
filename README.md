# AI 工程治理层（LobeHub + 本地 Codex）

这个项目不再自建另一套 AI 看板和多 Agent 调度器。主产品底座改为
[LobeHub](https://github.com/lobehub/lobehub)：它已经提供成熟的任务 UI、项目、任务树、
依赖、暂停/继续、复用已有 Topic、Codex/Claude 等异构 CLI、运行时间线、程序/Agent/LLM
验收器、自动返修上限、跨轮 Acceptance 和最终人工接受/拒绝。

本仓库只保留 LobeHub 尚不能替我们决定的工程治理原则：策略判断通过无状态 MCP 交给
LobeHub，九步推进则由一个只保存恢复检查点的薄控制循环负责：

- 默认由一个已有上下文连续负责调查、实现和返修；
- 只有真正独立的并行工作、对抗性证伪或上下文污染时才开新 Topic；
- 不再机械模拟“项目经理、架构师、开发、测试、QA”等人类岗位；
- 确定性程序检查优先，之后才是独立 Agent 证伪；
- 自动返修最多一轮，再失败或出现分歧就进入人的决策信箱；
- 人负责方向、审美、体验、不可逆/高风险决定，以及最终合并和发布责任；
- 执行使用本机登录的 Codex CLI 订阅额度，不接模型 API，不接 API Key。

## 架构

```text
人提出目标
  ↓
LobeHub Project / Task / Topic / Timeline / Acceptance UI
  ├─ 继续最匹配的已有 Topic（默认）
  ├─ 新 Topic：仅独立并行、对抗证伪、上下文不兼容
  └─ LobeHub heterogeneous harness → 本地 Codex CLI
          ↑
本仓库的无状态治理 MCP
  ├─ 目标 → 带版本与哈希的可验证工作契约
  ├─ 上下文亲和路由
  ├─ 风险自适应质量维度与只读监察 Lane
  ├─ 发现聚合、一次返修与发布就绪判断
  └─ 本地 Codex 额度与模型档位建议
```

LobeHub 是任务事实源。这个项目不再复制任务状态、评论、验收记录或前端 UI。默认的隐私
方向是使用上游未修改的**本机自托管实例**；不要求登录 LobeHub Cloud。

## 当前环境

已验证的 LobeHub Desktop：`2.2.14`。官方应用保持原样安装，本项目不复制或修改其源码。

LobeHub 的完整应用使用 LobeHub Community License；我们只把它当未修改的外部产品使用。
本仓库自己的 Python 治理代码继续保持独立。

需要明确区分两种“登录”：

- LobeHub Cloud 账号：不是必需项，本项目当前不要求登录；
- 自托管实例的本地身份：完整 Project/Task/Topic 持久化仍需要一个数据所有者。它只存在于
  自己的本机服务，不是 LobeHub Cloud 账号。

因此“暂时不登录”是合法的安装/开发状态，但在建立本地身份前不能把 LobeHub CLI 的任务
创建、续跑和验收当成已经可用。

## 第一次使用

### 1. 检查

```bash
cd "/Users/wangxian/Documents/ChatGPT/AI学习/codex-dev-orchestrator"
python3 run.py doctor
```

检查项包括：

- LobeHub Desktop/CLI 是否存在；
- 当前选择的是 LobeHub Cloud 还是本机自托管实例；
- 身份是否已建立，以及是否已达到 `ready_for_live_runs`；
- Codex CLI 是否以 ChatGPT Plus/Pro/Team 等订阅方式登录；
- LobeHub `hetero exec` 是否支持 Codex 和显式 `--agent-arg`；
- 在线 Desktop/CLI 设备通道（仅用于连接与可观测，不作为 Provider API Key）；
- 可导入 LobeHub 的本地 MCP 配置；
- `api_keys_required` 必须为 `false`。

`ok` 表示安装和配置检查本身没有错误；`ready_for_live_runs` 才表示 LobeHub 身份与 Codex
订阅执行端都已准备好。暂缓登录时，相关信息进入 `pending_actions`，不会伪装成已经可运行。

### 2. 优先选择本机自托管 UI

上游 LobeHub 提供完整自托管应用。准备好本机服务后，把 CLI 目标指向它：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py doctor
```

本项目不会复制 LobeHub 源码，也不会为了去掉登录而修改它的数据库身份约束。正式接入前有
两个必须验证的门槛：上游 Project/Task/Topic/Acceptance 在自托管版中完整可用；只使用本地
Codex heterogeneous CLI 时不需要配置任何模型 API Key。任一不满足，就不把 LobeHub 选为
事实源。

当前机器已经使用 Docker Desktop 启动完整的 LobeHub 自托管栈，入口为
`http://127.0.0.1:3210`。部署配置位于被 Git 忽略的
`.data/lobehub-selfhost/`：LobeHub 与 RustFS 只监听回环地址，PostgreSQL 与 Redis 不发布
宿主机端口，`.env` 权限为仅当前用户可读，且不包含任何模型 API Key。

本机服务的日常启动与状态检查：

```bash
cd "/Users/wangxian/Documents/ChatGPT/AI学习/codex-dev-orchestrator/.data/lobehub-selfhost"
docker compose --env-file .env up -d
docker compose --env-file .env ps
```

### 3. 身份（可以暂缓）

若以后启用本机自托管实例，只需要在该实例建立本地身份：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py login
```

这不是 LobeHub Cloud 登录。Codex CLI 继续使用它自己的 ChatGPT 订阅身份；不要设置
`OPENAI_API_KEY`。

### 4. 把治理 MCP 导入 LobeHub

```bash
python3 run.py lobehub-config
```

复制输出的 JSON。在 LobeHub Desktop 中进入设置的自定义 MCP 页面，选择快速导入 JSON。
它会启动：

```text
python3 /绝对路径/run.py mcp
```

该 MCP 只有七个无状态策略工具：

| 工具 | 作用 |
| --- | --- |
| `compile_engineering_goal` | 冻结目标、非目标、禁区、风险、验收和安全/恢复边界；关键结果缺失时返回追问 |
| `route_to_context` | 对 LobeHub 已有 Topic 做可解释的上下文亲和度路由 |
| `get_codex_quota_advice` | 读取本地 Codex App Server 的额度窗口并建议模型档位 |
| `compare_engineering_contracts` | 检测目标、范围、验收、安全和风险漂移，要求人确认受保护字段变化 |
| `build_verification_plan` | 按风险与变更面生成确定性门禁和独立只读反证 Lane |
| `aggregate_verification_findings` | 过滤低置信噪声、去重并决定通过、一次集中返修或升级 |
| `decide_release_readiness` | 根据契约和证据判断本地交付/人工决策/发布就绪，不执行外部动作 |

它没有批准、合并、发布、删除、任意命令执行或 API Key 工具。

### 5. 在 LobeHub 原生对象中建立项目与验收规则

本地身份建立完成后运行：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py bootstrap \
  --project-name "AI 工程治理层" \
  --identifier DEV \
  --repo "/Users/wangxian/Documents/ChatGPT/AI学习/codex-dev-orchestrator"
```

这会幂等地完成三件事：

- 保留 Project Coordinator 的治理指令，但不把它伪装成 Codex 执行端；
- 根据当前订阅额度为逻辑 owner/verifier 选择 Sol、Terra 或 Luna；
- 建立四个面向用户结果的 Criterion 和 `maxRepairRounds=1` 的 Rubric。

owner 和 verifier 不是 LobeHub Provider Agent，而是两种执行策略：
`hetero:codex:workspace-write` 和 `hetero:codex:read-only`。它们都调用本地已登录
Codex CLI，不配置模型 API Key。项目还安装了 LobeHub 官方 `acceptance`
Skill，以及本仓库的 `govern-engineering-delivery` Skill。

也可以直接创建一张已经带治理契约的根任务：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py goal \
  --project <LobeHub项目ID> \
  --name "实现版本端点" \
  --goal "为服务增加只读版本端点" \
  --outcome "调用方可以读取当前构建版本，并在无此能力时得到明确错误" \
  --accept "GET /version 返回当前构建版本" \
  --accept "未知构建版本时返回文档化的失败响应" \
  --non-goal "不改变已有写接口" \
  --prohibit "不新增对外网络调用" \
  --surface api \
  --observe "版本端点成功/失败日志" \
  --risk low
```

如果没有可观察的 Acceptance Criteria，或者只写“测试通过/CI 绿色”，命令不会创建任务，而是
返回 `needs_clarification` 和必须回答的关键问题。成功时会同时返回 `task_id` 与
`topic_id`：Task 是工作事实源，Topic 保存冻结契约和 Codex 事件，Task 评论中保存
稳定关联。

LobeHub 2.2.14 的 Task 负责人界面只接受 Provider Agent，尚不能直接绑定
heterogeneous Codex。本项目只用发布版 CLI 的 `topic create`、`message create/edit` 和
`task comment` 做薄关联，不读写 LobeHub 私有数据库或内部 API。

## 九步自动控制循环

`run-governed-task` 把原先需要主控逐项调用的九步接成一个可恢复状态机：

1. 编译并冻结工作契约；
2. 创建或核对 LobeHub Task，并绑定 Owner Topic；
3. 按上下文亲和度继续已有 Topic/原生 Codex Session；
4. 读取本地订阅额度，冻结本轮 Owner/Verifier 模型选择；
5. 用 `workspace-write + --approve-for-me` 运行连续 Owner；
6. 用无 shell 的明确参数执行确定性门禁；
7. 只在门禁通过后运行计划要求的只读反证 Lane；
8. 有阻断证据时，把失败批量返回原 Owner，最多自动返修一次并完整回归；
9. 生成 Acceptance handoff，暂停 Task，等待人做最终 accept/reject。

输入是一个 JSON spec。可从
[`docs/governed-task.example.json`](docs/governed-task.example.json) 复制：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py run-governed-task \
  --spec docs/my-task.json
```

命令会返回 `run_id` 和本机恢复账本路径。发生进程退出、LobeHub 暂时不可用或 Codex
中断后，使用同一个运行 ID 恢复：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py run-governed-task \
  --resume <run_id>
```

查看状态不会推进运行，也不要求调用模型：

```bash
python3 run.py governed-task-status <run_id>
```

高风险、主观、安全敏感或不可逆契约会在材料执行前停下。人在看过 LobeHub Task 后，可用
显式命令记录决定并从同一检查点继续；这个记录带入 Task 时间线，不是 Agent 模拟审批：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py run-governed-task \
  --resume <run_id> \
  --approve-material-execution \
  --decision-note "已确认信任边界、回滚路径和可观测信号"
```

恢复账本默认位于被 Git 忽略的 `.data/governed-runs/`，权限为当前用户可读写。它只保存
阶段、LobeHub 对象 ID、Codex Session、脱敏门禁摘要和事件 ID；Task、对话和 Acceptance
内容仍以 LobeHub 为事实源。每次 `pending → running → completed/skipped/waiting` 都先原子写入
账本，再以 `[engineering-governance run-event]` 幂等评论同步到 Task。

若本地在 Codex 已完成并把正文写入 LobeHub 后、尚未来得及完成阶段检查点就退出，恢复时会
先核对原 assistant message；正文已经存在就直接采用，不会重复执行这一轮。程序门禁只接受
argv 数组且不启 shell，拒绝内联解释器、明显的删除、发布、部署和危险 Git 命令；门禁前后
工作区指纹变化也会直接判失败。原始门禁输出不写入 Task 评论，账本只保留脱敏尾部和完整
输出摘要哈希。

最终状态是 `awaiting_human_acceptance`。控制循环不会自动 push、merge、deploy、publish，
也不会代替人在 LobeHub Acceptance 点击接受或拒绝。

## 手动推进与故障排查

1. 建立或选择一个 Project；
2. 把业务目标、非目标、禁区、风险和用户可观察的验收结果交给
   `compile_engineering_goal`；只有返回 `status=ready` 才继续，并冻结 `contract_hash`；
3. 使用 `goal` 命令创建未分配 Provider Agent 的根 Task 和已关联 Topic；
4. 从相同 Project/仓库的 Topic 中调用 `route_to_context`；
5. 返回 `continue_existing` 时，使用 `execute-topic --resume <CodexSessionId>` 继续；
6. 调用 `build_verification_plan`，先运行仓库自己的确定性门禁；这些门禁不进入 Acceptance checks；
7. 只启动计划点名的全新只读 Topic 做反证，再用 `aggregate_verification_findings` 合并证据；
8. 如有阻断发现，原 owner Topic 只做一次集中返修；再次失败或分歧就升级；
9. 按官方 `acceptance` Skill 发布不可变证据 round；人的最终 accept/reject 不可由 Agent 模拟。

这组步骤是自动循环的展开形式，主要用于排查某个状态、单独重放安全的只读步骤，或接入尚未
支持的仓库门禁；正常新任务优先使用 `run-governed-task`。

执行一个已冻结任务：

```bash
ORCH_LOBEHUB_SERVER="http://127.0.0.1:3210" python3 run.py execute-topic \
  --task <Task ID或DEV-1> \
  --topic <Topic ID> \
  --repo "/绝对路径/仓库" \
  --sandbox workspace-write
```

独立监察必须改为 `--sandbox read-only`。续跑时增加 `--resume <CodexSessionId>`。编排器
始终显式传入沙箱，禁止 `--dangerously-bypass-approvals-and-sandbox`；LobeHub
2.2.14 与当前 Codex CLI 的 resume 参数顺序差异由一个只重排参数的兼容层处理。

首次执行的返回值中，`session_id` 表示调用时传入的续跑 ID，因此通常是 `null`；
`continuation_session_id` 是这次运行捕获到、供下一次 `--resume` 使用的原生 Codex session。
该 ID 同时写入 Task 评论。适配层只从受限临时 raw dump 解析 `thread.started.thread_id`，随后
立即删除临时目录，不读取 OAuth 凭据或 API Key。

正常成功时，harness JSONL 事件仍会 ingest 到 LobeHub，编排器还会用官方
`message edit` 持久化最终文本，规避 2.2.14 成功 stream 不落正文的已验证差异。

测试、构建、Lint、类型检查和扫描器照常运行，但只作为交付门禁和一行叙述，不作为 Criterion。
最终 `accept/reject` 仍由人操作，治理 MCP 不具备这个权限。

## 额度策略

`get_codex_quota_advice` 通过本机 `codex app-server` 的
`account/rateLimits/read` 读取套餐、使用比例、额度窗口和刷新时间。它不读取 OAuth Token，
也不会发起模型请求。

建议映射：

| 档位 | Codex 模型 | 使用原则 |
| --- | --- | --- |
| high | `gpt-5.6-sol` | 额度充裕且任务复杂/高价值 |
| balanced | `gpt-5.6-terra` | 日常实现与一般分析 |
| economy | `gpt-5.6-luna` | 额度偏紧、机械任务或观察期 |

触顶时返回 `defer_until`，不会购买额外用量或自动消耗 reset credit。

## 旧版迁移

旧的 Python 看板、SQLite、worktree 调度器仍保留在 Git 历史和标签
`legacy-local-orchestrator-v1` 中；现有 `.data` 不删除，方便回看本次法律计费 Demo 的审计记录。

如确实要临时打开旧版：

```bash
python3 run.py legacy-serve --open
```

旧版只用于迁移和对照，不再接收产品功能。

## 测试

```bash
PYTHONPYCACHEPREFIX=/tmp/codex-orchestrator-pycache \
python3 -m unittest discover -s tests -v
```

运行时仍只依赖 Python 3.9+ 标准库。

## 资料依据

- [LobeHub 主仓库](https://github.com/lobehub/lobehub)
- [LobeHub Task](https://lobehub.com/task)
- [LobeHub 的 Codex 集成](https://lobehub.com/coding/codex)
- [LobeHub Desktop 下载](https://lobehub.com/downloads)

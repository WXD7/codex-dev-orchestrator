# 成熟组件集成

## 原则

只通过上游发布的接口组合，不复制上游源码，不让本治理层成为第二事实源。具体版本、认证和部署由对应组件管理。

## LobeHub

职责：人类入口、目标对话、Project/Task、审批、记忆、定时运行和日报。

将下面的 stdio 服务登记为 MCP（路径替换为本仓库绝对路径）：

```json
{
  "mcpServers": {
    "ai-delivery-governance": {
      "command": "python3",
      "args": ["run.py", "governance-mcp"],
      "cwd": "/absolute/path/to/codex-dev-orchestrator-ai-native"
    }
  }
}
```

LobeHub 可保存和展示契约、计划、Operator Snapshot、Review Packet 和裁决结果，但批准仍由其人类身份和界面完成。治理 MCP 不提供批准工具。Agent 监控应投影到现有 Project/Task 页面，不再开发另一套看板。

上游：[LobeHub](https://github.com/lobehub/lobehub)

## Kandev

职责：研发任务、Codex/Claude/Gemini 等 CLI 会话、worktree、Diff、代码审查、子任务与交接。

推荐组合：

1. 外部研究 Agent/浏览器收集社区实践、近期高质量学术研究、至少两个开源框架及其官方资料，交给 `compile_technology_research`。另建全新只读调研质检上下文；治理层只编译证据，不自行联网。
2. LobeHub 把原始对话、通过质检的 `research_hash` 和候选路径交给 `compile_intent_brief`，展示最终结果、开发执行器、产品运行时、技术选择理由、输入/输出样例、非目标和风险边界。人同时选择单一路线或 2–3 路有界赛马，并冻结测试、指标、预算、停止条件和融合权限。
3. 创建全新只读意图检查上下文，对照原始对话、冻结调研、简报、拟定契约和验收样例提交 coverage/findings，再由 `compile_intent_inspection` 编译为 PASS/BLOCKED。检查员只提问，不修改或代答。
4. LobeHub 形成目标并调用 `compile_work_contract`。只把未解决的政策选择和领域事实显示为追问；工程不变量和可研究事实进入 owner/研究路线。
5. 契约未就绪时不创建可写开发任务。人类回答经 `propose_contract_resolution` 形成 delta 后，由 Kandev 侧可信控制器签名绑定父契约、新契约、新计划和原 Kandev task；attestation 不通过 MCP。
6. 即使契约和意图检查已通过，可信控制器仍必须调用 `attest_intent_alignment()` 记录真实人的确认，绑定 research/strategy/intent/inspection/contract/plan/external task；该工具不进入 MCP。
7. Kandev 侧先运行 environment capsule，确认 cwd、真实 Diff 根、权限、PATH、端口和锁。
8. 控制器创建原子签名 ledger，把私有 control token 隔离在所有 Agent 上下文之外。缺有效意图 attestation 时 `create_run_ledger()` 拒绝创建实现任务。
9. 就绪调研、意图、契约、验证计划、Bad Case registry 和 calibration policy 作为 Kandev 任务附件或仓库 `.ai-delivery/` 文件。
10. 调用 `build_delivery_handoff` 得到 owner 蓝图、调研、赛马和意图任务的可视状态。赛马路径在独立 context/worktree 中使用同一冻结数据和测试且互不可见；统一评测员只读盲评。随后 `attest_race_selection()` 绑定人的 keep/fuse/reject-all 决定；签署前 main owner 不能创建，reject-all 直接停止。
11. 每次状态变化/心跳调用 `record_agent_progress()`；Kandev/LobeHub 用 `build_operator_snapshot()` 显示唯一 Agent 数、中文名、进展、困难、依赖、来源 task/session 和 shadow/blocking 模式。
12. Kandev 的 Diff、测试和结构化复现包进入 `adjudicate_delivery`，跨通道同根因合并。新/变更 Inspector 的未校准发现保持可见但不阻塞。
13. 返修只交回原 owner 会话；完整复验后新建盲审 verifier；再次失败停止。最后用 `build_human_review_packet()` 交给人，而不是把 Agent 完成态当批准。

Kandev MCP 中的任务创建、分支和交接仍由 Kandev 自己执行，本治理层不保存它们的镜像。

### 原生智能体监控插件

仓库内的 [`integrations/kandev-agent-observer`](../integrations/kandev-agent-observer/README.md) 是 Kandev 0.91.0 原生插件实现。它通过正式插件路由、侧边栏入口、任务卡标签、任务详情顶栏和任务面板，把 Kandev 自己持有的 task/session/message 投影为中文 Agent 视图；不读取 Kandev SQLite，不调用私有 Zustand Store，也不复制任务数据。

总览显示任务/智能体数量、中文职责、工作、待人、等待、完成、失败和心跳健康；每张 Agent 卡显示使命、进展、困难、依赖、下一步和来源任务。只有 `author_type=agent` 的约定格式消息被视为结构化心跳，其他状态明确标为 `Kandev 推断`，避免把推断伪装成 Agent 自报。前端所有页面和任务卡共享一个 12 秒轮询器。

构建与安装命令、插件能力声明和兼容边界见插件 README。安装后从 Kandev 侧边栏进入 `智能体监控`，或打开 `/ai-delivery-observer`。Kandev 0.91.0 尚未提供任务列表行摘要插槽，因此本实现没有通过 DOM 注入或私有 Store 强行扩展该页面。

Codex 实时事件推荐由 [`plugins/codex-agent-observer-bridge`](../plugins/codex-agent-observer-bridge/README.md) 伴生插件接入。项目 `.codex/hooks.json` 只在当前仓库配置层有效，任务 cwd 位于上级目录或其他仓库时不会向下搜索。伴生插件通过 `$PLUGIN_ROOT` 解决发现和路径可移植性，但不能越过信任：人在 `/hooks` 看到 `Installed 1 / Active 0 / Review 1` 后仍需审阅，只有 `Active 1` 且全新任务产生了 Start/协作/Stop 原始事件，才可把 Kandev 页面标记为真实验收成功。

实际安装和 Profile/MCP 配置见 `governance init` 生成的 `.ai-delivery/KANDEV_RUNBOOK.md`。这里有三个不能静默越过的上游边界：

- Kandev 的 Codex 模型、模式和配置来自当前已安装 Agent 的能力探测，便携工作流不能猜一个固定的只读 mode；
- 内置 Feature Dev 的 Review 可能直接修代码，PR/CI Fixup 会提交和 push，不能原样套用本项目策略；
- External MCP 同时包含配置和任务写工具，只能保留在回环地址或受认证网络后，并对写操作保留人类授权。

本地 LobeHub 可以同时连接治理 stdio MCP 与 Kandev 的 `http://127.0.0.1:<port>/mcp`。前者只计算，后者才执行任务写入；不要把两个权限面混成一个“超级 Agent”。

上游：[Kandev](https://github.com/kdlbs/kandev)

## OpenAI Symphony

职责：持续读取工单、并发调度 Codex、隔离工作区、重试、协调、卡死检测和重启恢复。

`governance init` 生成 `.ai-delivery/SYMPHONY_POLICY.md`。它是策略片段，不伪造 tracker、workspace、Codex 命令、并发和 hook 等部署配置。把片段纳入根据真实环境配置的 `WORKFLOW.md`。

Symphony 的成功表示到达下一交接状态，例如 Human Review；不代表 Agent 可以把任务自行标为最终接受。

Symphony 的 tracker issue ID 应作为 `external_task_ref` 进入契约恢复 attestation。其生命周期 hook 负责签名进展心跳和 Review Packet 投影；治理 MCP 仍只做纯编译/裁决，不能签名、恢复任务或证明用户身份。

上游：[Symphony 规范](https://github.com/openai/symphony/blob/main/SPEC.md)

## GitHub Spec Kit

职责：`constitution → specify → clarify → plan → tasks → analyze → implement`。

Spec Kit 产出的目标、用户故事、验收、非目标、约束和澄清结论应转成 `compile_work_contract` 输入。技术计划和任务列表不进入不可变意图字段，以免实现变化被误判为需求漂移。

项目宪法可以引用 `.ai-delivery/CONSTITUTION.md`，但不应让执行 Agent 在同一运行中修改治理宪法。

上游：[GitHub Spec Kit](https://github.com/github/spec-kit)

## GitHub Actions 与 gh-aw

职责：确定性 CI、PR 状态、审计、权限和安全输出。仓库已包含 `.github/workflows/ai-delivery-ci.yml` 作为本项目的基础门禁。

构建、Lint、类型、测试和扫描保持普通 Actions job。只在需要解释、调查、分类或生成时使用 agentic workflow。Agent job 默认只读；写操作必须经 safe outputs 和独立权限阶段。

远程 GitHub Agent 需要单独的引擎认证和计费策略，不能假定会复用本机 Codex 登录。第一阶段可以只把确定性 CI 放在 GitHub，AI 监察保留在本机。

上游：[GitHub Agentic Workflows](https://github.github.com/gh-aw/about/)

## Playwright 与安全工具

E2E/UX 通道优先使用 Playwright Test Agents，但 healer 受 `.ai-delivery/CONSTITUTION.md` 限制。Semgrep、Trivy、CodeQL、ZAP 等输出作为确定性或工具证据交给安全监察解释，不由 LLM 替代。

主动安全测试只对明确授权的本地或测试环境执行。

## 故障与降级

- LobeHub 不可用：不丢失仓库中的契约和计划，等待人类界面恢复。
- Kandev 不可用：可以用 Symphony 或直接 Codex owner 执行，但仍遵守同一契约和裁决。
- Symphony 不可用：Kandev 保持主要研发控制平面；不要同时启动第二套 scheduler 争抢同一任务。
- 远程 CI 不可用：要求人工确认是否允许本地等价检查；不能静默视为通过。
- 额度不可观测：标记 unknown，使用谨慎预算；不能编造剩余量。

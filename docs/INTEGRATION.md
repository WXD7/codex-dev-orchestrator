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

LobeHub 可保存和展示契约、计划和裁决结果，但批准仍由其人类身份和界面完成。治理 MCP 不提供批准工具。

上游：[LobeHub](https://github.com/lobehub/lobehub)

## Kandev

职责：研发任务、Codex/Claude/Gemini 等 CLI 会话、worktree、Diff、代码审查、子任务与交接。

推荐组合：

1. LobeHub 形成目标并调用 `compile_work_contract`。
2. 契约未就绪时只显示追问，不创建可写开发任务。
3. 就绪契约和验证计划作为 Kandev 任务附件或仓库 `.ai-delivery/` 文件。
4. 调用 `build_delivery_handoff`，Kandev 只创建其中的主要开发 owner；确定性证据全部通过后，才按清单创建新的只读监察任务。
5. Kandev 的 Diff、测试和检查工件进入 `adjudicate_delivery`。
6. 返修只交回原 owner 会话；第二轮停止。

Kandev MCP 中的任务创建、分支和交接仍由 Kandev 自己执行，本治理层不保存它们的镜像。

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

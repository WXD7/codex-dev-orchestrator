# Kandev 智能体监控插件

0.4.3 将 Codex 监控改为“实时 Hook 为权威状态、app-server 只补全持久化历史”，并补齐工作区强绑定、活动版本与协议校验、分发健康、会话隔离和活跃任务陈旧判定；同时修复上下文压缩误停、跨任务迟到事件覆写、首次投递失败被吞、空闲通道暴露工作中 Agent，以及并行创建时角色和父节点被猜错。

## 真实数据通道

1. **Kandev 原生 Agent**：插件订阅 `agent.stream` 与 `agent.stream.*`，只接收 `normalized.kind=subagent_task` 的结构化事件。
2. **Codex 实时 Hook**：`SubagentStart`、`SubagentStop`、`PreToolUse` 与 `PostToolUse` 直接记录启动、停止、纠偏、等待、恢复和中断。只有这条通道会进入 Codex 的“真实工作中”统计。
3. **Codex 持久化历史**：独立 `codex app-server` 只读 `thread/list`、`thread/read`、`parentThreadId` 和历史 `collabAgentToolCall`。所有卡片统一标记“历史记录”，绝不推断当前运行状态。
4. **Kandev 历史推断**：旧 task/session/结构化心跳用于任务卡和兼容视图，UI 明确标记为推断。

插件不读取 Kandev 私有数据库，不写任务，不发送消息，不启动 Agent，也不改变审批策略。

## 页面能力

- 侧边栏入口：`智能体监控`
- 双通道健康：Codex 实时 Hook / Codex 持久化历史
- 三类分区：Kandev 实时 / Codex 实时 / Codex 历史
- 中文职责名、真实执行状态、进展摘要、当前困难、上级 Agent
- 父子与纠偏 DAG
- 创建、纠偏、恢复、等待、关闭、停止、完成、失败和中断时间轴；连续重复等待会折叠计数；`SubagentStop` 明确显示为“已停止（结果未知）”，不冒充成功完成
- Kandev 历史推断独立灰色分区
- Kandev 任务卡、详情顶栏和任务面板的兼容状态

## 隐私与安全

- Hook 接收器只把 ID、事件枚举、状态码、派生中文角色和时间写入快照；`transcript_path`、Prompt、邮件正文、`last_assistant_message`、`tool_input` 与 `tool_response` 整体丢弃。
- 历史桥不再保存 `agentsStates.message`，也不再读取 Prompt 推断角色；进展和困难只使用预定义文案。
- 历史 app-server 子进程只继承运行必需的最小环境，不继承 `DEEPSEEK_API_KEY`、其他 API Key、Token 或 Secret。
- `api_key`、自然语言“API Key 是… / 密钥为…”、带空格引号值、Bearer Token 与常见 `sk-/ds-` Token 会在兼容心跳通道脱敏。
- 快照以原子替换方式写入，权限为 `0600`；失败时保留最后一次成功数据并标记 `stale`。
- 插件状态只使用 Kandev 官方插件 State API，最多保留 128 个 Agent、256 条关系和 512 条时间轴事件。

## 测试与构建

```bash
make package \
  GO_BIN=/path/to/go \
  KANDEV_BACKEND=/path/to/kandev/apps/backend
```

打包流程运行 Go 单元测试，编译 macOS arm64 插件，包含只读桥接脚本，并生成完整校验清单：

`dist/ai-delivery-agent-observer-0.4.3-darwin-arm64.tar.gz`

桥接器测试：

```bash
python3 -m unittest \
  bridge/test_codex_app_server_bridge.py \
  bridge/test_codex_hook_receiver.py \
  ui/test_ui_contract.py -v
```

## 启用 Codex 实时 Hook

推荐安装仓库内的 [`plugins/codex-agent-observer-bridge`](../../plugins/codex-agent-observer-bridge/README.md) Codex 伴生插件。它用插件自带 `hooks/hooks.json` 跨项目调用 Kandev 当前激活且校验通过的本机 `bridge/codex_hook_receiver.py`，避免任务从上级目录或其他仓库启动时无法发现项目 Hook。

仓库的 `.codex/hooks.json` 只覆盖当前仓库配置层，可用于单仓库定制和降级诊断。Codex 会在新任务启动时加载 Hook；伴生插件和项目 Hook 首次出现或配置变更后都必须通过 `/hooks` 审阅并信任精确哈希。未审阅的 Hook 会被跳过，这是预期的安全边界。诊断含义：`Installed 0 / Active 0` 是未发现；`Installed 1 / Active 0 / Review 1` 是已发现但待信任；`Active 1` 才表示事件源可运行。

默认实时快照位置：

`~/.kandev/plugins/ai-delivery-agent-observer/data/codex-hook-snapshot.json`

Hook 快照和锁文件均为 `0600`，目录为 `0700`。可用 `KANDEV_AGENT_OBSERVER_HOOK_EVENTS` 覆盖快照路径。

## 启动 Codex 持久化历史同步

这条通道不代表实时运行状态，只用于补全现有任务树和历史协作事件。根任务 ID 与 Kandev 工作区 ID必须显式绑定：

```bash
python3 bridge/codex_app_server_bridge.py \
  --root-thread-id <codex-root-thread-id> \
  --kandev-workspace-id <kandev-workspace-id>
```

默认快照位置：

`~/.kandev/plugins/ai-delivery-agent-observer/data/codex-app-snapshot.json`

可用 `KANDEV_AGENT_OBSERVER_EVENTS` 覆盖路径。`--cwd` 仅为旧命令兼容保留，不再落盘。

## 安装

把 0.4.3 包提交给 Kandev 原生插件安装接口，刷新页面后在 `Settings → Plugins → 智能体监控` 中确认启用。Kandev 原生事件由插件直接接入；Codex 实时事件推荐安装伴生插件、显式绑定工作区、在 `/hooks` 信任后用新任务加载；历史补全需要保持历史同步器运行。

## 已知边界

- Hook 配置通常从新任务开始生效；当前已经运行的任务不能作为首次启用后的完整生命周期验收样本。
- Hook 是事件驱动通道，不是长连接；`idle` 表示已收到过有效事件但目前没有活跃 Codex 任务，不能因为超过 30 秒没有事件就判定断线。仍标记活跃的任务连续 15 分钟无事件才保守标为陈旧。
- 伴生插件没有安装、启用并经 `/hooks` 信任前，页面只能显示 Kandev 原生事件或 Codex 历史，不能宣称“Codex 实时”已经验收。
- Codex Hook 的 `SubagentStart/Stop` 不提供任意进展正文或困难详情，因此页面只展示可证明的生命周期状态和预定义说明，不会从 transcript 猜测。
- 新 Agent 的中文角色优先来自安全的 `task_name/agent_type` slug；无法识别时显示“执行智能体”。
- 持久化历史不能证明当前运行状态，因此始终显示“历史记录”，即使独立 app-server 返回 active、idle、interrupted 等值。

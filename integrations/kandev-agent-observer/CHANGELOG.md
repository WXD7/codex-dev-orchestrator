# 版本说明

## 0.4.3 — 2026-08-26

- 删除“仅剩一个未匹配 spawn 就按到达顺序猜 child”的最后分支；没有 `PostToolUse` 精确 child ID 证据时，`SubagentStart` 只使用自带角色与根任务。
- 新增两个并行 spawn 中穿插无证明 child 的反例回归，防止两个 Agent 被永久显示为同一个中文职责与父节点。
- Go 服务端源码未变；本包复用 0.4.2 已完整编译和独立哈希复验的同一二进制，仅更新 Hook 桥与包元数据。

## 0.4.2 — 2026-08-26

- 并行 spawn 只在 `PostToolUse` 给出精确 child ID 时绑定角色与父节点；多个未决创建不再按到达顺序猜测。
- 新增并行乱序、早到 `SubagentStart`、`compact` 续租、SessionEnd 迟到事件、缺失快照投递失败和空闲通道假工作状态的回归测试。
- 使用完整 Go 1.26 工具链重新编译服务端，不再复用 0.4.0 二进制。

## 0.4.1 — 2026-08-26

- `SessionStart(source=compact)` 只续租当前会话，不再误停仍在工作的子 Agent；`startup`、`resume` 和 `clear` 继续隔离旧运行状态。
- 结束任务的迟到 Hook 事件改为不写快照，避免覆盖其他根任务仍然有效的桥状态与活跃计数。
- 快照缺失、不可读或协议无效时，页面仍优先展示已验证的 Hook 投递失败原因。
- `working` 与 `waiting_on_human` Agent 必须同时拥有 active bridge 和 15 分钟内的活跃根任务租约，异常 idle/ready 快照不再产生假工作状态。
- 并行创建子 Agent 时只使用 spawn 结果中的精确 child ID 绑定中文角色与父节点；无法证明对应关系时不再按到达顺序猜测。

## 0.4.0 — 2026-08-26

- 将 Codex Hook 快照协议升级到 `codex-hooks-observer/v2`，支持多个根任务、活跃任务计数和精确 `SessionStart/SessionEnd`。
- 捕获子 Agent 的命令、文件修改、计划、集成调用与权限等待，只保存白名单活动类别和固定中文摘要，不保存工具输入输出、Prompt、邮件正文或密钥。
- 新增 `waiting_on_human`、任务结束清场、嵌套父子关系和重复 spawn 去重，避免已结束 Agent 继续显示“工作中”。
- Codex Agent 卡按根任务分组，卡片、DAG 与时间轴均显示所属任务；顶栏绿点只在确有 Agent 工作时点亮。
- Hook 健康改为事件驱动语义：`active` 表示有活跃任务，`idle` 表示桥已就绪但无活跃任务，不再把安静状态误称为长连接。
- 伴生 Codex 插件只调用 Kandev 当前启用、协议匹配且 SHA-256 校验通过的接收器，并支持显式工作区绑定文件与结构化投递失败诊断。

## 0.3.1 — 2026-08-26

- `SubagentStop` 改为“已停止（结果未知）”，不再错误显示为“执行完成”。
- 纠偏目标兼容 `recipient` / `recipients`，避免 Agent 间纠偏在 DAG 中丢失目标。
- 实时 Hook 的连续重复等待折叠计数，避免 Pre/Post 重复计数和时间轴刷屏。

## 0.3.0 — 2026-08-26

- 新增 Codex 官方生命周期 Hook 接收器，实时接入 `SubagentStart`、`SubagentStop` 和 Agent 协作工具事件。
- 将独立 app-server 通道改名为“Codex 持久化历史”，彻底移出“真实工作中”统计，避免误报当前状态。
- 页面新增实时 Hook / 持久化历史双健康卡、三类 Agent 分区，并将刷新周期缩短到 4 秒。
- Hook 写入采用 `flock` 并发合并、原子替换、`0600` 文件和有界事件集合。
- 丢弃 transcript、Prompt、Agent 正文、工具输入输出；历史桥不再保存自由文本状态，也不再通过 Prompt 推断角色。
- 历史 app-server 子进程改用最小环境，明确剔除 DeepSeek 等 API Key 与 Token。
- 对 ID、时间、状态、事件类型和摘要实施白名单投影，并增加并发、隐私、纠偏与折叠等待的回归测试。

## 0.2.2 — 2026-08-26

- 新增 Kandev `agent.stream.*` 真实子 Agent 事件通道。
- 新增基于公开 `codex app-server` 的 Codex Desktop 只读桥。
- 新增中文 Agent 卡片、桥接健康、父子/纠偏 DAG 和协作时间轴。
- 将 Kandev 任务推断隔离为历史辅助视图，不再当作真实 Agent 生命周期。
- 将重复等待和重复活动折叠为一条累计事件，避免时间轴被轮询噪声淹没。
- Prompt、邮件正文、Agent 正文和密钥不落盘；桥接器与插件执行双层脱敏。
- 补齐 Kandev `state` 最小权限，使用官方插件状态 API 保存有界结构化事件。

## 0.1.1 — 2026-08-25

- 修复任务推断与结构化心跳在总览中的混淆。
- 将执行状态与交付评审状态分开显示。

## 0.1.0 — 2026-08-25

- 首次提供 Kandev 原生智能体监控页面与任务状态卡片。

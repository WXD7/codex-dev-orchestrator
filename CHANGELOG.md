# 版本记录

本项目采用语义化包版本；产品代号用于表达治理架构代际。`0.2.1` 对应 AI Delivery Governance V2.1。

## [0.2.1] - 2026-08-25（V2.1 二次融合）

### 核心变化

- 新增 Question Ledger，把真正需要人决定的问题与可研究事实、工程不变量分开；答案必须形成绑定父契约、外部任务和新验证计划的可信 delta，避免口头回答后静默漂移。
- 新增只由可信控制器持有的 HMAC 阶段账本、原子检查点与恢复协议；阶段推进取决于真实产物不变量，不再相信 Agent 自述或正常退出码。
- 默认启用“契约与领域语义、状态与信任边界、测试预言机”三条正交监察；风险通道按需扩展，发现按根因合并后只允许原 owner 一次统一返修。
- 新增全新只读上下文的最终盲审，逐项验证 must-kill 反例；盲审失败会诚实停在人工决策点。

### 可观察性与人工接管

- Agent 使用中文短名，并持续报告唯一 ID、使命、工作/等待状态、进展、当前困难、依赖、人工需要、来源 task/session、校准模式和心跳。
- Operator Snapshot 明确区分“Agent 是否在工作”和“交付是否通过”，供 Kandev/LobeHub 投影，不新增第二套任务看板。
- Review Packet 汇总签名证据哈希、阶段结果、阻塞点、shadow/blocking 发现、停止原因和仍需人决定的事项。

### 校准与学习闭环

- 新增 Good/Bad Case 注册表及 `candidate / confirmed / retired` 生命周期；只有经人工或领域专家确认且附复现证据的案例才能进入隐藏回归。
- 新增 Inspector 校准：同时衡量召回率、误报率、人机一致率、样本量和独立贡献；新增或实质变更的 Inspector 默认 shadow，达标后才获得 blocking 资格。
- 新增环境 capsule，开工前核对工作目录、真实 Diff 根、权限、工具、端口和锁。

### 接口与集成

- MCP 纯计算工具扩展为九个，新增契约答案提案、Bad Case 编译与 Inspector 校准；HMAC 签名、运行账本和实时快照仍不暴露给 Agent 或 MCP。
- `governance init` 额外生成 `runtime-protocol.json`、`bad-case-registry.json` 和 `calibration-policy.json`。
- 更新 Kandev、Symphony、Spec Kit 与 LobeHub 的职责边界和运行模板。

### 验证与兼容性

- 德国法律计费回放把属性测试、mutation test、真实浏览器 E2E 设为高风险硬证据，并固化三类 must-kill：审计伪造、子串修正证据和 Boolean/Integer 类型往返。
- 新增治理运行时、学习闭环、法律计费契约及 CLI/MCP 回归测试；V2.1 发布候选在 Python 3.9+ 无第三方运行依赖约束下通过 178 项测试、静态编译与 Diff 检查。
- 旧 Python 看板与调度器只做兼容保留；本版本未增加旧 UI 产品能力。

### 安全边界

- 不调用模型 API、不读取或接受 API Key；执行端仍使用本机已登录的 Codex CLI/app-server。
- 不自动 push、merge、deploy、发布、付费或绕过权限；最终合并与发布始终由人决定。

## [0.2.0] - 2026-08-24（V2 基线）

- 将本地多 Agent 编排器收敛为无状态 AI 交付治理层。
- 建立内容寻址的工作契约、风险路由、只读隔离监察、证据裁决、一次返修和人工终审。
- 明确 LobeHub、Kandev、OpenAI Symphony、GitHub Spec Kit、GitHub Actions 与本治理层的职责边界。

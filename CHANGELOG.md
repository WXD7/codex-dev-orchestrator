# 版本记录

本项目采用语义化包版本；产品代号用于表达治理架构代际。`0.2.4` 对应 AI Delivery Governance V2.4。

## [0.2.4] - 2026-08-26（无历史接管与项目隔离）

- 新增根目录 START_HERE_NEW_CODEX.md，提供可直接复制到任意新对话的中文启动指令、工作区路由安装、新项目首次接入、手工验收和信息归属指南。
- 新增只读 `governance takeover --target <exact-git-root>`，全新对话先绑定唯一 `project_id`，再校验 Git 根、分支、基线、remote、当前/历史来源、一致性断言和冷启动验收。
- `governance init` 新生成 `DRIVER_BOOTSTRAP.md`、`CURRENT_STATE.json` 和 `EVIDENCE_INDEX.json`；所有必读项目来源必须使用相对路径且经 SHA-256 绑定，重复、绝对、越界或未索引来源一律阻断。
- 工作区入口改为纯路由：只根据当前 Git 根或人明确指定选项目，目标不唯一时停止，不根据最近对话、UI 记录或兄弟目录顺序猜测。
- 全局层仅保留通用治理代码、匿名失败模式和评测方法；项目需求、决策、Prompt、证据、Case、预期输出、凭据和当前状态不得跨 `project_id` 传播。
- Git 身份检查使用剔除 Key/Token/Secret/Password/Credential 的最小环境；接管包禁止对话链接、thread ID 和密钥值依赖。
- 真实 ephemeral、只读、无旧对话驾驶员已完成一次项目接管并通过机器可校验输出契约；首次运行暴露并修复了 Codex 结构化输出对 `const` 字段必须同时声明 `type` 的兼容性要求。本轮通过治理仓库 220 项和目标项目 114 项回归，产品模型调用为 0。

## [Kandev 智能体监控 0.4.3] - 2026-08-26（Codex 跨项目实时监控修复）

- 新增 `plugins/codex-agent-observer-bridge` 伴生插件，通过插件自带 `hooks/hooks.json` 让任意项目都能发现 Codex `SubagentStart`、`SubagentStop` 和协作工具事件，不再依赖任务 cwd 恰好位于治理仓库。
- dispatcher 只选择 Kandev 激活清单明确指向、协议匹配且 SHA-256 校验通过的接收器；子进程使用最小环境，不继承 DeepSeek Key 或其他凭据，失败时不向模型注入接收器输出。
- Hook 快照协议升级到 v2：新增多根任务隔离、SessionEnd 清场、权限等待、嵌套父子关系、活动类别和中文固定进展；Kandev 页面按根任务分组并显示投递失败原因。
- 全局伴生插件支持本机 `codex-hook-binding.json` 显式绑定 Kandev 工作区，避免“插件已加载但因没有作用域而不投递”的隐性失败。
- 保留 Codex `/hooks` 的逐哈希人工信任边界；新增 `0/0`、`1/0/Review 1`、`Active 1` 三态诊断和新任务真实生命周期验收说明。
- 新增伴生插件回归测试并通过官方插件清单校验；真实端到端 Hook 验收必须在用户安装、启用、信任插件后由全新任务完成。
- Kandev 观察插件升级到 `0.4.3`：上下文压缩不再误停子 Agent，迟到事件不再覆写其他活跃任务，首次投递失败不再被缺失快照吞掉，非 active 通道也无法暴露假工作状态；并行 spawn 必须等到精确 child ID 才绑定，即使只剩一个待匹配请求也不再猜测角色与父节点。
- 用户信任全部七类 Hook 后，新的无网络、无密钥、无产品写入子 Agent 探针已自动产生并进入 Kandev 的 `SessionStart → SubagentStart → 工具活动 → SubagentStop` 事件链，来源明确为 `codex_hooks_realtime`；端到端最后一跳由“待信任”更新为“真实生命周期已验证”。

## [0.2.3] - 2026-08-25（V2.3 技术调研与有界赛马）

### 开发前技术调研门禁

- 新增 `compile_technology_research`，强制覆盖社区实践、近期高质量学术研究、至少两个开源框架及其官方仓库/文档；逐来源记录方法、主张、质量信号、局限和时效。
- 框架适配矩阵覆盖需求、成熟度、维护、安全、集成、扩展、生态和许可证；只列一个框架、单一社区域名、陈旧论文无近期佐证、适配矩阵缺项都会失败关闭。
- 新增全新只读“调研质检员”，禁止看到收集过程和候选实现，阻断选择偏差、来源错配和自我质检。
- 高影响技术选择必须绑定通过质检的 `research_hash`。人的目标、范围、非目标和风险边界仍高于 AI 技术推荐；AI 可以扩大搜索，但不能改写人的产品意图。

### 2–3 路有界技术赛马

- 人在意图签署前选择 `single_path` 或 `bounded_race`；赛马必须冻结路径、数据、统一测试、评估维度、时间/成本预算、停止条件和融合权限。
- 每条赛道使用唯一上下文和隔离 worktree，提交前互不可见，并绑定同一 contract/research/strategy/test/dimension 哈希。
- 全新只读“统一赛马评测员”对所有赛道使用同一标尺，只能建议保留一路、融合明确优点或全部不合格；它看不到赛道 transcript，也不能替人作最终决定。
- 新增 `attest_race_selection`：可信控制器把人的 keep/fuse/reject-all 决定绑定 evaluation hash。保留赢家继续原上下文；融合创建明确、全新的 integration owner；全部淘汰则在主实现前停止。

### 接口、观察和验证

- CLI 新增 `governance research`；无状态 MCP 新增 `compile_technology_research`，仍不暴露签署、Agent 启动或外部写工具。
- HMAC 意图证据链扩展为 research/strategy/intent/inspection/contract/plan/external task；Review Packet 同时展示调研、评测和人类选路哈希。
- Kandev 交接清单新增中文“技术调研员、调研质检员、技术赛道一/二/三、统一赛马评测员、技术路线裁决”，继续报告进展、困难、依赖和人工需要。
- Kandev 0.91.0 原生“智能体监控”插件升级到 `0.3.1`：正式接入 Codex `SubagentStart` / `SubagentStop` 和协作工具 Hook，展示实时 Agent、父子/纠偏 DAG 与 4 秒刷新时间轴；Codex app-server 仅补全持久化历史，旧任务不再冒充实时运行。
- 监控插件对停止结果、单/多目标纠偏和重复等待采用可证明语义；快照只保留 ID、枚举状态、时间与派生中文角色，历史同步器不继承 API Key 或 Token。插件只使用 Kandev 正式 Host API 和只读能力，不读取私有 Store/SQLite，也不创建第二套看板。
- 包版本升级到 `0.2.3`，治理/运行/学习 Schema 升级到 `2.3`。本版本通过 200 项全仓回归、17 项监控插件专项测试、Go 服务端测试、静态编译和 Diff 检查。

## [0.2.2] - 2026-08-25（V2.2 意图对齐门禁）

### 开发前意图治理

- 新增“意图确认员”，用 `compile_intent_brief` 编译原始要求、最终结果、验收样例、开发执行器、产品运行时、技术选型、非目标和风险边界。缺具体输入/输出样例或高影响技术选择时阻断。
- 新增“意图检查员”，要求全新只读上下文同时对照原始要求、意图简报、技术调研、拟定契约和验收样例；逐项 coverage 不足或发现目标偷换、遗漏、供应商混淆、未确认默认、验收不可证明时一律 BLOCKED。
- 非低风险契约必须哈希绑定意图简报和检查产物；纯文档/格式类任务可政策豁免，但会记录明确理由。

### 可信开工门禁与可观测性

- 新增 `attest_intent_alignment` / `validate_intent_alignment_attestation`，由可信控制器 HMAC 绑定 intent、inspection、contract、plan 和 external task；不暴露到 MCP。
- `create_run_ledger` 在意图签署缺失、伪造、换契约、换计划或换任务时拒绝创建 owner。运行账本的首个签名事件同时绑定意图和契约答案 attestation。
- 新增 `activate_delivery_handoff`：只有在重放一个全新、ready、签名有效的 ledger 后，才从 owner 蓝图派生 `creation_allowed=true` 的控制平面交接清单。
- `delivery_handoff` 显示中文“意图确认员”和“意图检查员”的状态、进展、困难和人类需要；签署前 owner 蓝图显式 `creation_allowed=false`。

### 德国法律计费反例与接口

- 新增 `examples/legal-billing-intent-source.json`，明确开发执行器为本机已登录 Codex，产品 Demo 运行时为 DeepSeek 官方 API，只从 `DEEPSEEK_API_KEY` 环境变量读取密钥；最终金额由 Decimal 确定性引擎产生。
- 回归固化“用户要 DeepSeek + 必须计算金额，拟契约却变成 Codex CLI + 不算金额”的故意失败案例，验证意图检查会在 outcome、example、technical choice 和 provider coverage 上阻断。
- CLI 新增 `governance intent` 和 `governance inspect-intent`；无状态 MCP 新增 `compile_intent_brief` 和 `compile_intent_inspection`，工具数增至十一，仍无任务创建、批准、签名或外部写操作。
- V2.2 在 Python 3.9+ 无第三方运行依赖约束下通过 188 项全量回归、静态编译和 Diff 检查。

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

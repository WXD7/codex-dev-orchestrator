# V2.1 二次融合实现说明

## 融合原则

V2.1 不是把 A、B 和调研功能简单相加。它以 B/V2 的契约哈希、风险路由、Kandev owner、权限隔离、证据裁决和人工终审为底座，保留 A 中有效的恢复机制，再吸收 Question Ledger、可信观测、影子校准、Bad Case 和 Review Packet。固定 Agent 数量没有增加。

| 保留/吸收 | V2.1 落点 |
| --- | --- |
| B：契约与计划不可静默漂移 | 2.1 契约/计划哈希与完整性重编译校验 |
| B：Kandev 执行面 | `delivery-handoff.json` 继续只描述任务和 Profile 边界 |
| A：原子阶段账本 | 调用方持有的 HMAC 事件链、单写锁、`fsync`、原子替换 |
| A：中断恢复 | replay 签名、事件链、顺序和已完成阶段产物，再找第一个 pending 阶段 |
| A：诚实停止 | 证据缺失、一次返修不收敛、争议和控制面故障进入签名 `honest_stop` |
| 两边共同缺口：Agent 自述驱动状态 | 每阶段硬编码真实 artifact invariants；正常退出没有状态权 |
| 调研：结构化反问 | 高影响 Question Ledger + 人类 delta attestation + 同任务恢复绑定 |
| 调研：过程可观察 | 签名 Agent 心跳、中文短名、进展、困难、依赖和来源 task/session |
| 调研：先学习再执法 | 新/变更 Inspector shadow 校准；达标只获得 blocking 资格 |
| 调研：失败变资产 | 人工确认 Bad Case 编译成隐藏 must-kill，禁止自动晋级 |
| 调研：让人高质量接手 | 从签名 ledger 生成证据寻址 Review Packet |

## 九阶段 V2.1 状态机

1. `environment_preflight`
2. `owner_implementation`
3. `deterministic_ci`
4. `independent_inspection`
5. `adjudication`
6. `single_consolidated_repair`
7. `full_reverification`
8. `blind_final_verification`
9. `human_handoff`

最后状态是 `awaiting_human_decision`，不是自动批准。

## 反问为什么会更准

不确定性现在有四条路，并且每项都带稳定 `decision_id`、影响、关联验收、后果、默认方案、决策者和可逆性：

- `policy_choice`：执行者没有授权，未解决时向人提问；
- `domain_fact`：需要权威证据或专家确认，未解决时向人提问；
- `engineering_invariant`：从仓库、运行环境和测试中证明，不打断人；
- `researchable_fact`：自行研究，不把工具未知伪装成产品问题。

因此“支持范围由谁决定”会问人，“真实 Git 根目录是什么”会预检，“当前 Kandev Profile 长什么样”会调查。低影响且可逆的假设继续执行并保持可见；高影响不可安全默认的问题才暂停。答案不会直接恢复执行，而是形成新契约 delta，经可信控制器 HMAC attestation 绑定同一外部任务和新计划后，owner 才能启动。

## Agent 盯 Agent 的真实结构

非低风险代码默认开启三条互相正交的 fresh-context 只读监察：

1. 契约与领域语义；
2. 状态与信任边界；
3. 测试预言机反证。

安全、数据兼容、浏览器体验、可靠性/成本和高风险对抗只在风险触发时扩展。Inspector 看不到 owner 对话和同伴 findings，不能改代码；它们提交结构化反例与复现包。Adjudicator 按根因跨通道合并成一次返修，原 owner 修复。完整复验后，全新 final verifier 在看不到旧 findings 的情况下再跑 must-kill 反例。

这实现的是“独立反证 → 根因合并 → 原 owner 纠偏 → 全新盲审”，不是多个 Agent 自由聊天。

## 观察、校准与学习闭环

每个真实 Agent 由唯一 `agent_id` 计数，显示中文短名和一句使命；签名心跳持续更新执行状态、进展、当前困难、依赖、人工需要、来源 task/session 与 shadow/blocking 模式。Operator Snapshot 把这些字段投影给 Kandev/LobeHub，且不把“Agent 已结束”误写成“交付已通过”。

基线通道保持既有 blocking 行为，新增或实质变更的 Inspector 必须先 shadow。校准同时检查 Bad Case 召回率、Good Case 误报率、人机一致率、样本量和独立缺陷贡献；少一项不达标就继续 shadow。达标不等于自我批准，只意味着其高置信发现可以参与阻塞。

Bad Case 有 `candidate / confirmed / retired` 生命周期。只有具名人类/领域专家确认并附复现证据的 confirmed Case 才编译为隐藏 must-kill。Owner 看不到隐藏反例，Inspector 和 final verifier 可以使用。Review Packet 最后汇总签名证据、Agent 困难、校准模式和需要人决定的事项，不暴露 control token，也不赋予外部写权限。

## 德国法律计费回放

V2.1 编译后的法律计费契约为 `ready`。领域法源确认被保留为专家路线；typed correction 被路由为仓库工程不变量；Kandev 环境被路由为运行时研究。基础三条监察均启用，计费/隐私/API/UI 风险再扩展安全、数据兼容、真实浏览器、可靠性和对抗通道。

三类证据被设为硬门禁：属性测试、mutation test、真实浏览器 E2E。缺任意一种会进入 `repair_once`，不会因普通单元测试全绿而放行。

上轮 B 的三个失败被固化为 final verifier 的 must-kill：

- 修改审计内容并重算普通哈希仍不得 replay；
- `10000 → 0` 的单字符子串不得充当修正证据；
- Boolean/Integer 类型化修正必须从 UI 到存储和重载完整往返。

对真实 `german-legal-billing-eval-b` worktree 的环境 capsule 已确认：分配目录等于真实 Diff 根、读写权限正常、`git`/`python3` 可解析、无端口要求、无 Git 写锁。

## 明确边界

治理 MCP 仍是无状态且无写工具的。签名 attestation、ledger、进展和 Review Packet 库必须由 Kandev/Symphony 一侧的可信 controller 调用；control token 不能通过 MCP 或 prompt 交给 Agent。V2.1 没有自动 push、merge、deploy、发邮件或自我批准，也没有读取任何模型 API Key。

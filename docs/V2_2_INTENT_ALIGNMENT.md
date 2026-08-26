# V2.2 意图对齐与开工门禁

V2.2 解决的不是“Agent 没有照契约做”，而是更早的失败：契约本身可能已经偏离人的真实意图。因此原有 Question Gate 和三条开发后监察保留，但在 owner 出现之前新增两个明确角色和一个可信签署点。

## 两个开发前角色

### 意图确认员

`compile_intent_brief()` 生成绑定 `intent_hash` 的意图简报，强制展示：

- 原始用户要求和对话引用；
- 最终可观察结果；
- 至少一个具体输入/预期输出样例；
- 开发执行器与产品运行时供应商/模型的独立选择；
- 高影响技术选择、备选方案、理由和证据；
- 非目标、风险边界和未解问题。

这个编译器不能声称人已确认。它只能输出 `ready_for_inspection` 或者返回问题。

### 意图检查员

`compile_intent_inspection()` 编译一个外部 fresh-context 只读检查员的结果。它必须声明上下文 ID、全新、只读、看不到 owner transcript 和同伴 findings，并同时提交：

- 原始要求、意图简报、技术调研、拟定契约和验收样例的输入声明；
- 每个 outcome、example、technical choice、development executor 和 product runtime 的逐项 coverage 证据；
- 目标偷换、需求遗漏、供应商混淆、未确认默认、验收不可证明和调研冲突的结构化 findings。

任一必需输入、coverage 或隔离声明缺失，或任一 finding 仍为 `blocking`，最终一律为 `BLOCKED`。检查员只把问题返给人，不能修改契约后给自己 PASS。

## 三重开工条件

```text
意图简报完整
  → 全新只读意图检查 PASS
  → 人在可信界面确认展示内容
  → 控制器 HMAC 签署 intent + inspection + contract + plan + external task
  → create_run_ledger 允许 owner 蓝图被激活
```

`build_delivery_handoff` 是纯计算工具，它无法看见控制器私有 HMAC 令牌。因此非低风险工作的初始清单把 owner 蓝图标成 `creation_allowed=false`，并显示两个中文角色的进度与困难。可信控制器成功创建可 replay 的 signed ledger 后，`activate_delivery_handoff()` 重放签名创建事件，再派生 `ready_for_control_plane` 且 `creation_allowed=true` 的激活清单。该函数与签署一样不进入 MCP。

HMAC 签署库不进入 MCP，不进入 Agent Prompt，不证明人的身份；身份仍由 LobeHub/Kandev 等人类界面负责。它只证明“持有本地控制令牌的可信控制器曾记录这次确认”。

## 德国法律计费反例

`examples/legal-billing-intent-source.json` 固化了本次最典型的偏差：

- 开发执行器是本机已登录 Codex；
- 产品 Demo 运行时是 DeepSeek 官方 API，只从 `DEEPSEEK_API_KEY` 环境变量取密钥；
- DeepSeek 负责带出处的事实提取和追问；
- 最终金额由 Decimal 确定性计算器产生，受支持样例必须显示具体金额；
- 测试默认使用假适配器，真实 API 费用只由人主动触发。

回归中的故意错误契约把它改成“使用 Codex CLI，不计算金额”。意图检查会同时在 outcome、amount example、deterministic money core 和 product runtime coverage 上阻断，并产生 `provider_confusion` finding。

## 与原有监察的关系

意图确认员和意图检查员属于开发前门禁，不替代开发后的三条默认监察：

- `contract-domain-semantics`：反证产品实现是否符合已确认意图与领域语义；
- `state-trust-boundaries`：检查状态、类型、身份、权限和审计真实性；
- `test-oracle-falsification`：用突变、属性、边界和假模拟反证测试能否抓住错误代码。

结果是一条贯通的证据链：人要什么 → 独立检查有没有被改写 → 人确认 → 实现是否正确 → 测试是否真能证伪 → 最终盲验。

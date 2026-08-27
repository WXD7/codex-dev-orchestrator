# 新 Codex 对话启用驾驶员接管系统

这是给人和任意全新 Codex 对话的首要入口。这套机制不依赖某一个旧对话的
记忆；它先绑定唯一 Git 项目，再从该项目的版本化状态和证据恢复驾驶员。

## 最简单的启用方法

1. 在 Codex 中打开或选中要继续的**精确 Git 项目根目录**。
2. 新建对话。Codex 会自动读取该项目根的 AGENTS.md。
3. 把下面这段话作为新对话的第一条指令：

   ~~~text
   请启用 AI Delivery Governance 的无历史驾驶员接管。只绑定当前这一个
   Git 项目；先按 AGENTS.md 运行只读 governance takeover，只有
   ready_for_takeover: true 时才按 required_read_order 读取项目材料。
   不得从旧对话、最近 UI 状态或兄弟仓库猜测项目事实。先向我报告项目 ID、
   当前状态、已验证证据、已有未提交改动、下一步和仍需人决定的事项，然后
   再开始开发。
   ~~~

4. 如果输出是 ready_for_takeover: false，不要让新 Codex 自行修改状态后给
   自己放行；先检查它报告的 Git、哈希、历史标记或项目身份冲突。

## 在当前工作区中使用

工作区根的 AGENTS.md 只是纯路由器。它不保存任何产品状态，只负责确定唯一
项目。可版本化的模板在：

~~~text
orchestrator/templates/WORKSPACE_ROUTER_AGENTS.md
~~~

在新机器或新工作区中，可把该模板复制为工作区根的 AGENTS.md。不要在这个
文件中硬编码任何具体产品的需求、Prompt、金标或当前状态。

## 手工验证一个已接入项目

在治理仓库根目录运行：

~~~bash
python3 run.py governance takeover --target /absolute/path/to/exact/git/repository
~~~

正常结果应同时满足：

- ready_for_takeover: true；
- project_id 只对应所选项目；
- 所有 blocking check 为通过；
- 工作树不干净时只警告并要求保留；
- 命令没有读取密钥值、没有调用产品模型、没有搜索兄弟项目。

## 首次把一个新项目接入这套系统

1. 确认目标是一个已有 commit 的 Git 仓库。
2. 完成该项目的意图、技术调研和契约输入，再运行：

   ~~~bash
   python3 run.py governance init --target /absolute/path/to/exact/git/repository --input /path/to/project-contract-source.json
   ~~~

3. 在目标项目根的 AGENTS.md 中增加开工门：先运行 governance takeover，
   成功后才读项目材料和修改代码。
4. 核对新生成的 .ai-delivery/CURRENT_STATE.json、EVIDENCE_INDEX.json 和
   DRIVER_BOOTSTRAP.md。
5. 运行一次 takeover，再用一个 ephemeral、只读、无旧历史的 Codex 会话做
   冷启动验收。
6. 把上述项目内状态和证据提交到**该项目自己的 Git 仓库**。

## 信息只能放在哪里

| 信息 | 应当保存的位置 |
| --- | --- |
| 通用治理代码、隔离规则、匿名失败模式、评测方法 | 本治理仓库 |
| 一个项目的需求、技术选择、Prompt、证据、预期输出、Case 和当前状态 | 该项目仓库 |
| 工作区存在哪些项目及如何选择 | 工作区根的纯路由 AGENTS.md |
| 密钥值、Token、真实客户材料 | 不进入接管包、Prompt、日志或 Git |

更完整的信任边界见 docs/V2_4_PROJECT_CONTINUITY.md。

# Codex 智能体监控桥

这个 Codex 伴生插件把任意项目中的子 Agent 生命周期交给本机已安装的 Kandev `ai-delivery-agent-observer` 接收器。它解决项目级 `.codex/hooks.json` 只在当前仓库配置层生效、从上级目录或其他仓库启动时事件源消失的问题。

## 安全边界

- 插件只调用 Kandev 激活清单 `~/.kandev/plugins/ai-delivery-agent-observer.yml` 明确指向、协议匹配且接收器 SHA-256 校验通过的版本；旧包即使版本更高也不会被误选。
- 接收器子进程只获得 `HOME`、固定 `PATH`、`LANG` 和 `PYTHONDONTWRITEBYTECODE`；不会继承 `DEEPSEEK_API_KEY` 或其他凭据。
- Hook 原始事件只在内存中交给本机接收器。Kandev 接收器仅持久化 ID、事件枚举、状态、派生中文角色和时间，丢弃 Prompt、消息正文、工具输入、工具输出与 transcript。
- 找不到有效接收器、接收器超时或失败时，插件安静失败并向 Codex 返回空 JSON，不注入错误或接收器输出。
- 安装或启用插件不会自动信任 Hook；必须由人在 `/hooks` 审阅当前 Hook 哈希。不要使用信任绕过参数。

## 启用与验收

1. 先安装并启用 Kandev 智能体监控 `0.4.2` 或更高兼容版本。
2. 把本目录作为本地 Codex 插件安装并启用。
3. 将目标 Kandev 工作区写入 `~/.kandev/plugins/ai-delivery-agent-observer/data/codex-hook-binding.json`；文件协议为 `codex-hook-workspace-binding/v1`，只包含 `kandev_workspace_id`，也可用 `KANDEV_AGENT_OBSERVER_WORKSPACE_ID` 环境变量显式覆盖。
4. 打开 `/hooks`：
   - `Installed 0 / Active 0`：当前任务没有发现该 Hook；常见原因是插件未安装、未启用，或任务尚未重新加载。
   - `Installed 1 / Active 0 / Review 1`：Hook 已发现，正在等待人审阅信任。
   - `Active 1`：当前 Hook 定义已受信任并会运行。
5. 新建一个范围很小的 Codex 任务，创建一个无网络、无密钥、无文件修改的子 Agent 探针；在 Kandev `/ai-delivery-observer` 确认 `SessionStart`、`SubagentStart`、等待或纠偏、`SubagentStop`、`SessionEnd` 均显示为“Codex 实时 Hook”。
6. 刷新 Kandev 页面，确认根任务分组、活跃任务计数、父子/纠偏 DAG、时间轴和停止状态仍可恢复。`SubagentStop` 只证明生命周期停止，不等于交付通过。

Hook 是事件驱动通道，不是长连接。页面显示 `idle` 且长时间没有新事件是正常的；只有投递器明确写入 `delivery_failed` 才代表最近一次真实投递失败。仍标记为活跃的运行若连续 15 分钟没有生命周期或工具事件，会保守标为陈旧，避免应用异常退出后留下永久“工作中”。

项目级 `.codex/hooks.json` 仍可用于单仓库定制或降级诊断，但不应再作为跨项目监控的唯一事件源。伴生插件和项目 Hook 同时启用时会同时运行，因此迁移完成后应避免保留重复定义。

## 开发验证

```bash
python3 -m unittest -v tests.test_codex_agent_observer_plugin
python3 /path/to/plugin-creator/scripts/validate_plugin.py plugins/codex-agent-observer-bridge
```

(function () {
  var cleanup = function () {};
  window.registerKandevPlugin("ai-delivery-agent-observer", {
    initialize: function (registry, host) {
      var React = host.React;
      var jsx = host.jsx;
      var disposed = false;
      var pollTimer = null;
      var entries = new Map();
      var controllers = new Set();
      var POLL_INTERVAL_MS = 4000;

      var executionLabels = {
        working: "工作中",
        queued: "待启动",
        waiting_on_human: "需人处理",
        waiting_on_dependency: "等待依赖",
        stopped: "已停止（结果未知）",
        finished: "执行完成",
        failed: "执行失败",
        interrupted: "已中断",
        historical: "历史记录",
      };
      var deliveryLabels = {
        not_submitted: "尚未送审",
        awaiting_review: "等待评审",
        accepted: "评审通过",
        changes_required: "需要修改",
      };

      function entryFor(key) {
        var existing = entries.get(key);
        if (existing) return existing;
        var entry = {
          key: key,
          data: null,
          loading: false,
          error: null,
          listeners: new Set(),
          refs: 0,
          inflight: null,
        };
        entries.set(key, entry);
        return entry;
      }

      function queryForKey(key) {
        if (key.indexOf("task:") === 0) {
          return "task_id=" + encodeURIComponent(key.slice(5));
        }
        return "workspace_id=" + encodeURIComponent(key);
      }

      function notify(entry) {
        entry.listeners.forEach(function (listener) {
          listener();
        });
      }

      function loadSnapshot(key) {
        var entry = entryFor(key);
        if (entry.inflight || disposed) return entry.inflight || Promise.resolve();
        var controller = new AbortController();
        controllers.add(controller);
        entry.loading = entry.data === null;
        entry.error = null;
        notify(entry);
        entry.inflight = host.api
          .fetch("webhooks/snapshot?" + queryForKey(key), {
            method: "GET",
            headers: { Accept: "application/json" },
            signal: controller.signal,
          })
          .then(function (response) {
            if (!response.ok) {
              return response.text().then(function (body) {
                throw new Error(body || "监控数据读取失败（" + response.status + "）");
              });
            }
            return response.json();
          })
          .then(function (data) {
            if (!disposed) entry.data = data;
          })
          .catch(function (error) {
            if (!controller.signal.aborted && !disposed) {
              entry.error = error instanceof Error ? error.message : "监控数据读取失败";
            }
          })
          .finally(function () {
            controllers.delete(controller);
            entry.loading = false;
            entry.inflight = null;
            if (!disposed) notify(entry);
          });
        return entry.inflight;
      }

      function pollActiveEntries() {
        entries.forEach(function (entry) {
          if (entry.refs > 0) loadSnapshot(entry.key);
        });
      }

      function ensurePoller() {
        if (!pollTimer) pollTimer = setInterval(pollActiveEntries, POLL_INTERVAL_MS);
      }

      function subscribe(key, listener) {
        var entry = entryFor(key);
        entry.refs += 1;
        entry.listeners.add(listener);
        ensurePoller();
        loadSnapshot(key);
        return function () {
          entry.listeners.delete(listener);
          entry.refs = Math.max(0, entry.refs - 1);
        };
      }

      function useSnapshot(key) {
        var updateState = React.useState(0);
        var forceUpdate = updateState[1];
        React.useEffect(
          function () {
            if (!key) return undefined;
            return subscribe(key, function () {
              forceUpdate(function (value) {
                return value + 1;
              });
            });
          },
          [key],
        );
        return key ? entryFor(key) : { data: null, loading: false, error: null };
      }

      function useActiveWorkspace() {
        var state = React.useState(function () {
          return host.context.getActiveWorkspaceId();
        });
        var workspaceId = state[0];
        var setWorkspaceId = state[1];
        React.useEffect(function () {
          return host.context.subscribeActiveWorkspace(setWorkspaceId);
        }, []);
        return workspaceId;
      }

      function agentForTask(snapshot, taskId) {
        if (!snapshot || !snapshot.agents) return null;
        for (var i = 0; i < snapshot.agents.length; i += 1) {
          if (snapshot.agents[i].source.task_id === taskId) return snapshot.agents[i];
        }
        return null;
      }

      function executionLabel(state) {
        return executionLabels[state] || "状态未知";
      }

      function deliveryLabel(verdict) {
        return deliveryLabels[verdict] || "评审未知";
      }

      function deriveView(agents) {
        var list = Array.isArray(agents) ? agents : [];
        var needsHuman = [];
        var failed = [];
        var working = [];
        var waiting = [];
        var stopped = [];
        var finished = [];
        var roles = new Set();
        var stale = 0;
        list.forEach(function (agent) {
          roles.add(agent.display_name || "执行智能体");
          if (
            agent.source_quality === "structured_agent_heartbeat" &&
            agent.heartbeat_health === "stale"
          ) {
            stale += 1;
          }
          if (agent.needs_human || agent.execution_state === "waiting_on_human") {
            needsHuman.push(agent);
          } else if (agent.execution_state === "failed" || agent.execution_state === "interrupted") {
            failed.push(agent);
          } else if (agent.execution_state === "working") {
            working.push(agent);
          } else if (
            agent.execution_state === "waiting_on_dependency" ||
            agent.execution_state === "queued"
          ) {
            waiting.push(agent);
          } else if (agent.execution_state === "stopped") {
            stopped.push(agent);
          } else if (agent.execution_state === "finished") {
            finished.push(agent);
          }
        });
        return {
          needsHuman: needsHuman,
          failed: failed,
          working: working,
          waiting: waiting,
          stopped: stopped,
          finished: finished,
          summary: {
            total_tasks: list.length,
            unique_roles: roles.size,
            working: working.length,
            waiting_on_human: needsHuman.length,
            waiting_on_dependency: waiting.length,
            stopped: stopped.length,
            finished: finished.length,
            failed: failed.length,
            stale: stale,
          },
        };
      }

      function relativeTime(value) {
        if (!value) return "无心跳";
        try {
          return host.utils.formatRelativeTime(value);
        } catch (_error) {
          return value;
        }
      }

      function shortId(value) {
        var text = String(value || "");
        if (!text) return "未报告";
        return text.length > 13 ? "…" + text.slice(-12) : text;
      }

      function rootLabel(value) {
        return value ? "Codex 任务 " + shortId(value) : "未归属根任务";
      }

      function groupAgentsByRoot(agents) {
        var groups = new Map();
        (agents || []).forEach(function (agent) {
          var root = agent.root_thread_id || "";
          if (!groups.has(root)) groups.set(root, []);
          groups.get(root).push(agent);
        });
        return Array.from(groups.entries()).map(function (entry) {
          return { root: entry[0], agents: entry[1] };
        });
      }

      function StateBadge(props) {
        return jsx(
          "span",
          {
            className: "ao-badge ao-state-" + String(props.state || "unknown"),
            "data-agent-state": props.state || "unknown",
          },
          props.label || executionLabel(props.state),
        );
      }

      function SourceBadge(props) {
        var labels = {
          structured_agent_heartbeat: "智能体心跳",
          kandev_inference: "Kandev 历史推断",
          kandev_agent_stream_exact: "Kandev 原生事件",
          codex_hooks_realtime: "Codex 实时 Hook",
          codex_app_server_history: "Codex 持久化历史",
        };
        var exact =
          props.quality === "kandev_agent_stream_exact" ||
          props.quality === "codex_hooks_realtime" ||
          props.quality === "structured_agent_heartbeat";
        return jsx(
          "span",
          { className: "ao-source " + (exact ? "" : "ao-source-inferred") },
          labels[props.quality] || "来源未知",
        );
      }

      function DetailRow(props) {
        return jsx(
          "div",
          { className: "ao-detail-row" },
          jsx("span", { className: "ao-detail-label" }, props.label),
          jsx("span", { className: "ao-detail-value" }, props.value || "未报告"),
        );
      }

      function AgentCard(props) {
        var agent = props.agent;
        var compact = Boolean(props.compact);
        var structured = agent.source_quality === "structured_agent_heartbeat";
        var taskId = (agent.source && agent.source.task_id) || agent.task_id || "";
        var hasTask = Boolean(taskId);
        function openTask() {
          if (hasTask) host.navigate("/t/" + taskId);
        }
        return jsx(
          "article",
          {
            className:
              "ao-agent-card ao-card-state-" + agent.execution_state + (compact ? " ao-agent-card-compact" : ""),
            "data-testid": "agent-observer-card",
            "data-task-id": taskId,
          },
          jsx(
            "div",
            { className: "ao-agent-head" },
            jsx(
              hasTask ? "button" : "div",
              hasTask ? { type: "button", className: "ao-agent-title", onClick: openTask } : { className: "ao-agent-title" },
              jsx("span", { className: "ao-agent-name" }, agent.display_name),
              jsx("span", { className: "ao-agent-mission" }, agent.mission),
            ),
            jsx(
              "div",
              { className: "ao-badge-row" },
              jsx(StateBadge, { state: agent.execution_state }),
              agent.delivery_verdict
                ? jsx("span", { className: "ao-badge ao-delivery" }, deliveryLabel(agent.delivery_verdict))
                : null,
            ),
          ),
          jsx("p", { className: "ao-progress" }, agent.progress_summary || "尚无进展摘要"),
          compact
            ? null
            : jsx(
                "div",
                { className: "ao-detail-grid" },
                jsx(DetailRow, { label: "当前困难", value: agent.current_difficulty }),
                jsx(DetailRow, {
                  label: agent.parent_agent_id ? "上级 Agent" : "依赖",
                  value: agent.parent_agent_id ? shortId(agent.parent_agent_id) : agent.dependency,
                }),
                jsx(DetailRow, {
                  label: agent.agent_id ? "Agent 标识" : "下一步",
                  value: agent.agent_id ? shortId(agent.agent_id) : agent.next_step,
                }),
                agent.root_thread_id
                  ? jsx(DetailRow, { label: "所属 Codex 任务", value: shortId(agent.root_thread_id) })
                  : null,
                jsx(DetailRow, {
                  label: agent.delivery_verdict ? "执行 / 交付" : "状态证据",
                  value: agent.delivery_verdict
                    ? executionLabel(agent.execution_state) + " / " + deliveryLabel(agent.delivery_verdict)
                    : executionLabel(agent.execution_state) + " / 真实事件",
                }),
              ),
          jsx(
            "footer",
            { className: "ao-agent-footer" },
            jsx(SourceBadge, { quality: agent.source_quality }),
            jsx(
              "span",
              null,
              (structured ? "心跳 " : "更新 ") + relativeTime(agent.last_activity_at || agent.last_heartbeat_at),
            ),
            structured && agent.heartbeat_health === "stale"
              ? jsx("span", { className: "ao-stale" }, "心跳已过期")
              : null,
            hasTask
              ? jsx("button", { type: "button", className: "ao-task-link", onClick: openTask }, "打开任务")
              : null,
          ),
        );
      }

      function StatCard(props) {
        return jsx(
          "div",
          { className: "ao-stat " + (props.attention ? "ao-stat-attention" : "") },
          jsx("span", { className: "ao-stat-value" }, String(props.value || 0)),
          jsx("span", { className: "ao-stat-label" }, props.label),
        );
      }

      function Group(props) {
        return jsx(
          "section",
          { className: "ao-group" },
          jsx(
            "div",
            { className: "ao-group-heading" },
            jsx("h2", null, props.title),
            jsx("span", { className: "ao-group-count" }, String(props.agents.length)),
          ),
          props.agents.length
            ? jsx(
                "div",
                { className: "ao-agent-grid" },
                props.agents.map(function (agent) {
                  return jsx(AgentCard, { key: agent.agent_id || (agent.source && agent.source.task_id), agent: agent });
                }),
              )
            : jsx("div", { className: "ao-empty-group" }, props.empty || "当前没有智能体"),
        );
      }

      function LoadingState() {
        return jsx(
          "div",
          { className: "ao-loading", "data-testid": "agent-observer-loading" },
          jsx("span", { className: "ao-pulse" }),
          "正在读取 Kandev 与 Codex 的真实 Agent 事件…",
        );
      }

      function ErrorState(props) {
        return jsx(
          "div",
          { className: "ao-error", role: "alert", "data-testid": "agent-observer-error" },
          jsx("strong", null, "监控数据暂时不可用"),
          jsx("span", null, props.message),
          jsx(
            "button",
            { type: "button", onClick: props.retry },
            "重试",
          ),
        );
      }

      function BridgeBanner(props) {
        var bridge = props.bridge || { state: "unavailable" };
        var labels = {
          active: "正在接收活跃任务事件",
          idle: "事件桥已就绪，目前没有活跃任务",
          history_synced: "Codex 持久化历史已同步",
          stale: "数据通道需要检查",
          delivery_failed: "最近一次 Hook 投递失败",
          workspace_unbound: "尚未绑定 Kandev 工作区",
          unavailable: "数据通道尚未启用",
          out_of_scope: "数据通道绑定了其他工作区",
          not_applicable: "当前视图不使用此数据通道",
        };
        var statusLabel = labels[bridge.state] || "数据通道状态未知";
        var detail = bridge.error ||
          (bridge.last_success_at ? "最近事件：" + relativeTime(bridge.last_success_at) : "等待第一份真实事件快照");
        var runDetail = bridge.source === "codex_hooks"
          ? " · 活跃 " + String(bridge.active_runs || 0) + " / 已见 " + String(bridge.run_count || 0) + " 个 Codex 任务"
          : "";
        return jsx(
          "section",
          { className: "ao-bridge ao-bridge-" + String(bridge.state || "unavailable"), role: "status" },
          jsx("span", { className: "ao-bridge-light" }),
          jsx(
            "div",
            { className: "ao-bridge-copy" },
            jsx("strong", null, props.title || "Codex 数据通道"),
            jsx("span", null, statusLabel + runDetail),
            jsx("span", null, detail),
          ),
          bridge.root_thread_id
            ? jsx("span", { className: "ao-bridge-root" }, "主任务 " + shortId(bridge.root_thread_id))
            : null,
        );
      }

      function NativeSourceGroup(props) {
        var grouped = props.groupByRoot ? groupAgentsByRoot(props.agents) : [];
        return jsx(
          "section",
          { className: "ao-native-source" },
          jsx(
            "div",
            { className: "ao-native-heading" },
            jsx("div", null, jsx("h2", null, props.title), jsx("p", null, props.description)),
            jsx("span", { className: "ao-group-count" }, String(props.agents.length)),
          ),
          props.agents.length && props.groupByRoot
            ? jsx(
                "div",
                { className: "ao-run-groups" },
                grouped.map(function (group) {
                  var activeCount = deriveView(group.agents).summary.working;
                  var waitingCount = deriveView(group.agents).summary.waiting_on_human;
                  return jsx(
                    "section",
                    { className: "ao-run-group", key: group.root || "unknown-root" },
                    jsx(
                      "div",
                      { className: "ao-run-heading" },
                      jsx("strong", null, rootLabel(group.root)),
                      jsx("span", null, String(group.agents.length) + " Agent · " + String(activeCount) + " 工作 · " + String(waitingCount) + " 等你"),
                    ),
                    jsx(
                      "div",
                      { className: "ao-agent-grid" },
                      group.agents.map(function (agent) {
                        return jsx(AgentCard, { key: (agent.root_thread_id || "") + ":" + agent.agent_id, agent: agent });
                      }),
                    ),
                  );
                }),
              )
            : props.agents.length
            ? jsx(
                "div",
                { className: "ao-agent-grid" },
                props.agents.map(function (agent) {
                  return jsx(AgentCard, { key: (agent.root_thread_id || "") + ":" + agent.agent_id, agent: agent });
                }),
              )
            : jsx("div", { className: "ao-empty-group" }, props.empty),
        );
      }

      function DagView(props) {
        var allAgents = (props.kandevAgents || []).concat(props.codexAgents || []).concat(props.historyAgents || []);
        var names = new Map();
        allAgents.forEach(function (agent) { names.set((agent.root_thread_id || "") + ":" + agent.agent_id, agent.display_name); });
        var bridgeRoot = props.bridge && props.bridge.root_thread_id;
        function agentName(id, root) {
          var scoped = (root || "") + ":" + id;
          if (names.has(scoped)) return names.get(scoped);
          if (id && (id === root || id === bridgeRoot)) return "当前主任务";
          return id ? "上级 Agent " + shortId(id) : "未知上级";
        }
        var edges = (props.edges || []).filter(function (edge) {
          return edge.edge_type === "spawn" || edge.edge_type === "correction" || edge.edge_type === "resume" || edge.edge_type === "close";
        }).slice(-80);
        var edgeLabels = { spawn: "创建", correction: "纠偏", resume: "恢复", close: "关闭" };
        return jsx(
          "section",
          { className: "ao-flow-section", "data-testid": "agent-observer-dag" },
          jsx("div", { className: "ao-section-title" }, jsx("h2", null, "Agent 父子与纠偏 DAG"), jsx("span", null, "只展示真实关系事件")),
          edges.length
            ? jsx(
                "div",
                { className: "ao-dag" },
                edges.map(function (edge) {
                  return jsx(
                    "div",
                    { className: "ao-dag-row ao-dag-" + edge.edge_type, key: (edge.root_thread_id || "") + ":" + edge.edge_id },
                    jsx("span", { className: "ao-run-chip" }, rootLabel(edge.root_thread_id)),
                    jsx("span", { className: "ao-dag-node" }, agentName(edge.from_agent_id, edge.root_thread_id)),
                    jsx(
                      "span",
                      { className: "ao-dag-arrow" },
                      jsx("span", null, edgeLabels[edge.edge_type] || edge.edge_type),
                      "→",
                    ),
                    jsx(
                      "span",
                      { className: "ao-dag-targets" },
                      (edge.to_agent_ids || []).map(function (id) {
                        return jsx("span", { className: "ao-dag-node", key: id }, agentName(id, edge.root_thread_id));
                      }),
                    ),
                    jsx(SourceBadge, { quality: edge.source_quality }),
                  );
                }),
              )
            : jsx("div", { className: "ao-empty-group" }, "尚未收到可确认的父子或纠偏事件。"),
        );
      }

      function TimelineView(props) {
        var eventLabels = {
          spawn: "创建子 Agent", correction: "上级纠偏", resume: "恢复工作", wait: "等待结果",
          close: "关闭 Agent", activity: "Agent 活动", stopped: "已停止（结果未知）", finished: "执行完成", failed: "执行失败",
          interrupted: "执行中断", state: "状态变化", permission: "等待权限审批",
          session_start: "主任务开始 / 恢复", session_end: "主任务结束",
        };
        var events = (props.events || []).slice(-40).reverse();
        return jsx(
          "section",
          { className: "ao-flow-section", "data-testid": "agent-observer-timeline" },
          jsx("div", { className: "ao-section-title" }, jsx("h2", null, "协作时间轴"), jsx("span", null, "最近 " + events.length + " 条真实事件")),
          events.length
            ? jsx(
                "ol",
                { className: "ao-timeline" },
                events.map(function (event) {
                  return jsx(
                    "li",
                    { key: (event.root_thread_id || "") + ":" + event.event_id, className: "ao-timeline-item" },
                    jsx("span", { className: "ao-timeline-dot ao-dot-event-" + event.event_type }),
                    jsx(
                      "div",
                      { className: "ao-timeline-body" },
                      jsx(
                        "div",
                        { className: "ao-timeline-title" },
                        jsx("strong", null, eventLabels[event.event_type] || event.event_type || "协作事件"),
                        jsx(SourceBadge, { quality: event.source_quality }),
                        jsx("time", null, relativeTime(event.observed_at)),
                      ),
                      event.root_thread_id
                        ? jsx("span", { className: "ao-run-chip ao-timeline-run" }, rootLabel(event.root_thread_id))
                        : null,
                      jsx(
                        "p",
                        null,
                        (event.summary || "状态已更新") +
                          (event.repeat_count > 1 ? "（同类事件累计 " + event.repeat_count + " 次，已折叠）" : ""),
                      ),
                      (event.target_agent_ids || []).length
                        ? jsx("span", { className: "ao-timeline-target" }, "目标 " + (event.target_agent_ids || []).map(shortId).join("、"))
                        : null,
                    ),
                  );
                }),
              )
            : jsx("div", { className: "ao-empty-group" }, "尚未收到协作时间轴事件。"),
        );
      }

      function ObserverPage() {
        var workspaceId = useActiveWorkspace();
        var state = useSnapshot(workspaceId || "");
        if (!workspaceId) {
          return jsx("main", { className: "ao-page" }, jsx("div", { className: "ao-empty-page" }, "请先选择一个工作区。"));
        }
        if (!state.data && state.loading) {
          return jsx("main", { className: "ao-page" }, jsx(LoadingState));
        }
        if (!state.data && state.error) {
          return jsx(
            "main",
            { className: "ao-page" },
            jsx(ErrorState, { message: state.error, retry: function () { loadSnapshot(workspaceId); } }),
          );
        }
        var data = state.data || { summary: {}, agents: [], kandev_native_agents: [], codex_native_agents: [], codex_history_agents: [] };
        var inferredAgents = data.agents || [];
        var kandevAgents = data.kandev_native_agents || [];
        var codexAgents = data.codex_native_agents || [];
        var codexHistoryAgents = data.codex_history_agents || [];
        var inferredView = deriveView(inferredAgents);
        var exactView = deriveView(kandevAgents.concat(codexAgents));
        var summary = data.summary || {};
        return jsx(
          "main",
          { className: "ao-page", "data-testid": "agent-observer-page" },
          jsx(
            "header",
            { className: "ao-page-intro" },
            jsx(
              "div",
              null,
              jsx("h1", null, "智能体运行总览"),
              jsx("p", null, "实时 Agent、父子 DAG、纠偏和协作时间轴；每 4 秒自动刷新。"),
            ),
            jsx(
              "button",
              {
                type: "button",
                className: "ao-refresh",
                disabled: Boolean(state.loading),
                onClick: function () { loadSnapshot(workspaceId); },
              },
              state.loading ? "刷新中…" : "立即刷新",
            ),
          ),
          state.error
            ? jsx("div", { className: "ao-inline-warning" }, "上次刷新失败，正在保留最近一次快照：" + state.error)
            : null,
          jsx(
            "div",
            { className: "ao-bridge-grid" },
            jsx(BridgeBanner, { bridge: data.bridge, title: "Codex 实时 Hook" }),
            jsx(BridgeBanner, { bridge: data.history_bridge, title: "Codex 持久化历史" }),
          ),
          jsx(
            "div",
            { className: "ao-scope-note", role: "note" },
            "统计口径：只有 Kandev 原生事件和 Codex 实时 Hook 计入“真实工作中”；Hook 是事件驱动通道，不是长连接，空闲且很久没有新事件是正常状态。只有仍声称活跃的任务连续 15 分钟没有生命周期或工具事件时才标为陈旧。Codex app-server 仅补全持久化历史；历史存在绝不冒充实时运行。",
          ),
          jsx(
            "section",
            { className: "ao-stats", "aria-label": "智能体统计" },
            jsx(StatCard, { label: "实时 Agent", value: kandevAgents.length + codexAgents.length }),
            jsx(StatCard, { label: "Kandev 原生", value: kandevAgents.length }),
            jsx(StatCard, { label: "Codex 实时", value: codexAgents.length }),
            jsx(StatCard, { label: "活跃 Codex 任务", value: (data.bridge && data.bridge.active_runs) || 0 }),
            jsx(StatCard, { label: "Codex 历史", value: codexHistoryAgents.length }),
            jsx(StatCard, { label: "真实工作中", value: exactView.summary.working }),
            jsx(StatCard, { label: "已停止待核验", value: exactView.summary.stopped }),
            jsx(StatCard, { label: "真实失败 / 中断", value: exactView.summary.failed, attention: exactView.summary.failed > 0 }),
            jsx(StatCard, { label: "上级纠偏", value: summary.corrections || 0, attention: (summary.corrections || 0) > 0 }),
            jsx(StatCard, { label: "协作事件", value: summary.timeline_events || 0 }),
            jsx(StatCard, { label: "历史任务推断", value: inferredAgents.length }),
          ),
          jsx(
            "section",
            { className: "ao-section-block" },
            jsx("div", { className: "ao-section-title ao-section-title-major" }, jsx("h2", null, "真实 Agent 实时状态"), jsx("span", null, "只使用实时事件通道")),
            jsx(NativeSourceGroup, {
              title: "Kandev 内启动的 Agent", agents: kandevAgents,
              description: "来自 agent.stream.* 的结构化子 Agent 事件。",
              empty: "本工作区尚未由 Kandev ACP 启动可识别的子 Agent。",
            }),
            jsx(NativeSourceGroup, {
              title: "Codex Desktop 原生子 Agent", agents: codexAgents,
              description: "来自 SubagentStart/Stop 与协作工具 Hook 的实时生命周期。",
              empty: data.bridge && (data.bridge.state === "active" || data.bridge.state === "idle") ? "当前还没有收到子 Agent 生命周期事件。" : "启用 Codex Hook 后显示实时子 Agent。",
              groupByRoot: true,
            }),
          ),
          jsx(
            "section",
            { className: "ao-inference-section ao-history-section" },
            jsx("div", { className: "ao-section-title ao-section-title-major" }, jsx("h2", null, "Codex 持久化历史"), jsx("span", null, "补全任务树，不代表当前运行")),
            jsx(NativeSourceGroup, {
              title: "历史子 Agent", agents: codexHistoryAgents,
              description: "独立 app-server 读取的持久化父子关系与协作记录；状态统一标为历史。",
              empty: "尚未同步 Codex 持久化历史。",
            }),
          ),
          jsx(DagView, { kandevAgents: kandevAgents, codexAgents: codexAgents, historyAgents: codexHistoryAgents, edges: data.edges || [], bridge: data.bridge || {} }),
          jsx(TimelineView, { events: data.timeline || [] }),
          jsx(
            "section",
            { className: "ao-inference-section" },
            jsx("div", { className: "ao-section-title ao-section-title-major" }, jsx("h2", null, "Kandev 任务与历史推断"), jsx("span", null, "辅助观察，不代表真实 Agent 生命周期")),
            jsx(Group, { title: "需要你处理", agents: inferredView.needsHuman, empty: "当前没有等待你确认的任务" }),
            jsx(Group, { title: "推断失败", agents: inferredView.failed, empty: "当前没有推断失败" }),
            jsx(Group, { title: "推断工作中", agents: inferredView.working, empty: "当前没有推断为工作中的任务" }),
            jsx(Group, { title: "等待与待启动", agents: inferredView.waiting, empty: "当前没有等待中的任务" }),
            jsx(Group, { title: "执行已完成", agents: inferredView.finished, empty: "当前没有执行完成的任务" }),
          ),
        );
      }

      function CardTags(props) {
        var slotProps = props.slotProps || {};
        var state = useSnapshot(slotProps.workspaceId || (slotProps.taskId ? "task:" + slotProps.taskId : ""));
        var agent = agentForTask(state.data, slotProps.taskId);
        if (!agent) return null;
        return jsx(
          "div",
          { className: "ao-card-tags", "data-testid": "agent-observer-card-tags" },
          jsx("span", { className: "ao-role-tag" }, agent.display_name),
          jsx(StateBadge, { state: agent.execution_state }),
        );
      }

      function ChatTopBar(props) {
        var slotProps = props.slotProps || {};
        var key = slotProps.workspaceId || (slotProps.taskId ? "task:" + slotProps.taskId : "");
        var state = useSnapshot(key);
        var agent = agentForTask(state.data, slotProps.taskId);
        if (!agent) return null;
        return jsx(
          "button",
          {
            type: "button",
            className: "ao-topbar-task",
            onClick: function () { host.navigate("/ai-delivery-observer"); },
            title: agent.progress_summary,
            "data-testid": "agent-observer-chat-status",
          },
          jsx("span", { className: "ao-topbar-dot ao-dot-" + agent.execution_state }),
          jsx("span", { className: "ao-topbar-role" }, agent.display_name),
          jsx("span", { className: "ao-topbar-state" }, executionLabel(agent.execution_state)),
        );
      }

      function MainTopBar(props) {
        var slotProps = props.slotProps || {};
        var state = useSnapshot(slotProps.workspaceId || "");
        if (!state.data) return null;
        var exactAgents = (state.data.kandev_native_agents || []).concat(state.data.codex_native_agents || []);
        var summary = deriveView(exactAgents).summary;
        return jsx(
          "button",
          {
            type: "button",
            className: "ao-main-topbar",
            onClick: function () { host.navigate("/ai-delivery-observer"); },
            "data-testid": "agent-observer-main-status",
            title: "打开智能体监控",
          },
          jsx("span", { className: "ao-live-dot" + ((summary.working || 0) > 0 ? " ao-live-dot-active" : "") }),
          String(exactAgents.length) + " 真实 Agent · " + String(summary.working || 0) + " 工作 · " + String((state.data.bridge && state.data.bridge.active_runs) || 0) + " 活跃任务",
        );
      }

      function TaskPanel(props) {
        var workspaceId = useActiveWorkspace();
        var state = useSnapshot(workspaceId || "task:" + props.taskId);
        var agent = agentForTask(state.data, props.taskId);
        if (!agent && state.loading) return jsx("div", { className: "ao-panel" }, jsx(LoadingState));
        if (!agent && state.error) {
          return jsx("div", { className: "ao-panel" }, jsx(ErrorState, { message: state.error, retry: function () { loadSnapshot(workspaceId || "task:" + props.taskId); } }));
        }
        if (!agent) return jsx("div", { className: "ao-panel ao-empty-page" }, "当前任务还没有可识别的智能体状态。" );
        return jsx(
          "div",
          { className: "ao-panel", "data-testid": "agent-observer-task-panel" },
          jsx(AgentCard, { agent: agent }),
        );
      }

      registry.registerRoute("/ai-delivery-observer", ObserverPage, {
        topbar: { title: "智能体监控", subtitle: "多智能体执行与交付状态", icon: "robot" },
      });
      registry.registerNavItem({
        id: "agent-observer",
        label: "智能体监控",
        path: "/ai-delivery-observer",
        icon: "robot",
        section: "main",
      });
      registry.registerComponent("main-top-bar", MainTopBar);
      registry.registerComponent("chat-top-bar", ChatTopBar);
      registry.registerComponent("task-card-tags", CardTags);
      registry.registerTaskPanel({
        id: "agent-progress",
        title: "智能体进展",
        icon: "robot",
        Component: TaskPanel,
        mobileEnabled: true,
      });
      registry.registerTaskMenuAction({
        id: "view-agent-status",
        label: "查看智能体状态",
        icon: "robot",
        group: "primary",
        run: function () {
          host.navigate("/ai-delivery-observer");
        },
      });

      cleanup = function () {
        disposed = true;
        if (pollTimer) clearInterval(pollTimer);
        controllers.forEach(function (controller) { controller.abort(); });
        controllers.clear();
        entries.clear();
      };
    },
    destroy: function () {
      cleanup();
      cleanup = function () {};
    },
  });
})();

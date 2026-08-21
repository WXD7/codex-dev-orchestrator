const executorLabels = {};

const state = {
  projects: [],
  tasks: [],
  currentProject: null,
  openTaskId: null,
  executors: [],
  polling: null,
};

const columns = [
  ["backlog", "待处理"],
  ["ready", "可执行"],
  ["running", "执行中"],
  ["waiting_approval", "等待确认"],
  ["review", "评审"],
  ["blocked", "阻塞"],
  ["failed", "失败"],
  ["done", "完成"],
];

const roles = {
  coordinator: "技术协调者",
  planner: "方案规划者",
  implementer: "实现工程师",
  reviewer: "独立评审者",
  qa: "质量验证者",
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

let toastTimer;
function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = "toast"; }, 3200);
}

function quotaSummary(item) {
  const quota = item.quota || {};
  if (!quota.observed) return `${item.label}：额度待观测`;
  const remaining = Math.round(Number(quota.remaining_percent || 0));
  let reset = "";
  if (quota.reset_at) {
    const seconds = Math.max(0, Number(quota.reset_at) - Date.now() / 1000);
    reset = seconds < 3600
      ? `，${Math.max(1, Math.round(seconds / 60))} 分钟后刷新`
      : `，${(seconds / 3600).toFixed(1)} 小时后刷新`;
  }
  return `${item.label}：剩余 ${remaining}%${reset}`;
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    const node = $("#agent-health");
    const agent = health.agent;
    state.executors = agent.executors || [];
    state.executors.forEach((item) => { executorLabels[item.name] = item.label; });
    const ready = state.executors.filter((item) => item.ready);
    node.className = `health-card ${agent.ready ? "ok" : "error"}`;
    node.querySelector("strong").textContent = agent.ready
      ? (agent.quota_aware ? "智能额度调度已开启" : "执行器已就绪")
      : "没有可用的执行器";
    node.querySelector("small").textContent = agent.ready
      ? (ready.map(quotaSummary).join(" · ") || `默认 ${agent.default}`)
      : (agent.problems[0] || "请运行 doctor");
  } catch (error) {
    $("#agent-health").className = "health-card error";
  }
}

async function loadProjects(preferredId = null) {
  const payload = await api("/api/projects");
  state.projects = payload.projects;
  renderProjects();
  if (!state.projects.length) {
    state.currentProject = null;
    state.tasks = [];
    renderEmpty();
    return;
  }
  const target = preferredId || state.currentProject?.id || state.projects[0].id;
  await selectProject(target);
}

function renderProjects() {
  $("#project-list").innerHTML = state.projects.map((project) => `
    <button class="project-item ${state.currentProject?.id === project.id ? "active" : ""}" data-project-id="${project.id}">
      <span class="project-avatar">${escapeHtml(project.name.slice(0, 1).toUpperCase())}</span>
      <span class="project-copy">
        <strong>${escapeHtml(project.name)}</strong>
        <small>${project.task_count || 0} 个任务 · ${project.running_count || 0} 个执行中</small>
      </span>
    </button>
  `).join("");
}

async function selectProject(projectId) {
  const project = state.projects.find((item) => item.id === projectId);
  if (!project) return;
  state.currentProject = project;
  renderProjects();
  $("#project-title").textContent = project.name;
  $("#project-meta").textContent = `${project.repo_path} · 基准 ${project.base_branch} · ${project.auto_start ? "自动调度已开启" : "手动调度"}`;
  $("#new-task").disabled = false;
  $("#onboarding").classList.add("hidden");
  $("#board-section").classList.remove("hidden");
  await loadTasks();
}

function renderEmpty() {
  $("#project-title").textContent = "选择或添加一个 Git 工程";
  $("#project-meta").textContent = "任务、worktree、消息、审批和 Codex 执行记录集中在这里。";
  $("#new-task").disabled = true;
  $("#onboarding").classList.remove("hidden");
  $("#board-section").classList.add("hidden");
}

async function loadTasks(silent = false) {
  if (!state.currentProject) return;
  try {
    const payload = await api(`/api/projects/${state.currentProject.id}/tasks`);
    state.tasks = payload.tasks;
    state.currentProject.task_count = state.tasks.length;
    state.currentProject.running_count = state.tasks.filter((task) => task.status === "running").length;
    renderProjects();
    renderBoard();
    if (state.openTaskId) await openTask(state.openTaskId, true);
  } catch (error) {
    if (!silent) toast(error.message, true);
  }
}

function renderBoard() {
  const counts = Object.fromEntries(columns.map(([status]) => [status, 0]));
  state.tasks.forEach((task) => { counts[task.status] = (counts[task.status] || 0) + 1; });
  $("#board-counts").textContent = `${state.tasks.length} 个任务 · ${counts.running} 个正在执行 · ${counts.waiting_approval} 个等待你确认`;
  $("#kanban").innerHTML = columns.map(([status, label]) => {
    const tasks = state.tasks.filter((task) => task.status === status);
    return `
      <section class="column" data-status="${status}">
        <div class="column-header">
          <div class="column-title"><span class="status-dot"></span><h3>${label}</h3></div>
          <span class="column-count">${tasks.length}</span>
        </div>
        <div class="task-stack">
          ${tasks.length ? tasks.map(taskCard).join("") : '<div class="empty-column">暂无任务</div>'}
        </div>
      </section>`;
  }).join("");
}

function taskCard(task) {
  const flags = [
    task.allow_delegation ? '<span class="tiny-flag" title="可委派子任务">↳</span>' : "",
    task.requires_approval ? '<span class="tiny-flag" title="需要人工批准">✓</span>' : "",
    (task.required_artifacts || []).length ? `<span class="tiny-flag" title="必须交付 ${(task.required_artifacts || []).length} 个文件">▣</span>` : "",
    task.worktree_path ? '<span class="tiny-flag" title="已有独立 worktree">⑂</span>' : "",
  ].join("");
  const body = task.summary || task.description || "尚无说明";
  const assignedExecutor = task.assigned_executor || task.executor;
  const executor = assignedExecutor
    ? `<span class="task-executor">${escapeHtml(executorLabels[assignedExecutor] || assignedExecutor)}${task.assigned_model ? ` · ${escapeHtml(task.assigned_model)}` : ""}</span>`
    : '<span class="task-executor">智能分配</span>';
  return `
    <article class="task-card" data-task-id="${task.id}">
      <div class="task-card-top"><span class="role-pill">${escapeHtml(roles[task.role] || task.role)}</span><span class="task-id">${escapeHtml(task.id.slice(-6))}</span></div>
      <h4>${escapeHtml(task.title)}</h4>
      <p>${escapeHtml(body)}</p>
      <div class="task-card-footer"><span>P${task.priority}</span>${executor}<span class="tiny-flags">${flags}</span></div>
    </article>`;
}

async function openTask(taskId, silent = false) {
  try {
    const task = await api(`/api/tasks/${taskId}`);
    state.openTaskId = taskId;
    $("#drawer-id").textContent = `${task.id} · ${roles[task.role] || task.role}`;
    $("#drawer-title").textContent = task.title;
    $("#drawer-body").innerHTML = renderTaskDetail(task);
    $("#task-drawer").classList.add("open");
    $("#task-drawer").setAttribute("aria-hidden", "false");
    $("#drawer-backdrop").classList.add("open");
    bindDrawer(task);
    if (!silent) history.replaceState(null, "", `#/task/${taskId}`);
  } catch (error) {
    if (!silent) toast(error.message, true);
  }
}

function renderTaskDetail(task) {
  const statusLabel = Object.fromEntries(columns)[task.status] || task.status;
  const actions = taskActions(task);
  const approval = task.approval ? `
    <div class="approval-box"><strong>等待你的决定</strong><p>${escapeHtml(task.approval.question)}</p></div>` : "";
  const error = task.error ? `<div class="error-box">${escapeHtml(task.error)}</div>` : "";
  const messages = task.messages.length ? task.messages.map((message) => `
    <div class="message ${escapeHtml(message.kind)}">
      <div class="message-head"><strong>${escapeHtml(message.sender)}</strong><span>${escapeHtml(message.created_at)}</span></div>
      <div class="message-body">${escapeHtml(message.body)}</div>
    </div>`).join("") : '<div class="empty-column">还没有消息</div>';
  const dependencies = task.dependencies.length
    ? task.dependencies.map((item) => `${escapeHtml(item.title)}（${Object.fromEntries(columns)[item.status] || item.status}）`).join("、")
    : "无";
  const children = task.children.length
    ? task.children.map((item) => `${escapeHtml(item.title)}（${Object.fromEntries(columns)[item.status] || item.status}）`).join("、")
    : "无";
  let evidence = [];
  try { evidence = JSON.parse(task.evidence || "[]"); } catch (error) { evidence = []; }
  const ranChecks = evidence.length
    ? `<ul class="evidence-list">${evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : `<div class="evidence-empty">Agent 没有报告任何检查。批准前请自行确认。</div>`;
  const requiredArtifacts = (task.required_artifacts || []).length
    ? `<ul class="evidence-list">${task.required_artifacts.map((item) => `<li><code>${escapeHtml(item)}</code></li>`).join("")}</ul>`
    : '<div class="evidence-empty">此任务未声明必须交付的文件。</div>';
  const severityLabels = { error: "错误", warning: "警告", info: "提示" };
  const alerts = (task.alerts || []).length
    ? (task.alerts || []).map((alert) => `
      <div class="runtime-alert ${escapeHtml(alert.severity)}">
        <div class="runtime-alert-head">
          <span class="alert-severity">${escapeHtml(severityLabels[alert.severity] || alert.severity)}</span>
          <code>${escapeHtml(alert.type)}</code>
          ${alert.occurrences > 1 ? `<span class="alert-count">重复 ${alert.occurrences} 次</span>` : ""}
          <time>${escapeHtml(alert.created_at)}</time>
        </div>
        <div class="runtime-alert-message">${escapeHtml(alert.message)}</div>
      </div>`).join("")
    : '<div class="monitor-ok">没有发现需要处理的运行异常。</div>';
  const events = (task.runs || []).slice(0, 8).map((run) => `
    <div class="event"><strong>${escapeHtml(run.status)} · ${escapeHtml(run.started_at)}</strong><pre>${escapeHtml(JSON.stringify(run.usage || {}, null, 2))}</pre></div>
  `).join("") || '<div class="empty-column">尚未执行</div>';
  return `
    <div class="meta-strip">
      <div class="meta-box"><span>状态</span><strong>${escapeHtml(statusLabel)}</strong></div>
      <div class="meta-box"><span>分支</span><strong>${escapeHtml(task.branch_name || "执行时创建")}</strong></div>
      <div class="meta-box"><span>Agent / 模型</span><strong>${escapeHtml(task.assigned_executor ? `${executorLabels[task.assigned_executor] || task.assigned_executor} · ${task.assigned_model || "自动"}` : "等待智能分配")}</strong></div>
      <div class="meta-box"><span>会话</span><strong>${escapeHtml(task.session_id ? task.session_id.slice(0, 8) : "尚未建立")}</strong></div>
    </div>
    ${error}${approval}
    <div class="task-actions">${actions}</div>
    <section class="detail-section"><h3>运行异常与警告</h3><div class="runtime-alert-list">${alerts}</div></section>
    <section class="detail-section"><h3>目标与边界</h3><div class="detail-text">${escapeHtml(task.description || "未填写")}</div></section>
    <section class="detail-section"><h3>必需产物（系统验收）</h3>${requiredArtifacts}</section>
    <section class="detail-section"><h3>Agent 摘要</h3><div class="detail-text">${escapeHtml(task.summary || "Agent 尚未提交摘要。")}</div></section>
    <section class="detail-section"><h3>检查证据（Agent 自报）</h3>${ranChecks}</section>
    <section class="detail-section"><h3>交接说明</h3><div class="detail-text">${escapeHtml(task.handoff || "暂无交接信息。")}</div></section>
    <section class="detail-section"><h3>任务关系</h3><div class="detail-text">前置：${dependencies}\n子任务：${children}</div></section>
    <section class="detail-section">
      <h3>对话与审批</h3><div class="message-list">${messages}</div>
      <form class="message-form" id="message-form"><input name="body" required placeholder="给这个 Agent 留消息…"><button class="button secondary small">发送</button></form>
    </section>
    <section class="detail-section"><h3>最近运行</h3><div class="event-list">${events}</div></section>
    <section class="detail-section"><h3>分支变更</h3><button class="button secondary small" id="load-changes">加载完整 Diff</button><div id="changes-panel"></div></section>`;
}

function taskActions(task) {
  if (["backlog", "ready"].includes(task.status)) return '<button class="button primary small" data-action="start">智能启动</button>';
  if (["failed", "blocked"].includes(task.status)) return '<button class="button primary small" data-action="retry">重试任务</button>';
  if (task.status === "waiting_approval") return `
    <button class="button primary small" data-action="approve">批准</button>
    <button class="button danger small" data-action="reject">拒绝并反馈</button>`;
  if (task.status === "review") return `
    <button class="button primary small" data-action="accept-review">评审通过</button>
    <button class="button danger small" data-action="request-changes">要求修改</button>`;
  if (task.status === "running") return '<button class="button secondary small" disabled>Agent 正在工作</button>';
  return "";
}

function bindDrawer(task) {
  $("#message-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = new FormData(event.currentTarget).get("body");
    try {
      await api(`/api/tasks/${task.id}/message`, { method: "POST", body: JSON.stringify({ body }) });
      await openTask(task.id, true);
    } catch (error) { toast(error.message, true); }
  });
  $("#load-changes")?.addEventListener("click", async () => {
    const panel = $("#changes-panel");
    panel.innerHTML = '<p class="detail-text">正在读取分支差异…</p>';
    try {
      const changes = await api(`/api/tasks/${task.id}/changes`);
      panel.innerHTML = `<pre class="code-panel">${escapeHtml([changes.commits, changes.stat, changes.diff, changes.status].filter(Boolean).join("\n\n")) || "暂无代码变更"}</pre>`;
    } catch (error) { panel.innerHTML = `<div class="error-box">${escapeHtml(error.message)}</div>`; }
  });
}

async function taskAction(action, taskId) {
  try {
    if (action === "start" || action === "retry") {
      await api(`/api/tasks/${taskId}/${action}`, { method: "POST", body: "{}" });
      toast("任务已进入执行队列");
    } else {
      openDecisionDialog(action, taskId);
      return;
    }
    await loadTasks(true);
  } catch (error) { toast(error.message, true); }
}

function openDecisionDialog(action, taskId) {
  const copy = {
    approve: ["批准任务结果", "批准后该任务完成，并释放满足依赖条件的下游任务。", "确认批准"],
    reject: ["拒绝并退回修改", "请写明拒绝原因和期望的修改方向，Agent 将在同一会话中继续。", "退回修改"],
    "accept-review": ["通过人工评审", "通过后任务完成，并释放下游评审或 QA 任务。", "评审通过"],
    "request-changes": ["要求继续修改", "请给出具体、可验证的修改要求，Agent 将在同一 worktree 和会话中继续。", "退回修改"],
  }[action];
  if (!copy) return;
  const form = $("#decision-form");
  form.reset();
  form.elements.task_id.value = taskId;
  form.elements.action.value = action;
  $("#decision-title").textContent = copy[0];
  $("#decision-help").textContent = copy[1];
  $("#decision-submit").textContent = copy[2];
  $("#decision-submit").className = `button ${["reject", "request-changes"].includes(action) ? "danger" : "primary"}`;
  $("#decision-dialog").showModal();
}

$("#decision-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const action = form.elements.action.value;
  const taskId = form.elements.task_id.value;
  const note = form.elements.note.value.trim();
  if (["reject", "request-changes"].includes(action) && !note) {
    toast("退回任务时必须填写修改要求", true);
    return;
  }
  try {
    if (["approve", "reject"].includes(action)) {
      await api(`/api/tasks/${taskId}/approval`, {
        method: "POST",
        body: JSON.stringify({ approved: action === "approve", note }),
      });
    } else {
      await api(`/api/tasks/${taskId}/review`, {
        method: "POST",
        body: JSON.stringify({ accepted: action === "accept-review", note }),
      });
    }
    $("#decision-dialog").close();
    toast(["approve", "accept-review"].includes(action) ? "决定已记录" : "修改要求已交还 Agent");
    await loadTasks(true);
  } catch (error) { toast(error.message, true); }
});

function closeDrawer() {
  state.openTaskId = null;
  if (location.hash.startsWith("#/task/")) history.replaceState(null, "", location.pathname);
  $("#task-drawer").classList.remove("open");
  $("#task-drawer").setAttribute("aria-hidden", "true");
  $("#drawer-backdrop").classList.remove("open");
}

function openProjectDialog() { $("#project-dialog").showModal(); }
function openTaskDialog() {
  if (!state.currentProject) return;
  const executor = $("#task-form [name=executor]");
  executor.innerHTML = `<option value="">智能分配（推荐）</option>${state.executors
    .map((item) => `<option value="${item.name}"${item.ready ? "" : " disabled"}>${escapeHtml(item.label)}${item.ready ? "" : " · 不可用"}</option>`)
    .join("")}`;
  const parent = $("#task-form [name=parent_id]");
  const dependencies = $("#task-form [name=dependencies]");
  const options = state.tasks.filter((task) => task.status !== "done").map((task) => `<option value="${task.id}">${escapeHtml(task.title)} · ${escapeHtml(Object.fromEntries(columns)[task.status] || task.status)}</option>`).join("");
  parent.innerHTML = `<option value="">无</option>${options}`;
  dependencies.innerHTML = state.tasks.map((task) => `<option value="${task.id}">${escapeHtml(task.title)}</option>`).join("");
  $("#task-dialog").showModal();
}

$("#project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form));
  data.auto_start = form.elements.auto_start.checked;
  try {
    const project = await api("/api/projects", { method: "POST", body: JSON.stringify(data) });
    form.closest("dialog").close(); form.reset();
    await loadProjects(project.id); toast("项目已接入");
  } catch (error) { toast(error.message, true); }
});

$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const data = Object.fromEntries(formData);
  data.project_id = state.currentProject.id;
  data.dependencies = formData.getAll("dependencies");
  data.required_artifacts = String(data.required_artifacts || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  ["requires_approval", "allow_delegation", "auto_start", "start_now"].forEach((key) => { data[key] = form.elements[key].checked; });
  data.priority = Number(data.priority);
  try {
    const task = await api("/api/tasks", { method: "POST", body: JSON.stringify(data) });
    form.closest("dialog").close(); form.reset();
    await loadTasks(); await openTask(task.id); toast("任务已创建");
  } catch (error) { toast(error.message, true); }
});

document.addEventListener("click", async (event) => {
  const project = event.target.closest("[data-project-id]");
  if (project) await selectProject(project.dataset.projectId);
  const task = event.target.closest("[data-task-id]");
  if (task) await openTask(task.dataset.taskId);
  const action = event.target.closest("[data-action]");
  if (action && state.openTaskId) await taskAction(action.dataset.action, state.openTaskId);
  if (event.target.closest(".dialog-close")) event.target.closest("dialog").close();
});

$("#new-project").addEventListener("click", openProjectDialog);
$("#onboarding-project").addEventListener("click", openProjectDialog);
$("#new-task").addEventListener("click", openTaskDialog);
$("#refresh-board").addEventListener("click", () => loadTasks());
$("#close-drawer").addEventListener("click", closeDrawer);
$("#drawer-backdrop").addEventListener("click", closeDrawer);

function deepLinkTaskId() {
  const match = /^#\/task\/([A-Za-z0-9_]+)$/.exec(location.hash);
  return match ? match[1] : null;
}

// Deep links let the MCP layer point a human at the exact task that needs a
// decision, without ever making the decision itself.
async function followDeepLink() {
  const taskId = deepLinkTaskId();
  if (!taskId || taskId === state.openTaskId) return;
  try {
    const task = await api(`/api/tasks/${taskId}`);
    if (state.currentProject?.id !== task.project_id) await selectProject(task.project_id);
    await openTask(taskId);
  } catch (error) {
    toast(error.message, true);
  }
}

window.addEventListener("hashchange", () => followDeepLink());

async function boot() {
  await Promise.all([loadHealth(), loadProjects()]);
  await followDeepLink();
  state.polling = setInterval(() => {
    loadHealth();
    if (state.currentProject) loadTasks(true);
  }, 3000);
}

boot().catch((error) => toast(error.message, true));

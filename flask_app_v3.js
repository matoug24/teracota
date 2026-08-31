let state = { authenticated: false, systems: [], tasks: [] };
let activeView = "systems";
let dialogMode = null;
let editingItem = null;
let dashboardRangeMonths = 6;
let dashboardRangeOffset = 0;
let detailRangeScale = 1;
let detailRangeOffset = 0;
let issueSeverityFilter = "All";
let issueStatusFilter = "All";
let visitTypeFilter = "All";

const pageMode = document.body.dataset.page || "home";
const initialSystemId = Number(document.body.dataset.systemId || 0);
const visitCategories = ["Calibration", "Optics", "Commissioning", "Electronics"];

const authArea = document.querySelector("#authArea");
const metricGrid = document.querySelector("#metricGrid");
const timelineArea = document.querySelector("#timelineArea");
const developmentPanel = document.querySelector("#developmentPanel");
const taskColumns = document.querySelector("#taskColumns");
const systemsView = document.querySelector("#systemsView");
const systemDetailPage = document.querySelector("#systemDetailPage");
const entryDialog = document.querySelector("#entryDialog");
const entryForm = document.querySelector("#entryForm");
const dialogTitle = document.querySelector("#dialogTitle");
const dialogFields = document.querySelector("#dialogFields");
const detailDialog = document.querySelector("#detailDialog");
const detailDialogTitle = document.querySelector("#detailDialogTitle");
const detailDialogContent = document.querySelector("#detailDialogContent");

document.querySelectorAll(".nav-tab").forEach((button) => {
  button.addEventListener("click", () => {
    if (pageMode === "system") {
      window.location.href = button.dataset.view === "development" ? "/#development" : "/";
      return;
    }
    setActiveView(button.dataset.view);
  });
});

document.querySelector("#addSystemButton").addEventListener("click", () => openDialog("system"));
document.querySelector("#addTaskButton").addEventListener("click", () => openDialog("task"));
document.querySelector("#closeDetailDialog").addEventListener("click", () => detailDialog.close());

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;

  const system = getCurrentSystem();
  const action = button.dataset.action;
  if (action === "editSystem") openDialog("editSystem", system);
  if (action === "maintenance") openDialog("maintenance");
  if (action === "issue") openDialog("systemIssue");
  if (action === "deleteSystem" && system) deleteItem("system", system.id);
});

entryForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  saveDialog();
});

loadState();

async function loadState() {
  const response = await fetch("/api/state");
  state = await response.json();
  if (pageMode === "home" && window.location.hash === "#development") {
    activeView = "development";
  }
  render();
}

async function sendJson(url, options) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(`Request failed: ${response.status}`);
  }
  state = await response.json();
  render();
}

function setActiveView(view) {
  activeView = view;
  history.replaceState(null, "", view === "development" ? "#development" : window.location.pathname);
  render();
}

function render() {
  renderAuth();

  if (pageMode === "system") {
    renderSystemDetailPage();
    return;
  }

  const showingDevelopment = activeView === "development";
  systemsView.classList.toggle("hidden", showingDevelopment);
  developmentPanel.classList.toggle("hidden", !showingDevelopment);
  systemDetailPage.classList.add("hidden");

  document.querySelectorAll(".nav-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === activeView);
  });

  renderMetrics();
  renderTimeline();
  renderTasks();
  setEditVisibility();
}

function renderAuth() {
  if (state.authenticated) {
    authArea.innerHTML = `
      <span class="auth-label">teraview</span>
      <button class="secondary-button auth-button" id="logoutButton" type="button">Log out</button>
    `;
    document.querySelector("#logoutButton").addEventListener("click", logout);
    return;
  }

  authArea.innerHTML = `
    <button class="secondary-button auth-button" id="loginButton" type="button">Log in</button>
  `;
  document.querySelector("#loginButton").addEventListener("click", showLogin);
}

function showLogin() {
  dialogMode = "login";
  editingItem = null;
  dialogTitle.textContent = "Log in";
  dialogFields.innerHTML = [
    field("username", "Username", "text", ""),
    field("password", "Password", "password", ""),
  ].map(renderField).join("");
  entryDialog.showModal();
}

async function logout() {
  await sendJson("/api/logout", { method: "POST", body: "{}" });
}

function setEditVisibility() {
  document.querySelectorAll(".requires-auth").forEach((element) => {
    element.classList.toggle("hidden", !state.authenticated);
  });
}

function renderMetrics() {
  const systems = state.systems;
  const openIssues = systems.reduce(
    (total, system) => total + system.issues.filter((issue) => issue.status !== "Closed").length,
    0,
  );
  const metrics = [
    ["Systems", systems.length],
    ["Open Issues", openIssues],
  ];

  metricGrid.innerHTML = metrics
    .map(([label, value]) => `
      <article class="metric">
        <span>${escapeHtml(label)}</span>
        <strong>${value}</strong>
      </article>
    `)
    .join("");
}

function renderTimeline() {
  const groups = groupByLocation(state.systems);
  const dashboardTimeline = timelineWindow(dashboardRangeMonths, dashboardRangeOffset);

  if (!groups.length) {
    timelineArea.innerHTML = `
      <div class="empty-card">
        <h3>No systems yet</h3>
        <p>${state.authenticated ? "Add a system to start building the operations timeline." : "Log in to add systems."}</p>
      </div>
    `;
    return;
  }

  timelineArea.innerHTML = `
    ${renderTimelineControls("dashboard", dashboardTimeline, `${dashboardRangeMonths} month${dashboardRangeMonths === 1 ? "" : "s"}`)}
    <div class="location-groups">
      ${groups
    .map(([location, systems]) => `
      <section class="location-group">
        <header class="location-header">
          <h3>${escapeHtml(location)}</h3>
          <span>${systems.length} system${systems.length === 1 ? "" : "s"}</span>
        </header>
        ${renderTimelineScale(dashboardTimeline.start, dashboardTimeline.end)}
        <div class="timeline-list">
          ${systems.map((system) => renderTimelineRow(system, dashboardTimeline.start, dashboardTimeline.end)).join("")}
        </div>
      </section>
    `)
    .join("")}
    </div>
  `;

  timelineArea.querySelectorAll(".timeline-row").forEach((button) => {
    button.addEventListener("click", () => {
      const systemId = button.getAttribute("data-system-id");
      if (systemId) window.location.href = `/systems/${systemId}`;
    });
  });
  bindTimelineControls(timelineArea, "dashboard");
}

function renderTimelineRow(system, start, end) {
  const markers = eventMarkers(system, start, end);
  const laneCount = Math.max(1, ...markers.map((marker) => marker.lane + 1));
  const openIssueCount = system.issues.filter((issue) => issue.status !== "Closed").length;
  const statusClass = getStatusClass(system.status);

  return `
    <button class="timeline-row" type="button" data-system-id="${system.id}">
      <div class="timeline-meta">
        <strong>${escapeHtml(system.name)}</strong>
        <span>${escapeHtml(system.status)} - ${openIssueCount} open issue${openIssueCount === 1 ? "" : "s"}</span>
      </div>
      <div class="timeline-track" style="--lane-count: ${laneCount}" aria-label="${escapeAttribute(system.name)} timeline">
        <span class="status-bar ${statusClass}"></span>
        ${markers.map(renderMarker).join("")}
      </div>
    </button>
  `;
}

function renderMarker(marker) {
  return `
    <span class="timeline-marker ${marker.kind} ${marker.severityClass} ${marker.statusClass}"
      style="left: ${marker.left}%; --marker-lane: ${marker.lane}"
      title="${escapeAttribute(marker.title)}">
      ${marker.label}
    </span>
  `;
}

function renderSystemDetailPage() {
  const system = state.systems.find((item) => item.id === initialSystemId);
  systemsView.classList.add("hidden");
  developmentPanel.classList.add("hidden");
  systemDetailPage.classList.remove("hidden");

  document.querySelectorAll(".nav-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === "systems");
  });

  if (!system) {
    systemDetailPage.innerHTML = `
      <div class="detail-page-header">
        <a class="secondary-button link-button" href="/">Back to Systems</a>
      </div>
      <div class="empty-card">
        <h3>System not found</h3>
        <p>This system may have been removed or the link is incorrect.</p>
      </div>
    `;
    return;
  }

  const fullHistory = fullTimelineWindow(system);
  const fullTimeline = scaledTimelineWindow(fullHistory, detailRangeScale, detailRangeOffset);
  const openIssueCount = system.issues.filter((issue) => issue.status !== "Closed").length;
  const visibleIssues = system.issues.filter((issue) => (
    (issueSeverityFilter === "All" || normalizeSeverity(issue.severity) === issueSeverityFilter)
    && (issueStatusFilter === "All" || issue.status === issueStatusFilter)
  ));
  const visibleVisits = system.maintenance.filter((record) => (
    visitTypeFilter === "All" || splitList(record.type).includes(visitTypeFilter)
  ));

  systemDetailPage.innerHTML = `
    <div class="detail-page-header">
      <a class="secondary-button link-button" href="/">Back to Systems</a>
      <div class="detail-actions requires-auth">
        <button class="secondary-button" type="button" data-action="editSystem">Edit Info</button>
        <button class="primary-button" type="button" data-action="maintenance">Add Visit</button>
        <button class="secondary-button" type="button" data-action="issue">Report Issue</button>
        <button class="danger-button" type="button" data-action="deleteSystem">Delete System</button>
      </div>
    </div>

    <section class="system-detail-hero">
      <div class="system-hero-copy">
        <p class="eyebrow">${escapeHtml(system.location)}</p>
        <h2>${escapeHtml(system.name)}</h2>
        <div class="hero-summary">
          <div>
            <span>Open Issues</span>
            <strong>${openIssueCount}</strong>
          </div>
          <div class="hero-notes">
            <span>Operations Notes</span>
            <p>${escapeHtml(system.notes || "No notes yet.")}</p>
          </div>
        </div>
      </div>
      ${statusPill(system.status)}
    </section>

    <section class="detail-section">
      <header>
        <h3>System Timeline</h3>
        ${renderTimelineControls("detail", fullTimeline, detailRangeScale === 1 && detailRangeOffset === 0 ? "All history" : formatTimelineSpan(fullTimeline))}
      </header>
      ${renderTimelineScale(fullTimeline.start, fullTimeline.end, "detail-timeline-scale")}
      ${renderTimelineRow(system, fullTimeline.start, fullTimeline.end)}
    </section>

    <div class="detail-layout">
      <section class="detail-section">
        <header>
          <h3>Issues</h3>
          <span class="tag">${system.issues.length} total</span>
        </header>
        ${renderFilterGroup("Severity", "issue-severity", ["All", "Low", "Medium", "High"], issueSeverityFilter)}
        ${renderFilterGroup("Status", "issue-status", ["All", "Open", "Closed"], issueStatusFilter)}
        <div class="issue-list">
          ${renderIssueItems(visibleIssues, "No issues match the selected filters.")}
        </div>
      </section>

      <section class="detail-section">
        <header>
          <h3>Visit History</h3>
          <span class="tag">${system.maintenance.length} visits</span>
        </header>
        ${renderFilterGroup("Work Completed", "visit-type", ["All", ...visitCategories], visitTypeFilter)}
        <div class="record-list">
          ${renderMaintenanceItems(visibleVisits)}
        </div>
      </section>
    </div>
  `;

  bindSystemDetailActions(system);
  bindTimelineControls(systemDetailPage, "detail");
  bindRecordFilters();
  setEditVisibility();
}

function bindSystemDetailActions(system) {
  systemDetailPage.querySelectorAll("[data-edit-maintenance]").forEach((button) => {
    button.addEventListener("click", () => {
      const record = system.maintenance.find((item) => item.id === Number(button.dataset.editMaintenance));
      openDialog("editMaintenance", record);
    });
  });
  systemDetailPage.querySelectorAll("[data-delete-maintenance]").forEach((button) => {
    button.addEventListener("click", () => deleteItem("maintenance", Number(button.dataset.deleteMaintenance)));
  });
  systemDetailPage.querySelectorAll("[data-edit-issue]").forEach((button) => {
    button.addEventListener("click", () => {
      const issue = system.issues.find((item) => item.id === Number(button.dataset.editIssue));
      openDialog("editIssue", issue);
    });
  });
  systemDetailPage.querySelectorAll("[data-delete-issue]").forEach((button) => {
    button.addEventListener("click", () => deleteItem("issue", Number(button.dataset.deleteIssue)));
  });
}

function bindRecordFilters() {
  systemDetailPage.querySelectorAll("[data-filter-kind]").forEach((button) => {
    button.addEventListener("click", () => {
      const kind = button.dataset.filterKind;
      const value = button.dataset.filterValue;
      if (kind === "issue-severity") issueSeverityFilter = value;
      if (kind === "issue-status") issueStatusFilter = value;
      if (kind === "visit-type") visitTypeFilter = value;
      renderSystemDetailPage();
    });
  });
}

function renderTasks() {
  const categories = ["Hardware", "Software"];
  taskColumns.innerHTML = categories
    .map((category) => {
      const tasks = state.tasks.filter((task) => task.category === category);
      return `
        <section class="task-column">
          <header>
            <h3>${category}</h3>
            <span>${tasks.length}</span>
          </header>
          <div class="task-list">
            ${tasks.length ? tasks.map(renderTaskItem).join("") : emptyTask(category)}
          </div>
        </section>
      `;
    })
    .join("");

  document.querySelectorAll("[data-task-id]").forEach((button) => {
    button.addEventListener("click", () => showTaskDetails(Number(button.dataset.taskId)));
  });
}

function renderTaskItem(task) {
  return `
    <button class="task-item" type="button" data-task-id="${task.id}">
      <h3>${escapeHtml(task.title)}</h3>
      <span class="task-time">Expected ${escapeHtml(task.expected_time)}</span>
    </button>
  `;
}

function showTaskDetails(taskId) {
  const task = state.tasks.find((item) => item.id === taskId);
  if (!task) return;

  detailDialogTitle.textContent = task.title;
  detailDialogContent.innerHTML = `
    <div class="info-grid">
      ${infoCell("Category", task.category)}
      ${infoCell("Expected Time", task.expected_time)}
      ${infoCell("Created", formatDate(task.created_at))}
    </div>
    <div class="panel-block">
      <h4>Details</h4>
      <p class="muted">${escapeHtml(task.notes || "No details added.")}</p>
    </div>
    <div class="dialog-actions-inline requires-auth">
      <button class="secondary-button" type="button" id="editTaskButton">Edit Task</button>
      <button class="danger-button" type="button" id="deleteTaskButton">Delete Task</button>
    </div>
  `;
  detailDialog.showModal();
  setEditVisibility();
  document.querySelector("#editTaskButton")?.addEventListener("click", () => {
    detailDialog.close();
    openDialog("editTask", task);
  });
  document.querySelector("#deleteTaskButton")?.addEventListener("click", () => {
    detailDialog.close();
    deleteItem("task", task.id);
  });
}

function emptyTask(category) {
  return `
    <div class="task-item empty-task">
      <h3>No ${category.toLowerCase()} tasks</h3>
      <span class="task-time">${state.authenticated ? "Add a development task when shared work is identified." : "Log in to add tasks."}</span>
    </div>
  `;
}

function openDialog(mode, item = null) {
  dialogMode = mode;
  editingItem = item;
  const system = getCurrentSystem();
  const configs = {
    login: {
      title: "Log in",
      fields: [field("username", "Username", "text", ""), field("password", "Password", "password", "")],
    },
    system: { title: "Add System", fields: systemFields() },
    editSystem: { title: "Edit System", fields: systemFields(item || system) },
    maintenance: {
      title: `Add Visit - ${system?.name || "System"}`,
      fields: maintenanceFields(),
    },
    editMaintenance: {
      title: "Edit Visit",
      fields: maintenanceFields(item),
    },
    systemIssue: {
      title: `Report Issue - ${system?.name || "System"}`,
      fields: issueFields(),
    },
    editIssue: {
      title: "Edit Issue",
      fields: issueFields(item),
    },
    task: {
      title: "Add Development Task",
      fields: taskFields(),
    },
    editTask: {
      title: "Edit Development Task",
      fields: taskFields(item),
    },
  };

  dialogTitle.textContent = configs[mode].title;
  dialogFields.innerHTML = configs[mode].fields.map(renderField).join("");
  entryDialog.showModal();
}

async function saveDialog() {
  const data = formPayload(entryForm);
  const system = getCurrentSystem();

  try {
    if (dialogMode === "login") {
      await sendJson("/api/login", { method: "POST", body: JSON.stringify(data) });
    }

    if (dialogMode === "system") {
      const oldIds = new Set(state.systems.map((item) => item.id));
      await sendJson("/api/systems", { method: "POST", body: JSON.stringify(data) });
      const newSystem = state.systems.find((item) => !oldIds.has(item.id));
      entryDialog.close();
      entryForm.reset();
      if (newSystem) window.location.href = `/systems/${newSystem.id}`;
      return;
    }

    if (dialogMode === "editSystem" && system) {
      await sendJson(`/api/systems/${system.id}`, { method: "PUT", body: JSON.stringify(data) });
    }

    if (dialogMode === "maintenance" && system) {
      await sendJson(`/api/systems/${system.id}/maintenance`, { method: "POST", body: JSON.stringify(data) });
    }

    if (dialogMode === "editMaintenance" && editingItem) {
      await sendJson(`/api/maintenance/${editingItem.id}`, { method: "PUT", body: JSON.stringify(data) });
    }

    if (dialogMode === "systemIssue" && system) {
      await sendJson(`/api/systems/${system.id}/issues`, { method: "POST", body: JSON.stringify(data) });
    }

    if (dialogMode === "editIssue" && editingItem) {
      await sendJson(`/api/issues/${editingItem.id}`, { method: "PUT", body: JSON.stringify(data) });
    }

    if (dialogMode === "task") {
      await sendJson("/api/tasks", { method: "POST", body: JSON.stringify(data) });
    }

    if (dialogMode === "editTask" && editingItem) {
      await sendJson(`/api/tasks/${editingItem.id}`, { method: "PUT", body: JSON.stringify(data) });
    }
  } catch (error) {
    showFormError(dialogMode === "login" ? "Invalid username or password." : "Unable to save. Please log in and try again.");
    return;
  }

  entryDialog.close();
  entryForm.reset();
}

function showFormError(message) {
  let error = dialogFields.querySelector(".form-error");
  if (!error) {
    error = document.createElement("div");
    error.className = "form-error";
    dialogFields.prepend(error);
  }
  error.textContent = message;
}

async function deleteItem(type, id) {
  const labels = {
    system: "system",
    maintenance: "maintenance record",
    issue: "issue",
    task: "development task",
  };
  if (!window.confirm(`Delete this ${labels[type]}?`)) return;

  const routes = {
    system: `/api/systems/${id}`,
    maintenance: `/api/maintenance/${id}`,
    issue: `/api/issues/${id}`,
    task: `/api/tasks/${id}`,
  };
  await sendJson(routes[type], { method: "DELETE" });
  if (type === "system") window.location.href = "/";
}

function systemFields(system = {}) {
  system = system || {};
  return [
    field("name", "System Name", "text", system.name || ""),
    field("location", "Location", "text", system.location || ""),
    field("status", "Status", "select", system.status || "Operational", [
      "Operational",
      "Needs Maintenance",
      "Offline",
      "Commissioning",
    ]),
    field("notes", "Operations Notes", "textarea", system.notes || ""),
  ];
}

function maintenanceFields(record = {}) {
  record = record || {};
  return [
    field("date", "Visit Date", "date", record.date || new Date().toISOString().slice(0, 10)),
    field("engineer", "Engineer", "text", record.engineer || ""),
    field("type", "Work Completed", "checkboxes", splitList(record.type), visitCategories),
    field("summary", "Summary", "textarea", record.summary || ""),
  ];
}

function issueFields(issue = {}) {
  issue = issue || {};
  return [
    field("title", "Title", "text", issue.title || ""),
    field("severity", "Severity", "select", normalizeSeverity(issue.severity || "Medium"), ["Low", "Medium", "High"]),
    field("opened", "Opened Date", "date", issue.opened || new Date().toISOString().slice(0, 10)),
    field("status", "Status", "select", issue.status || "Open", ["Open", "Closed"]),
    field("notes", "Notes", "textarea", issue.notes || ""),
  ];
}

function taskFields(task = {}) {
  task = task || {};
  return [
    field("title", "Title", "text", task.title || ""),
    field("category", "Category", "select", task.category || "Hardware", ["Hardware", "Software"]),
    field("expected_time", "Expected Time To Complete", "text", task.expected_time || ""),
    field("notes", "Details", "textarea", task.notes || ""),
  ];
}

function field(name, label, type, value, options = []) {
  return { name, label, type, value, options };
}

function renderField(config) {
  const common = `id="${config.name}" name="${config.name}"`;
  let control = "";

  if (config.type === "textarea") {
    control = `<textarea ${common} rows="4">${escapeHtml(config.value)}</textarea>`;
  } else if (config.type === "select") {
    control = `
      <select ${common}>
        ${config.options.map((option) => `<option value="${escapeAttribute(option)}" ${option === config.value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
      </select>
    `;
  } else if (config.type === "checkboxes") {
    const selected = Array.isArray(config.value) ? config.value : splitList(config.value);
    control = `
      <div class="checkbox-grid">
        ${config.options.map((option) => `
          <label class="checkbox-option">
            <input type="checkbox" name="${config.name}" value="${escapeAttribute(option)}" ${selected.includes(option) ? "checked" : ""} />
            <span>${escapeHtml(option)}</span>
          </label>
        `).join("")}
      </div>
    `;
  } else {
    control = `<input ${common} type="${config.type}" value="${escapeAttribute(config.value)}" />`;
  }

  return `
    <div class="field-group">
      <label for="${config.name}">${escapeHtml(config.label)}</label>
      ${control}
    </div>
  `;
}

function formPayload(form) {
  const data = {};
  const formData = new FormData(form);
  formData.forEach((value, key) => {
    if (Object.prototype.hasOwnProperty.call(data, key)) {
      if (!Array.isArray(data[key])) data[key] = [data[key]];
      data[key].push(value);
    } else {
      data[key] = value;
    }
  });
  return data;
}

function renderMaintenanceItems(records) {
  if (!records.length) {
    return `
      <div class="record-item">
        <h3>No visits found</h3>
        <p>${state.authenticated ? "Add a visit or change the selected filter." : "No visit records match the selected filter."}</p>
      </div>
    `;
  }

  return records.map((record) => `
    <article class="record-item">
      <div class="item-header">
        <h3>${escapeHtml(record.type)} - ${formatDate(record.date)}</h3>
        <div class="item-actions requires-auth">
          <button class="secondary-button" type="button" data-edit-maintenance="${record.id}">Edit</button>
          <button class="danger-button" type="button" data-delete-maintenance="${record.id}">Delete</button>
        </div>
      </div>
      <p>${escapeHtml(record.summary)}</p>
      <div class="tag-row">
        <span class="tag">${escapeHtml(record.engineer)}</span>
      </div>
    </article>
  `).join("");
}

function renderIssueItems(issues, emptyMessage) {
  if (!issues.length) {
    return `
      <div class="issue-item">
        <h3>No issues</h3>
        <p>${escapeHtml(emptyMessage)}</p>
      </div>
    `;
  }

  return issues.map((issue) => `
    <article class="issue-item issue-card-${getSeverityClass(normalizeSeverity(issue.severity))}">
      <div class="item-header">
        <h3>${escapeHtml(issue.title)}</h3>
        <div class="item-actions requires-auth">
          <button class="secondary-button" type="button" data-edit-issue="${issue.id}">Edit</button>
          <button class="danger-button" type="button" data-delete-issue="${issue.id}">Delete</button>
        </div>
      </div>
      <p>${escapeHtml(issue.notes || "No notes added.")}</p>
      <div class="tag-row">
        <span class="tag severity-tag ${getSeverityClass(normalizeSeverity(issue.severity))}">${escapeHtml(normalizeSeverity(issue.severity))}</span>
        <span class="tag">${escapeHtml(issue.status)}</span>
        <span class="tag">Opened ${formatDate(issue.opened)}</span>
      </div>
    </article>
  `).join("");
}

function eventMarkers(system, start, end) {
  const events = [];
  system.maintenance.forEach((record) => {
    events.push({
      date: record.date,
      kind: "visit",
      severityClass: "",
      statusClass: "",
      label: "V",
      title: `${record.type} visit - ${formatDate(record.date)} - ${record.summary || ""}`,
    });
  });
  system.issues.forEach((issue) => {
    events.push({
      date: issue.opened,
      kind: "issue",
      severityClass: getSeverityClass(normalizeSeverity(issue.severity)),
      statusClass: issue.status === "Closed" ? "closed" : "open",
      label: "!",
      title: `${issue.status} ${issue.severity} issue - ${formatDate(issue.opened)} - ${issue.title}`,
    });
  });

  const visibleEvents = events
    .map((event) => ({ ...event, left: timelinePosition(event.date, start, end) }))
    .filter((event) => event.left >= 0 && event.left <= 100)
    .map((event) => ({ ...event, left: Math.min(98.5, Math.max(1.5, event.left)) }))
    .sort((a, b) => a.left - b.left);
  return assignMarkerLanes(visibleEvents);
}

function assignMarkerLanes(events) {
  const laneEnds = [];
  return events.map((event) => {
    let lane = laneEnds.findIndex((lastPosition) => event.left - lastPosition >= 3.5);
    if (lane === -1) lane = laneEnds.length;
    laneEnds[lane] = event.left;
    return { ...event, lane };
  });
}

function timelineWindow(months = 6, offset = 0) {
  const end = addMonths(startOfDay(new Date()), offset);
  return { start: addMonths(end, -months), end };
}

function fullTimelineWindow(system) {
  const dates = [
    ...system.maintenance.map((record) => record.date),
    ...system.issues.map((issue) => issue.opened),
  ].filter(Boolean).map((value) => startOfDay(new Date(`${value}T00:00:00`)));

  if (!dates.length) return timelineWindow();

  let start = new Date(Math.min(...dates));
  let end = new Date(Math.max(...dates));
  start = addMonths(start, -1);
  end = addMonths(end, 1);
  if (end <= start) end = addMonths(start, 2);
  return { start, end };
}

function scaledTimelineWindow(base, scale, offset) {
  const baseSpan = base.end - base.start;
  const span = Math.max(1000 * 60 * 60 * 24 * 14, baseSpan * scale);
  const center = (base.start.getTime() + base.end.getTime()) / 2 + offset * span * 0.35;
  return { start: new Date(center - span / 2), end: new Date(center + span / 2) };
}

function renderTimelineControls(scope, range, label) {
  return `
    <div class="timeline-controls" aria-label="Timeline controls">
      <button class="icon-button timeline-control" type="button" data-timeline-scope="${scope}" data-timeline-action="previous" title="Earlier" aria-label="Earlier">&lt;</button>
      <button class="icon-button timeline-control" type="button" data-timeline-scope="${scope}" data-timeline-action="zoom-out" title="Zoom out" aria-label="Zoom out">-</button>
      <span class="timeline-range-label" title="${escapeAttribute(formatDate(range.start))} to ${escapeAttribute(formatDate(range.end))}">${escapeHtml(label)}</span>
      <button class="icon-button timeline-control" type="button" data-timeline-scope="${scope}" data-timeline-action="zoom-in" title="Zoom in" aria-label="Zoom in">+</button>
      <button class="icon-button timeline-control" type="button" data-timeline-scope="${scope}" data-timeline-action="next" title="Later" aria-label="Later">&gt;</button>
      <button class="secondary-button timeline-reset" type="button" data-timeline-scope="${scope}" data-timeline-action="reset">Reset</button>
    </div>
  `;
}

function renderTimelineScale(start, end, extraClass = "") {
  const labels = monthLabels(start, end);
  return `
    <div class="timeline-scale ${extraClass}" style="--tick-count: ${labels.length}">
      ${labels.map((label) => `<span>${escapeHtml(label)}</span>`).join("")}
    </div>
  `;
}

function bindTimelineControls(container, scope) {
  container.querySelectorAll(`[data-timeline-scope="${scope}"]`).forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const action = button.dataset.timelineAction;
      if (scope === "dashboard") {
        if (action === "zoom-in") dashboardRangeMonths = Math.max(1, Math.round(dashboardRangeMonths / 2));
        if (action === "zoom-out") dashboardRangeMonths = Math.min(60, dashboardRangeMonths * 2);
        if (action === "previous") dashboardRangeOffset -= Math.max(1, Math.round(dashboardRangeMonths / 2));
        if (action === "next") dashboardRangeOffset += Math.max(1, Math.round(dashboardRangeMonths / 2));
        if (action === "reset") {
          dashboardRangeMonths = 6;
          dashboardRangeOffset = 0;
        }
        renderTimeline();
        return;
      }

      if (action === "zoom-in") detailRangeScale = Math.max(0.125, detailRangeScale / 2);
      if (action === "zoom-out") detailRangeScale = Math.min(8, detailRangeScale * 2);
      if (action === "previous") detailRangeOffset -= 1;
      if (action === "next") detailRangeOffset += 1;
      if (action === "reset") {
        detailRangeScale = 1;
        detailRangeOffset = 0;
      }
      renderSystemDetailPage();
    });
  });
}

function renderFilterGroup(label, kind, options, active) {
  return `
    <div class="record-filter">
      <span>${escapeHtml(label)}</span>
      <div class="filter-buttons">
        ${options.map((option) => `
          <button class="filter-button ${option === active ? "active" : ""}" type="button"
            data-filter-kind="${kind}" data-filter-value="${escapeAttribute(option)}">${escapeHtml(option)}</button>
        `).join("")}
      </div>
    </div>
  `;
}

function formatTimelineSpan(range) {
  const months = Math.max(1, Math.round(monthDiff(range.start, range.end)));
  return `${months} month${months === 1 ? "" : "s"}`;
}

function timelinePosition(dateString, start, end) {
  if (!dateString) return -1;
  const eventDate = startOfDay(new Date(`${dateString}T00:00:00`));
  const span = end - start;
  return Math.round(((eventDate - start) / span) * 1000) / 10;
}

function monthLabels(start, end) {
  const labels = [];
  const cursor = new Date(start);
  const spanMonths = Math.max(1, monthDiff(start, end));
  const count = Math.min(9, spanMonths + 1);
  for (let index = 0; index < count; index += 1) {
    const progress = count === 1 ? 0 : index / (count - 1);
    const labelDate = addMonths(cursor, Math.round(spanMonths * progress));
    labels.push(labelDate.toLocaleDateString("en", { month: "short", year: "2-digit" }));
  }
  return labels;
}

function monthDiff(start, end) {
  return (end.getFullYear() - start.getFullYear()) * 12 + (end.getMonth() - start.getMonth());
}

function groupByLocation(systems) {
  const map = new Map();
  systems.forEach((system) => {
    if (!map.has(system.location)) map.set(system.location, []);
    map.get(system.location).push(system);
  });
  return [...map.entries()];
}

function getCurrentSystem() {
  return state.systems.find((system) => system.id === initialSystemId) || state.systems[0];
}

function statusPill(status) {
  return `
    <span class="status-pill ${getStatusClass(status)}">
      <span class="status-dot" aria-hidden="true"></span>
      ${escapeHtml(status)}
    </span>
  `;
}

function infoCell(label, value) {
  return `
    <div class="info-cell">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "Not set")}</strong>
    </div>
  `;
}

function getStatusClass(status) {
  return `status-${String(status).toLowerCase().replace(/\s+/g, "-")}`;
}

function getSeverityClass(severity) {
  return `severity-${String(severity || "medium").toLowerCase()}`;
}

function normalizeSeverity(severity) {
  return String(severity || "Medium").toLowerCase() === "critical" ? "High" : String(severity || "Medium");
}

function splitList(value) {
  return String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function addMonths(dateValue, months) {
  const copy = new Date(dateValue);
  copy.setMonth(copy.getMonth() + months);
  return startOfDay(copy);
}

function startOfDay(dateValue) {
  const copy = new Date(dateValue);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function formatDate(dateString) {
  if (!dateString) return "Not set";
  if (dateString instanceof Date) {
    return new Intl.DateTimeFormat("en", {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(dateString);
  }
  return new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(`${dateString}T00:00:00`));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

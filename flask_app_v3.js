let state = { authenticated: false, systems: [], tasks: [], locations: [], theme: "standard" };
let activeView = "systems";
let dialogMode = null;
let editingItem = null;
let dashboardRangeMonths = 9;
let dashboardRangeOffset = 0;
let detailRangeScale = 1;
let detailRangeOffset = 0;
let issueSeverityFilter = "All";
let issueStatusFilter = "All";
let visitTypeFilter = "All";

const pageMode = document.body.dataset.page || "home";
const initialSystemId = Number(document.body.dataset.systemId || 0);
const initialLocation = document.body.dataset.location || "";
const visitCategories = ["Calibration", "Optics", "Commissioning", "Electronics"];
const themeOptions = [
  { id: "standard", name: "Standard", description: "Clean field operations", colors: ["#10252f", "#f0b64c", "#f4f7f9"] },
  { id: "control-room", name: "Control Room", description: "Graphite industrial console", colors: ["#242a2d", "#45b97c", "#ffbd45"] },
  { id: "instrument", name: "Instrument", description: "Lab equipment interface", colors: ["#f2f3ef", "#31393c", "#16a085"] },
];

const authArea = document.querySelector("#authArea");
const metricGrid = document.querySelector("#metricGrid");
const timelineArea = document.querySelector("#timelineArea");
const developmentPanel = document.querySelector("#developmentPanel");
const taskColumns = document.querySelector("#taskColumns");
const systemsView = document.querySelector("#systemsView");
const systemDetailPage = document.querySelector("#systemDetailPage");
const locationDetailPage = document.querySelector("#locationDetailPage");
const adminPage = document.querySelector("#adminPage");
const themeStylesheet = document.querySelector("#themeStylesheet");
const entryDialog = document.querySelector("#entryDialog");
const entryForm = document.querySelector("#entryForm");
const dialogTitle = document.querySelector("#dialogTitle");
const dialogFields = document.querySelector("#dialogFields");
const saveDialogButton = document.querySelector("#saveDialogButton");
const detailDialog = document.querySelector("#detailDialog");
const detailDialogTitle = document.querySelector("#detailDialogTitle");
const detailDialogContent = document.querySelector("#detailDialogContent");

document.querySelectorAll(".nav-tab").forEach((button) => {
  button.addEventListener("click", () => {
    if (pageMode !== "home") {
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
  if (action === "siteVisit") openDialog("siteVisit");
  if (action === "issue") openDialog("systemIssue");
  if (action === "editLocation") openDialog("editLocation", getCurrentLocation());
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
  if (response.status === 401) {
    redirectToLogin();
    return;
  }
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
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) {
      redirectToLogin();
      return;
    }
    throw new Error(payload.message || `Unable to complete the request (${response.status}).`);
  }
  state = payload;
  render();
}

function setActiveView(view) {
  activeView = view;
  history.replaceState(null, "", view === "development" ? "#development" : window.location.pathname);
  render();
}

function render() {
  renderAuth();
  applyTheme();

  if (pageMode === "system") {
    renderSystemDetailPage();
    return;
  }

  if (pageMode === "location") {
    renderLocationDetailPage();
    return;
  }

  if (pageMode === "admin") {
    renderAdminPage();
    return;
  }

  const showingDevelopment = activeView === "development";
  systemsView.classList.toggle("hidden", showingDevelopment);
  developmentPanel.classList.toggle("hidden", !showingDevelopment);
  systemDetailPage.classList.add("hidden");
  locationDetailPage.classList.add("hidden");
  adminPage.classList.add("hidden");

  document.querySelectorAll(".nav-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === activeView);
  });

  renderMetrics();
  renderTimeline();
  renderTasks();
  setEditVisibility();
}

function applyTheme() {
  const theme = themeOptions.find((option) => option.id === state.theme) || themeOptions[0];
  const filename = theme.id === "standard" ? "flask_styles.css" : `flask_styles_${theme.id.replace("-", "_")}.css`;
  const nextHref = `/assets/${filename}`;
  if (!themeStylesheet.href.endsWith(nextHref)) themeStylesheet.href = nextHref;
}

function renderAuth() {
  if (state.authenticated) {
    authArea.innerHTML = `
      <span class="auth-label">${escapeHtml(state.username || "Signed in")}</span>
      <button class="secondary-button auth-button" id="logoutButton" type="button">Log out</button>
    `;
    document.querySelector("#logoutButton").addEventListener("click", logout);
    return;
  }

  redirectToLogin();
}

function redirectToLogin() {
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  window.location.href = `/login?next=${encodeURIComponent(next)}`;
}

async function logout() {
  await fetch("/api/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  window.location.href = "/login";
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
          <h3><a href="/locations/${encodeURIComponent(location)}">${escapeHtml(location)}</a></h3>
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
  const segments = statusSegments(system, start, end);
  const visitLaneCount = Math.max(1, ...markers.filter((marker) => marker.kind === "visit").map((marker) => marker.lane + 1));
  const issueLaneCount = Math.max(1, ...markers.filter((marker) => marker.kind === "issue").map((marker) => marker.lane + 1));
  const trackHeight = 70 + (Math.max(visitLaneCount, issueLaneCount) - 1) * 54;
  const openIssueCount = system.issues.filter((issue) => issue.status !== "Closed").length;

  return `
    <button class="timeline-row" type="button" data-system-id="${system.id}">
      <div class="timeline-meta">
        <strong>${escapeHtml(system.name)}</strong>
        <span>${escapeHtml(system.status)} - ${openIssueCount} open issue${openIssueCount === 1 ? "" : "s"}</span>
      </div>
      <div class="timeline-track" style="--track-height: ${trackHeight}px" aria-label="${escapeAttribute(system.name)} timeline">
        ${segments.map(renderStatusSegment).join("")}
        ${markers.map(renderMarker).join("")}
      </div>
    </button>
  `;
}

function renderStatusSegment(segment) {
  return `
    <span class="status-bar status-segment ${getStatusClass(segment.status)}"
      style="left: ${segment.left}%; width: ${segment.width}%"
      title="${escapeAttribute(segment.status)} from ${escapeAttribute(formatDate(segment.startedAt))}"></span>
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
        <button class="primary-button" type="button" data-action="maintenance">Add Site Visit</button>
        <button class="secondary-button" type="button" data-action="issue">Report Issue</button>
      </div>
    </div>

    <section class="system-detail-hero">
      <div class="system-hero-copy">
        <p class="eyebrow">${escapeHtml(system.location)}</p>
        <h2>${escapeHtml(system.name)}</h2>
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
      <div class="status-history-list" aria-label="Status history">
        ${system.status_history.map((entry) => `
          <span class="status-history-item">
            <i class="status-dot ${getStatusClass(entry.status)}"></i>
            ${escapeHtml(entry.status)} <small>from ${formatDate(entry.started_at)}</small>
          </span>
        `).join("")}
      </div>
      <div class="timeline-summary">
        <div><span>Open Issues</span><strong>${openIssueCount}</strong></div>
        <div><span>Total Issues</span><strong>${system.issues.length}</strong></div>
        <div><span>Total Visits</span><strong>${system.maintenance.length}</strong></div>
        <div class="timeline-notes"><span>Operations Notes</span><p>${escapeHtml(system.notes || "No notes yet.")}</p></div>
      </div>
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

function renderLocationDetailPage() {
  systemsView.classList.add("hidden");
  developmentPanel.classList.add("hidden");
  systemDetailPage.classList.add("hidden");
  adminPage.classList.add("hidden");
  locationDetailPage.classList.remove("hidden");
  setSystemsNavActive();

  const location = getCurrentLocation();
  const systems = state.systems.filter((system) => system.location === initialLocation);
  const issues = systems.flatMap((system) => system.issues.map((issue) => ({ ...issue, systemName: system.name, systemId: system.id })));
  const visits = systems.flatMap((system) => system.maintenance.map((visit) => ({ ...visit, systemName: system.name, systemId: system.id })));
  const siteVisits = groupSiteVisits(visits);
  issues.sort((a, b) => String(b.opened).localeCompare(String(a.opened)));
  const openIssues = issues.filter((issue) => issue.status !== "Closed").length;
  const locationTimeline = timelineWindow(dashboardRangeMonths, dashboardRangeOffset);

  if (!location && !systems.length) {
    locationDetailPage.innerHTML = `
      <div class="detail-page-header"><a class="secondary-button link-button" href="/">Back to Systems</a></div>
      <div class="empty-card"><h3>Location not found</h3><p>No systems or location information were found.</p></div>
    `;
    return;
  }

  locationDetailPage.innerHTML = `
    <div class="detail-page-header">
      <a class="secondary-button link-button" href="/">Back to Systems</a>
      <div class="detail-actions requires-auth">
        <button class="primary-button" type="button" data-action="siteVisit">Add Site Visit</button>
      </div>
    </div>

    <section class="location-hero">
      <div>
        <p class="eyebrow">Location overview</p>
        <h2>${escapeHtml(initialLocation)}</h2>
      </div>
      <div class="location-metrics">
        ${metricCard("Systems", systems.length)}
        ${metricCard("Open Issues", openIssues)}
        ${metricCard("Total Issues", issues.length)}
        ${metricCard("Total Visits", siteVisits.length)}
      </div>
    </section>

    <section class="detail-section location-systems-timeline">
      <header><h3>Systems</h3><span class="tag">${systems.length}</span></header>
      ${renderTimelineControls("location", locationTimeline, `${dashboardRangeMonths} month${dashboardRangeMonths === 1 ? "" : "s"}`)}
      ${renderTimelineScale(locationTimeline.start, locationTimeline.end, "detail-timeline-scale")}
      <div class="timeline-list">
        ${systems.length ? systems.map((system) => renderTimelineRow(system, locationTimeline.start, locationTimeline.end)).join("") : '<p class="muted">No systems at this location.</p>'}
      </div>
    </section>

    <div class="detail-layout location-records">
      <section class="detail-section">
        <header><h3>Combined Issues</h3><span class="tag">${issues.length}</span></header>
        <div class="issue-list">${renderLocationIssues(issues)}</div>
      </section>
      <section class="detail-section">
        <header><h3>Site Visit History</h3><span class="tag">${siteVisits.length}</span></header>
        <div class="record-list">${renderLocationVisits(siteVisits)}</div>
      </section>
    </div>
  `;
  locationDetailPage.querySelectorAll(".timeline-row").forEach((button) => {
    button.addEventListener("click", () => {
      window.location.href = `/systems/${button.dataset.systemId}`;
    });
  });
  locationDetailPage.querySelectorAll("[data-edit-site-visit]").forEach((button) => {
    button.addEventListener("click", () => {
      const visit = siteVisits.find((item) => item.id === Number(button.dataset.editSiteVisit));
      if (visit) openDialog("editSiteVisit", visit);
    });
  });
  locationDetailPage.querySelectorAll("[data-delete-site-visit]").forEach((button) => {
    button.addEventListener("click", () => deleteItem("siteVisit", Number(button.dataset.deleteSiteVisit)));
  });
  bindTimelineControls(locationDetailPage, "location");
  setEditVisibility();
}

function renderAdminPage() {
  systemsView.classList.add("hidden");
  developmentPanel.classList.add("hidden");
  systemDetailPage.classList.add("hidden");
  locationDetailPage.classList.add("hidden");
  adminPage.classList.remove("hidden");
  setSystemsNavActive();

  if (!state.authenticated) {
    redirectToLogin();
    return;
  }

  adminPage.innerHTML = `
    <div class="section-header">
      <div><p class="eyebrow">Restricted tools</p><h2>Administration</h2></div>
    </div>

    <section class="admin-section">
      <header><div><p class="eyebrow">Application appearance</p><h3>Interface Style</h3></div><span class="tag">${themeOptions.length} styles</span></header>
      <div class="theme-grid">
        ${themeOptions.map(renderThemeOption).join("")}
      </div>
    </section>

    <section class="admin-section">
      <header><div><p class="eyebrow">Fleet records</p><h3>System Management</h3></div><span class="tag">${state.systems.length} systems</span></header>
      <div class="admin-system-list">
        ${state.systems.map((system) => `
          <article class="admin-system-row">
            <div><strong>${escapeHtml(system.name)}</strong><span>${escapeHtml(system.location)} - ${escapeHtml(system.status)}</span></div>
            <div class="item-actions">
              <a class="secondary-button link-button" href="/systems/${system.id}">View</a>
              <button class="secondary-button" type="button" data-admin-edit="${system.id}">Edit</button>
              <button class="danger-button" type="button" data-admin-delete="${system.id}">Delete</button>
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;

  adminPage.querySelectorAll("[data-theme]").forEach((button) => {
    button.addEventListener("click", () => sendJson("/api/settings/theme", {
      method: "PUT",
      body: JSON.stringify({ theme: button.dataset.theme }),
    }));
  });
  adminPage.querySelectorAll("[data-admin-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      const system = state.systems.find((item) => item.id === Number(button.dataset.adminEdit));
      openDialog("editSystem", system);
    });
  });
  adminPage.querySelectorAll("[data-admin-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteItem("system", Number(button.dataset.adminDelete)));
  });
}

function setSystemsNavActive() {
  document.querySelectorAll(".nav-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === "systems");
  });
}

function metricCard(label, value) {
  return `<article class="metric"><span>${escapeHtml(label)}</span><strong>${value}</strong></article>`;
}

function repeatedIssueSummary(issues) {
  const groups = new Map();
  issues.forEach((issue) => {
    const key = issue.title.trim().toLowerCase();
    if (!key) return;
    const item = groups.get(key) || { title: issue.title, count: 0 };
    item.count += 1;
    groups.set(key, item);
  });
  return [...groups.values()].filter((item) => item.count > 1).sort((a, b) => b.count - a.count);
}

function renderLocationIssues(issues) {
  if (!issues.length) return '<div class="issue-item"><h3>No issues</h3><p>No issues recorded at this location.</p></div>';
  return issues.map((issue) => `
    <article class="issue-item issue-card-${getSeverityClass(normalizeSeverity(issue.severity))} ${issue.status === "Closed" ? "issue-card-closed" : ""}">
      <div class="item-header"><h3>${escapeHtml(issue.title)}</h3><a class="tag link-tag" href="/systems/${issue.systemId}">${escapeHtml(issue.systemName)}</a></div>
      <p>${escapeHtml(issue.notes || "No notes added.")}</p>
      <div class="tag-row"><span class="tag severity-tag ${getSeverityClass(issue.severity)}">${escapeHtml(issue.severity)}</span><span class="tag ${issue.status === "Closed" ? "status-closed-tag" : ""}">${escapeHtml(issue.status)}</span><span class="tag">${formatDate(issue.opened)}</span></div>
    </article>
  `).join("");
}

function groupSiteVisits(visits) {
  const groups = new Map();
  visits.forEach((visit) => {
    const key = visit.visit_id ? `site-${visit.visit_id}` : `record-${visit.id}`;
    if (!groups.has(key)) {
      groups.set(key, {
        id: visit.visit_id || null,
        date: visit.date,
        engineer: visit.engineer,
        records: [],
      });
    }
    groups.get(key).records.push(visit);
  });
  return [...groups.values()].sort((a, b) => String(b.date).localeCompare(String(a.date)));
}

function renderLocationVisits(siteVisits) {
  if (!siteVisits.length) return '<div class="record-item"><h3>No site visits</h3><p>No visits recorded at this location.</p></div>';
  return siteVisits.map((visit) => `
    <article class="record-item site-visit-item">
      <div class="item-header">
        <div>
          <h3>${formatDate(visit.date)}</h3>
          <div class="tag-row"><span class="tag">${escapeHtml(visit.engineer)}</span><span class="tag">${visit.records.length} system${visit.records.length === 1 ? "" : "s"}</span></div>
        </div>
        ${visit.id ? `
          <div class="item-actions requires-auth">
            <button class="secondary-button" type="button" data-edit-site-visit="${visit.id}">Edit Visit</button>
            <button class="danger-button" type="button" data-delete-site-visit="${visit.id}">Delete Visit</button>
          </div>
        ` : ""}
      </div>
      <div class="site-visit-systems">
        ${visit.records.map((record) => `
          <div class="site-visit-system-row">
            <a href="/systems/${record.systemId}">${escapeHtml(record.systemName)}</a>
            <span>${escapeHtml(record.type)}</span>
            <p>${escapeHtml(record.summary || "No summary added.")}</p>
          </div>
        `).join("")}
      </div>
    </article>
  `).join("");
}

function renderThemeOption(theme) {
  return `
    <button class="theme-option ${state.theme === theme.id ? "active" : ""}" type="button" data-theme="${theme.id}">
      <span class="theme-swatches">${theme.colors.map((color) => `<i style="background:${color}"></i>`).join("")}</span>
      <strong>${escapeHtml(theme.name)}</strong><small>${escapeHtml(theme.description)}</small>
      <span class="theme-selected">${state.theme === theme.id ? "Selected" : "Select"}</span>
    </button>
  `;
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
    system: { title: "Add System", fields: systemFields() },
    editSystem: { title: "Edit System", fields: systemFields(item || system) },
    editStatusHistory: { title: "Edit Status Event", fields: statusHistoryFields(item) },
    editLocation: { title: `Edit ${initialLocation}`, fields: locationFields(item) },
    maintenance: {
      title: `Add Site Visit - ${system?.location || "Location"}`,
      fields: maintenanceFields({}, system ? [system.id] : [], system?.location || ""),
    },
    siteVisit: {
      title: `Add Site Visit - ${initialLocation}`,
      fields: maintenanceFields({}, [], initialLocation),
    },
    editMaintenance: {
      title: "Edit Visit",
      fields: maintenanceFields(item),
    },
    editSiteVisit: {
      title: "Edit Site Visit",
      fields: siteVisitFields(item),
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
  saveDialogButton.textContent = "Save";
  dialogFields.innerHTML = configs[mode].fields.map(renderField).join("");
  if (mode === "editSystem") {
    dialogFields.insertAdjacentHTML("beforeend", renderStatusHistoryManager(item || system));
    bindStatusHistoryManager();
  }
  entryDialog.showModal();
}

async function saveDialog() {
  const data = formPayload(entryForm);
  const system = getCurrentSystem();
  const targetSystem = dialogMode === "editSystem" ? editingItem || system : system;

  try {
    if (dialogMode === "system") {
      const oldIds = new Set(state.systems.map((item) => item.id));
      await sendJson("/api/systems", { method: "POST", body: JSON.stringify(data) });
      const newSystem = state.systems.find((item) => !oldIds.has(item.id));
      entryDialog.close();
      entryForm.reset();
      if (newSystem) window.location.href = `/systems/${newSystem.id}`;
      return;
    }

    if (dialogMode === "editSystem" && targetSystem) {
      await sendJson(`/api/systems/${targetSystem.id}`, { method: "PUT", body: JSON.stringify(data) });
    }

    if (dialogMode === "editLocation") {
      await sendJson(`/api/locations/${encodeURIComponent(initialLocation)}`, { method: "PUT", body: JSON.stringify(data) });
    }

    if (dialogMode === "editStatusHistory" && editingItem) {
      await sendJson(`/api/status-history/${editingItem.id}`, { method: "PUT", body: JSON.stringify(data) });
    }

    if (dialogMode === "maintenance" || dialogMode === "siteVisit") {
      if (!data.system_ids || (Array.isArray(data.system_ids) && !data.system_ids.length)) {
        showFormError("Select at least one system.");
        return;
      }
      data.location = dialogMode === "maintenance" ? system?.location : initialLocation;
      await sendJson("/api/site-visits", { method: "POST", body: JSON.stringify(data) });
    }

    if (dialogMode === "editMaintenance" && editingItem) {
      await sendJson(`/api/maintenance/${editingItem.id}`, { method: "PUT", body: JSON.stringify(data) });
    }

    if (dialogMode === "editSiteVisit" && editingItem) {
      await sendJson(`/api/site-visits/${editingItem.id}`, { method: "PUT", body: JSON.stringify(data) });
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
    showFormError(error.message);
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
    siteVisit: "site visit and all linked system records",
    issue: "issue",
    task: "development task",
    statusHistory: "status event",
  };
  if (!window.confirm(`Delete this ${labels[type]}?`)) return;

  const routes = {
    system: `/api/systems/${id}`,
    maintenance: `/api/maintenance/${id}`,
    siteVisit: `/api/site-visits/${id}`,
    issue: `/api/issues/${id}`,
    task: `/api/tasks/${id}`,
    statusHistory: `/api/status-history/${id}`,
  };
  await sendJson(routes[type], { method: "DELETE" });
  if (type === "system" && pageMode === "system") window.location.href = "/";
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
    field("status_start", "Status Effective Date", "date", new Date().toISOString().slice(0, 10)),
    field("notes", "Operations Notes", "textarea", system.notes || ""),
  ];
}

function statusHistoryFields(entry = {}) {
  entry = entry || {};
  return [
    field("status", "Status", "select", entry.status || "Operational", [
      "Operational",
      "Needs Maintenance",
      "Offline",
      "Commissioning",
    ]),
    field("started_at", "Effective Date", "date", entry.started_at || new Date().toISOString().slice(0, 10)),
  ];
}

function renderStatusHistoryManager(system) {
  if (!system?.status_history?.length) return "";
  const canDelete = system.status_history.length > 1;
  return `
    <section class="dialog-history-manager">
      <div class="dialog-subheader">
        <div><span>Status Timeline</span><strong>Edit or remove historical status changes</strong></div>
        <span class="tag">${system.status_history.length} events</span>
      </div>
      <div class="dialog-history-list">
        ${system.status_history.map((entry) => `
          <div class="dialog-history-row">
            <span class="status-history-label"><i class="status-dot ${getStatusClass(entry.status)}"></i><strong>${escapeHtml(entry.status)}</strong><small>${formatDate(entry.started_at)}</small></span>
            <div class="item-actions">
              <button class="secondary-button" type="button" data-edit-status-history="${entry.id}">Edit</button>
              <button class="danger-button" type="button" data-delete-status-history="${entry.id}" ${canDelete ? "" : "disabled"} title="${canDelete ? "Delete status event" : "A system must keep one status event"}">Delete</button>
            </div>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function bindStatusHistoryManager() {
  dialogFields.querySelectorAll("[data-edit-status-history]").forEach((button) => {
    button.addEventListener("click", () => {
      const system = editingItem || getCurrentSystem();
      const entry = system.status_history.find((item) => item.id === Number(button.dataset.editStatusHistory));
      entryDialog.close();
      openDialog("editStatusHistory", entry);
    });
  });
  dialogFields.querySelectorAll("[data-delete-status-history]").forEach((button) => {
    button.addEventListener("click", () => {
      entryDialog.close();
      deleteItem("statusHistory", Number(button.dataset.deleteStatusHistory));
    });
  });
}

function locationFields(location = {}) {
  location = location || {};
  return [
    field("contacts", "Local Contacts", "textarea", location.contacts || ""),
    field("notes", "Shared Site Notes", "textarea", location.notes || ""),
  ];
}

function systemsAtLocation(location) {
  return state.systems.filter((system) => system.location === location);
}

function maintenanceFields(record = {}, selectedSystemIds = [], location = "") {
  record = record || {};
  const fields = [
    field("date", "Visit Date", "date", record.date || new Date().toISOString().slice(0, 10)),
    field("engineer", "Engineer", "text", record.engineer || ""),
  ];
  if (!record.id) {
    fields.push(field(
      "system_ids",
      "Systems Serviced",
      "checkboxes",
      selectedSystemIds.map(String),
      systemsAtLocation(location).map((system) => ({ value: String(system.id), label: system.name })),
    ));
  }
  fields.push(
    field("type", "Work Completed", "checkboxes", splitList(record.type), visitCategories),
    field("summary", "Summary", "textarea", record.summary || ""),
  );
  return fields;
}

function siteVisitFields(visit = {}) {
  visit = visit || {};
  return [
    field("date", "Visit Date", "date", visit.date || new Date().toISOString().slice(0, 10)),
    field("engineer", "Engineer", "text", visit.engineer || ""),
  ];
}

function issueFields(issue = {}) {
  issue = issue || {};
  return [
    field("title", "Title", "text", issue.title || ""),
    field("severity", "Severity", "select", normalizeSeverity(issue.severity || "Medium"), ["Low", "Medium", "High"]),
    field("opened", "Opened Date", "date", issue.opened || new Date().toISOString().slice(0, 10)),
    field("status", "Status", "select", issue.status || "Open", ["Open", "Closed"]),
    field("reported_by", "Reported By", "text", issue.reported_by || ""),
    field("notes", "Notes", "textarea", issue.notes || ""),
    field("resolution_notes", "Resolution Notes", "textarea", issue.resolution_notes || ""),
    field("closed_date", "Closed Date", "date", issue.closed_date || ""),
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
    const selected = (Array.isArray(config.value) ? config.value : splitList(config.value)).map(String);
    control = `
      <div class="checkbox-grid">
        ${config.options.map((option) => {
          const optionValue = String(typeof option === "object" ? option.value : option);
          const optionLabel = typeof option === "object" ? option.label : option;
          return `
            <label class="checkbox-option">
              <input type="checkbox" name="${config.name}" value="${escapeAttribute(optionValue)}" ${selected.includes(optionValue) ? "checked" : ""} />
              <span>${escapeHtml(optionLabel)}</span>
            </label>
          `;
        }).join("")}
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
    <article class="issue-item issue-card-${getSeverityClass(normalizeSeverity(issue.severity))} ${issue.status === "Closed" ? "issue-card-closed" : ""}">
      <div class="item-header">
        <h3>${escapeHtml(issue.title)}</h3>
        <div class="item-actions requires-auth">
          <button class="secondary-button" type="button" data-edit-issue="${issue.id}">Edit</button>
          <button class="danger-button" type="button" data-delete-issue="${issue.id}">Delete</button>
        </div>
      </div>
      <p>${escapeHtml(issue.notes || "No notes added.")}</p>
      ${issue.resolution_notes ? `<div class="resolution-note"><strong>Resolution</strong><p>${escapeHtml(issue.resolution_notes)}</p></div>` : ""}
      <div class="tag-row">
        <span class="tag severity-tag ${getSeverityClass(normalizeSeverity(issue.severity))}">${escapeHtml(normalizeSeverity(issue.severity))}</span>
        <span class="tag ${issue.status === "Closed" ? "status-closed-tag" : ""}">${escapeHtml(issue.status)}</span>
        <span class="tag">Reported by ${escapeHtml(issue.reported_by || "Unknown")}</span>
        <span class="tag">Opened ${formatDate(issue.opened)}</span>
        ${issue.closed_date ? `<span class="tag">Closed ${formatDate(issue.closed_date)}</span>` : ""}
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
      label: "I",
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

function statusSegments(system, start, end) {
  const history = [...(system.status_history || [])].sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)));
  if (!history.length) return [{ status: system.status, startedAt: start, left: 0, width: 100 }];

  return history.map((entry, index) => {
    const segmentStart = startOfDay(new Date(`${entry.started_at}T00:00:00`));
    const nextEntry = history[index + 1];
    const segmentEnd = nextEntry ? startOfDay(new Date(`${nextEntry.started_at}T00:00:00`)) : end;
    const clippedStart = new Date(Math.max(segmentStart.getTime(), start.getTime()));
    const clippedEnd = new Date(Math.min(segmentEnd.getTime(), end.getTime()));
    if (clippedEnd <= clippedStart) return null;
    const left = Math.max(0, timelinePositionFromDate(clippedStart, start, end));
    const right = Math.min(100, timelinePositionFromDate(clippedEnd, start, end));
    return { status: entry.status, startedAt: entry.started_at, left, width: Math.max(0.4, right - left) };
  }).filter(Boolean);
}

function assignMarkerLanes(events) {
  const laneEnds = { visit: [], issue: [] };
  return events.map((event) => {
    const lanes = laneEnds[event.kind];
    let lane = lanes.findIndex((lastPosition) => event.left - lastPosition >= 3.5);
    if (lane === -1) lane = lanes.length;
    lanes[lane] = event.left;
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
    ...(system.status_history || []).map((entry) => entry.started_at),
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
      if (scope === "dashboard" || scope === "location") {
        if (action === "zoom-in") dashboardRangeMonths = Math.max(1, Math.round(dashboardRangeMonths / 2));
        if (action === "zoom-out") dashboardRangeMonths = Math.min(60, dashboardRangeMonths * 2);
        if (action === "previous") dashboardRangeOffset -= Math.max(1, Math.round(dashboardRangeMonths / 2));
        if (action === "next") dashboardRangeOffset += Math.max(1, Math.round(dashboardRangeMonths / 2));
        if (action === "reset") {
          dashboardRangeMonths = 9;
          dashboardRangeOffset = 0;
        }
        if (scope === "dashboard") renderTimeline();
        else renderLocationDetailPage();
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
  return timelinePositionFromDate(eventDate, start, end);
}

function timelinePositionFromDate(eventDate, start, end) {
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

function getCurrentLocation() {
  return state.locations.find((location) => location.name === initialLocation) || null;
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

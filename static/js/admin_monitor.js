(() => {
  const monitorState = {
    timer: null,
    section: null,
    level: "ALL",
    limit: 200,
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatBytes(value) {
    if (value === null || value === undefined) return "Unavailable";
    const bytes = Number(value);
    if (!Number.isFinite(bytes)) return "Unavailable";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = Math.max(0, bytes);
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return `${unit === 0 ? Math.round(amount) : amount.toFixed(1)} ${units[unit]}`;
  }

  function formatDuration(value) {
    let seconds = Number(value);
    if (!Number.isFinite(seconds)) return "Unavailable";
    seconds = Math.max(0, Math.floor(seconds));
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  function formatDateTime(value) {
    if (!value) return "No imports yet";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("en", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatLogTime(value) {
    return value ? formatDateTime(value) : "Unknown time";
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      throw new Error("Authentication required");
    }
    if (!response.ok) throw new Error(payload.message || `Request failed (${response.status})`);
    return payload;
  }

  function metric(label, value, detail, percent = null, alert = false) {
    const safePercent = Number.isFinite(Number(percent))
      ? Math.max(0, Math.min(100, Number(percent)))
      : null;
    return `
      <div class="monitor-metric ${alert ? "monitor-metric-alert" : ""}">
        <dt>${escapeHtml(label)}</dt>
        <dd>${escapeHtml(value)}</dd>
        <small>${escapeHtml(detail || "")}</small>
        ${safePercent === null ? "" : `<span class="monitor-meter"><i style="width:${safePercent}%"></i></span>`}
      </div>
    `;
  }

  function renderHealth(health) {
    const target = monitorState.section?.querySelector("#serverHealthContent");
    if (!target) return;
    const memory = health.memory || {};
    const rootDisk = health.disk?.root || {};
    const measurement = health.measurements || {};
    const storage = health.storage || {};
    const databaseBytes = Number(storage.operations_database_bytes || 0)
      + Number(storage.analytics_database_bytes || 0)
      + Number(storage.upload_database_bytes || 0);
    const load = health.cpu?.load_1m;
    const loadValue = load === null || load === undefined
      ? "Unavailable"
      : `${load} / ${health.cpu.logical_processors} CPU`;
    const pending = Number(measurement.pending_batches || 0);
    const failed = Number(measurement.failed_batches || 0);
    target.innerHTML = `
      <dl class="monitor-metric-grid">
        ${metric("Application", "Online", `Process uptime ${formatDuration(health.application?.process_uptime_seconds)}`)}
        ${metric("Memory", `${memory.used_percent ?? "-"}%`, `${formatBytes(memory.available_bytes)} available`, memory.used_percent, Number(memory.used_percent) >= 85)}
        ${metric("Swap", memory.swap_used_percent === null || memory.swap_used_percent === undefined ? "Unavailable" : `${memory.swap_used_percent}%`, memory.swap_used_percent === null || memory.swap_used_percent === undefined ? "Windows does not expose Linux swap data" : `${formatBytes(memory.swap_used_bytes)} used`, memory.swap_used_percent, Number(memory.swap_used_percent) >= 70)}
        ${metric("CPU load", loadValue, "1 minute average", load === null || load === undefined ? null : Number(load) * 100 / Number(health.cpu.logical_processors || 1), Number(load) > Number(health.cpu.logical_processors || 1))}
        ${metric("Server disk", `${rootDisk.used_percent ?? "-"}%`, `${formatBytes(rootDisk.free_bytes)} free`, rootDisk.used_percent, Number(rootDisk.used_percent) >= 85)}
        ${metric("Process memory", formatBytes(health.application?.process_memory_bytes), "TeraCota web process")}
        ${metric("Server uptime", formatDuration(health.host?.system_uptime_seconds), health.host?.hostname || "")}
        ${metric("Log storage", formatBytes(health.application?.log_bytes), "Current and rotated logs")}
      </dl>
      <div class="monitor-operation-grid">
        <div><span>Databases</span><strong>${formatBytes(databaseBytes)}</strong></div>
        <div><span>Imported CSV data</span><strong>${formatBytes(measurement.imported_bytes)}</strong><small>${Number(measurement.imported_files || 0).toLocaleString()} files</small></div>
        <div><span>Last measurement import</span><strong>${escapeHtml(formatDateTime(measurement.last_imported_at))}</strong></div>
        <div class="${failed ? "monitor-operation-alert" : ""}"><span>Upload batches</span><strong>${pending} pending / ${failed} failed</strong></div>
      </div>
    `;
    const status = monitorState.section.querySelector("#serverMonitorStatus");
    if (status) {
      status.textContent = `Updated ${formatDateTime(health.generated_at)}`;
      status.classList.remove("monitor-status-error");
    }
  }

  function renderLogs(payload) {
    const target = monitorState.section?.querySelector("#applicationLogRows");
    if (!target) return;
    const entries = [...(payload.entries || [])].reverse();
    target.innerHTML = entries.length ? entries.map((entry) => {
      const level = String(entry.level || "INFO").toUpperCase();
      const details = entry.exception ? `${entry.message}\n${entry.exception}` : entry.message;
      return `
        <article class="monitor-log-row" data-level="${escapeHtml(level)}">
          <time>${escapeHtml(formatLogTime(entry.timestamp))}</time>
          <span class="monitor-log-level">${escapeHtml(level)}</span>
          <pre>${escapeHtml(details)}</pre>
        </article>
      `;
    }).join("") : '<p class="monitor-empty">No matching application log entries.</p>';
  }

  async function loadHealth() {
    const health = await fetchJson("/api/admin/server-health");
    renderHealth(health);
  }

  async function loadLogs() {
    const params = new URLSearchParams({ level: monitorState.level, limit: String(monitorState.limit) });
    const logs = await fetchJson(`/api/admin/application-logs?${params}`);
    renderLogs(logs);
  }

  async function refreshAll() {
    const status = monitorState.section?.querySelector("#serverMonitorStatus");
    if (status) status.textContent = "Refreshing";
    try {
      await Promise.all([loadHealth(), loadLogs()]);
    } catch (error) {
      if (status) {
        status.textContent = error.message || "Unable to load monitoring data";
        status.classList.add("monitor-status-error");
      }
    }
  }

  function mount(page) {
    window.clearTimeout(monitorState.timer);
    monitorState.section = page.querySelector("#serverMonitoringSection");
    if (!monitorState.section) return;
    monitorState.section.innerHTML = `
      <header>
        <div><p class="eyebrow">Read-only diagnostics</p><h3>Server Health and Application Log</h3></div>
        <div class="monitor-header-actions">
          <span class="tag" id="serverMonitorStatus">Loading</span>
          <button class="icon-button" id="refreshServerMonitor" type="button" title="Refresh" aria-label="Refresh server monitoring">&#8635;</button>
        </div>
      </header>
      <div id="serverHealthContent" aria-live="polite"><p class="monitor-loading">Loading server health...</p></div>
      <div class="monitor-log-heading">
        <div><p class="eyebrow">Recent authenticated activity</p><h4>Application Log</h4></div>
        <div class="monitor-log-controls">
          <label>Level
            <select id="monitorLogLevel">
              <option value="ALL">All</option>
              <option value="ERROR">Error</option>
              <option value="WARNING">Warning</option>
              <option value="INFO">Information</option>
            </select>
          </label>
          <label>Entries
            <select id="monitorLogLimit">
              <option value="100">100</option>
              <option value="200" selected>200</option>
              <option value="500">500</option>
            </select>
          </label>
        </div>
      </div>
      <div class="monitor-log-list" id="applicationLogRows" aria-live="polite"><p class="monitor-loading">Loading application log...</p></div>
    `;
    monitorState.section.querySelector("#refreshServerMonitor")?.addEventListener("click", refreshAll);
    monitorState.section.querySelector("#monitorLogLevel")?.addEventListener("change", async (event) => {
      monitorState.level = event.target.value;
      await loadLogs();
    });
    monitorState.section.querySelector("#monitorLogLimit")?.addEventListener("change", async (event) => {
      monitorState.limit = Number(event.target.value);
      await loadLogs();
    });
    refreshAll();
    monitorState.timer = window.setTimeout(function refreshMonitor() {
      if (monitorState.section?.isConnected) refreshAll();
      monitorState.timer = window.setTimeout(refreshMonitor, 60000);
    }, 60000);
  }

  window.AdminMonitor = { mount };
})();

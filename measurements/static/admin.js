window.MeasurementAdmin = (() => {
  let root = null;
  let data = null;
  let activeLocation = "";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || payload.message || `Request failed (${response.status})`);
    return payload;
  }

  function locationData() {
    return data?.locations.find((location) => location.name === activeLocation) || null;
  }

  function renderStatus() {
    if (!data) return "";
    const problems = data.environment_problems || [];
    const databaseText = `Analytics: ${data.databases.analytics}; uploads: ${data.databases.uploads}`;
    if (!problems.length) return `<p class="measurement-admin-status ok">${escapeHtml(databaseText)}</p>`;
    return `
      <div class="measurement-admin-status warning">
        <strong>Configuration needs attention</strong>
        <span>${escapeHtml(problems.join(" "))}</span>
        <small>${escapeHtml(databaseText)}</small>
      </div>
    `;
  }

  function renderMappings(location) {
    if (!location.mappings.length) {
      return '<div class="admin-empty-state"><strong>No source aliases</strong><span>Map the Source values found in uploaded CSV files to systems at this location.</span></div>';
    }
    return location.mappings.map((mapping) => `
      <div class="measurement-mapping-row">
        <span><strong>${escapeHtml(mapping.system_name)}</strong><small>${escapeHtml(mapping.source_name)}</small></span>
        <button class="danger-button" type="button" data-delete-measurement-mapping="${mapping.id}">Remove</button>
      </div>
    `).join("");
  }

  function renderLocationEditor() {
    const location = locationData();
    if (!location) {
      return '<div class="admin-empty-state"><strong>No locations</strong><span>Add a system before configuring measurement uploads.</span></div>';
    }
    const config = location.config;
    const assignedSources = new Set(location.mappings.map((mapping) => mapping.source_name));
    const availableSources = location.discovered_sources.filter((source) => !assignedSources.has(source));
    return `
      <div class="measurement-client-bar">
        <div><span>Uploader client name</span><strong>${escapeHtml(location.name)}</strong></div>
        <button class="secondary-button" type="button" id="renameMeasurementLocation">Rename Location</button>
      </div>
      <p class="measurement-rename-warning">The uploader client value must exactly match this location. Renaming it updates existing measurement history, but every uploader for this client must be changed before its next upload.</p>

      <form id="measurementLocationConfig" class="measurement-config-form">
        <div class="measurement-config-pair">
          <label>Car ID filename index<input name="car_id_index" type="number" min="0" value="${config.car_id_index}" required /></label>
          <label>Body ID filename index<input name="body_id_index" type="number" min="0" value="${config.body_id_index}" required /></label>
        </div>
        ${[3, 4, 5].map((count) => `
          <label>${count}-layer names<input name="layers_${count}" value="${escapeHtml(config.layer_names[String(count)].join(", "))}" required /></label>
        `).join("")}
        <div class="admin-data-actions"><button class="primary-button" type="submit">Save Parsing Configuration</button><span class="measurement-save-message" id="measurementConfigMessage"></span></div>
      </form>

      <div class="measurement-mapping-header">
        <div><span>Robot source aliases</span><strong>One system can have multiple historical Source names</strong></div>
        <span class="tag">${location.mappings.length}</span>
      </div>
      <div class="measurement-mapping-list">${renderMappings(location)}</div>
      <form id="measurementMappingForm" class="measurement-mapping-form">
        <label>System<select name="system_id" required>
          <option value="">Choose system</option>
          ${location.systems.map((system) => `<option value="${system.id}">${escapeHtml(system.name)}</option>`).join("")}
        </select></label>
        <label>Source name<input name="source_name" list="measurementDiscoveredSources" placeholder="Exact Source value from CSV" required /></label>
        <datalist id="measurementDiscoveredSources">
          ${availableSources.map((source) => `<option value="${escapeHtml(source)}"></option>`).join("")}
        </datalist>
        <button class="secondary-button" type="submit">Add Alias</button>
      </form>
      <p class="muted admin-help">Unmapped sources discovered in imported data: ${availableSources.length ? availableSources.map(escapeHtml).join(", ") : "None"}.</p>
    `;
  }

  function paint() {
    if (!root || !data) return;
    if (!activeLocation && data.locations.length) activeLocation = data.locations[0].name;
    root.innerHTML = `
      <header><div><p class="eyebrow">Cloud measurement data</p><h3>Measurement History Configuration</h3></div><span class="tag">${data.locations.length} clients</span></header>
      ${renderStatus()}
      <label class="admin-setting-field measurement-location-picker">
        <span>Location / uploader client</span>
        <select id="measurementAdminLocation">
          ${data.locations.map((location) => `<option value="${escapeHtml(location.name)}" ${location.name === activeLocation ? "selected" : ""}>${escapeHtml(location.name)}</option>`).join("")}
        </select>
      </label>
      <div id="measurementLocationEditor">${renderLocationEditor()}</div>
    `;
    bind();
  }

  function bind() {
    root.querySelector("#measurementAdminLocation")?.addEventListener("change", (event) => {
      activeLocation = event.target.value;
      paint();
    });
    root.querySelector("#measurementLocationConfig")?.addEventListener("submit", saveConfig);
    root.querySelector("#measurementMappingForm")?.addEventListener("submit", saveMapping);
    root.querySelector("#renameMeasurementLocation")?.addEventListener("click", renameLocation);
    root.querySelectorAll("[data-delete-measurement-mapping]").forEach((button) => {
      button.addEventListener("click", () => removeMapping(Number(button.dataset.deleteMeasurementMapping)));
    });
  }

  async function reload() {
    data = await requestJson("/api/measurements/admin/config");
    if (activeLocation && !data.locations.some((location) => location.name === activeLocation)) {
      activeLocation = data.locations[0]?.name || "";
    }
    paint();
  }

  async function saveConfig(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = {
      car_id_index: Number(form.get("car_id_index")),
      body_id_index: Number(form.get("body_id_index")),
      layer_names: Object.fromEntries([3, 4, 5].map((count) => [
        String(count),
        String(form.get(`layers_${count}`) || "").split(",").map((value) => value.trim()).filter(Boolean),
      ])),
    };
    try {
      await requestJson(`/api/measurements/admin/locations/${encodeURIComponent(activeLocation)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      const message = root.querySelector("#measurementConfigMessage");
      if (message) message.textContent = "Saved";
      await reload();
    } catch (error) {
      window.alert(error.message);
    }
  }

  async function saveMapping(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await requestJson("/api/measurements/admin/mappings", {
        method: "POST",
        body: JSON.stringify({
          location: activeLocation,
          system_id: Number(form.get("system_id")),
          source_name: form.get("source_name"),
        }),
      });
      await reload();
    } catch (error) {
      window.alert(error.message);
    }
  }

  async function removeMapping(mappingId) {
    try {
      await requestJson(`/api/measurements/admin/mappings/${mappingId}`, { method: "DELETE" });
      await reload();
    } catch (error) {
      window.alert(error.message);
    }
  }

  async function renameLocation() {
    const replacement = window.prompt(
      `Rename ${activeLocation}. Existing measurement history will move to the new client name. Update every uploader before its next upload.`,
      activeLocation,
    );
    if (!replacement || replacement.trim() === activeLocation) return;
    if (!window.confirm(`Rename ${activeLocation} to ${replacement.trim()}? Uploads using the old client name will be rejected.`)) return;
    try {
      await requestJson(`/api/admin/locations/${encodeURIComponent(activeLocation)}/rename`, {
        method: "PUT",
        body: JSON.stringify({ name: replacement.trim() }),
      });
      window.location.reload();
    } catch (error) {
      window.alert(error.message);
    }
  }

  async function render(container) {
    root = container.querySelector("#measurementAdminSection");
    if (!root) return;
    root.innerHTML = '<div class="admin-empty-state"><strong>Loading measurement configuration</strong></div>';
    try {
      await reload();
    } catch (error) {
      root.innerHTML = `<div class="measurement-admin-status warning"><strong>Unable to load measurement configuration</strong><span>${escapeHtml(error.message)}</span></div>`;
    }
  }

  return { render };
})();

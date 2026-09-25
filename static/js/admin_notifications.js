(function () {
  "use strict";

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      throw new Error("Authentication required");
    }
    if (!response.ok) throw new Error(payload.message || `Request failed (${response.status})`);
    return payload;
  }

  function recipientRow(subscription = {}) {
    return `
      <div class="notification-recipient-row" data-recipient-row>
        <label class="notification-email-field">
          <span>Email address</span>
          <input type="email" data-recipient-email value="${escapeHtml(subscription.email || "")}" placeholder="name@example.com" />
        </label>
        <label class="notification-choice"><input data-recipient-updates type="checkbox" ${subscription.update_notifications ? "checked" : ""} /> Operational updates</label>
        <label class="notification-choice"><input data-recipient-daily type="checkbox" ${subscription.daily_summary ? "checked" : ""} /> Daily 5 AM summary</label>
        <button class="icon-button notification-remove" data-remove-recipient type="button" title="Remove recipient" aria-label="Remove recipient">&times;</button>
      </div>
    `;
  }

  function render(root, payload) {
    const smtp = payload.smtp || {};
    const outbox = payload.outbox || {};
    const locations = payload.locations || [];
    root.innerHTML = `
      <header class="notification-header">
        <div><p class="eyebrow">Location teams</p><h3>Email Notifications</h3></div>
        <div class="notification-health">
          <span class="notification-state ${smtp.configured ? "ready" : "setup"}">
            ${smtp.configured ? `SMTP ready: ${escapeHtml(smtp.sender)}` : "SMTP setup needed"}
          </span>
          <span class="tag">${Number(outbox.pending || 0)} pending</span>
          ${Number(outbox.failed || 0) ? `<span class="tag notification-failed">${Number(outbox.failed)} failed</span>` : ""}
        </div>
      </header>
      <div class="notification-location-list">
        ${locations.length ? locations.map((setting) => `
          <form class="notification-location" data-notification-location="${escapeHtml(setting.location)}">
            <div class="notification-location-heading">
              <strong>${escapeHtml(setting.location)}</strong>
              <span class="notification-save-state" aria-live="polite"></span>
            </div>
            <div class="notification-recipient-list">
              ${(setting.recipients || []).length
                ? setting.recipients.map(recipientRow).join("")
                : recipientRow()}
            </div>
            <div class="notification-form-actions">
              <button class="secondary-button" data-add-recipient type="button">+ Add recipient</button>
              <button class="primary-button notification-save" type="submit">Save location</button>
            </div>
          </form>
        `).join("") : '<div class="admin-empty-state"><strong>No locations</strong><span>Add a system before configuring a notification team.</span></div>'}
      </div>
      <p class="muted admin-help">The daily email summarizes the previous calendar day in the configured server timezone.</p>
    `;

    root.querySelectorAll("[data-notification-location]").forEach((form) => {
      form.addEventListener("click", (event) => {
        const addButton = event.target.closest("[data-add-recipient]");
        if (addButton) {
          form.querySelector(".notification-recipient-list").insertAdjacentHTML("beforeend", recipientRow());
          form.querySelector("[data-recipient-row]:last-child [data-recipient-email]")?.focus();
          return;
        }
        const removeButton = event.target.closest("[data-remove-recipient]");
        if (!removeButton) return;
        const rows = form.querySelectorAll("[data-recipient-row]");
        if (rows.length === 1) {
          const row = rows[0];
          row.querySelector("[data-recipient-email]").value = "";
          row.querySelector("[data-recipient-updates]").checked = false;
          row.querySelector("[data-recipient-daily]").checked = false;
          return;
        }
        removeButton.closest("[data-recipient-row]").remove();
      });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const location = form.dataset.notificationLocation;
        const button = form.querySelector(".notification-save");
        const status = form.querySelector(".notification-save-state");
        button.disabled = true;
        status.textContent = "Saving...";
        status.classList.remove("error");
        try {
          const updated = await requestJson(`/api/admin/notification-settings/${encodeURIComponent(location)}`, {
            method: "PUT",
            body: JSON.stringify({
              recipients: [...form.querySelectorAll("[data-recipient-row]")].map((row) => ({
                email: row.querySelector("[data-recipient-email]").value.trim(),
                update_notifications: row.querySelector("[data-recipient-updates]").checked,
                daily_summary: row.querySelector("[data-recipient-daily]").checked,
              })).filter((item) => item.email),
            }),
          });
          render(root, updated);
          const savedForm = [...root.querySelectorAll("[data-notification-location]")]
            .find((item) => item.dataset.notificationLocation === location);
          if (savedForm) savedForm.querySelector(".notification-save-state").textContent = "Saved";
        } catch (error) {
          status.textContent = error.message;
          status.classList.add("error");
          button.disabled = false;
        }
      });
    });
  }

  async function mount(page) {
    const root = page.querySelector("#notificationAdminSection");
    if (!root) return;
    root.innerHTML = '<div class="notification-loading">Loading email settings...</div>';
    try {
      render(root, await requestJson("/api/admin/notification-settings"));
    } catch (error) {
      root.innerHTML = `<div class="admin-empty-state"><strong>Email settings unavailable</strong><span>${escapeHtml(error.message)}</span></div>`;
    }
  }

  window.NotificationAdmin = { mount };
}());

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
            <label class="notification-recipients">
              <span>Team email addresses</span>
              <textarea name="recipients" rows="2" placeholder="name@example.com, teammate@example.com">${escapeHtml((setting.recipients || []).join("\n"))}</textarea>
            </label>
            <fieldset class="notification-options">
              <legend>Send this team</legend>
              <label><input name="update_notifications" type="checkbox" ${setting.update_notifications ? "checked" : ""} /> Operational updates</label>
              <label><input name="daily_summary" type="checkbox" ${setting.daily_summary ? "checked" : ""} /> Daily 5 AM summary</label>
            </fieldset>
            <button class="primary-button notification-save" type="submit">Save</button>
          </form>
        `).join("") : '<div class="admin-empty-state"><strong>No locations</strong><span>Add a system before configuring a notification team.</span></div>'}
      </div>
      <p class="muted admin-help">The daily email summarizes the previous calendar day in the configured server timezone.</p>
    `;

    root.querySelectorAll("[data-notification-location]").forEach((form) => {
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
              recipients: form.elements.recipients.value,
              update_notifications: form.elements.update_notifications.checked,
              daily_summary: form.elements.daily_summary.checked,
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

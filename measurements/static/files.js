const rawFilePage = {
  location: document.body.dataset.location,
};

function rawEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function rawShowError(error) {
  const banner = document.getElementById("rawFileError");
  banner.textContent = error.message || String(error);
  banner.hidden = false;
  window.setTimeout(() => { banner.hidden = true; }, 8000);
}

async function rawJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new Error("Authentication required");
  }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function rawRenderMonth(monthElement, payload) {
  const panel = monthElement.querySelector(".raw-month-panel");
  const rows = payload.files.map((file) => `
    <tr>
      <td title="${rawEscape(file.filename)}">${rawEscape(file.filename)}</td>
      <td>${rawEscape(file.job_date)} ${rawEscape(file.job_time)}</td>
      <td>${rawEscape(file.color || "-")}</td>
      <td>${rawEscape(file.size_label)}</td>
      <td><a class="secondary-button raw-download-link" href="${rawEscape(file.download_url)}">Download</a></td>
    </tr>
  `).join("");
  panel.innerHTML = `
    <div class="raw-table-wrap">
      <table class="raw-table">
        <thead><tr><th>Filename</th><th>Measurement time</th><th>Color</th><th>Size</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="raw-pagination">
      <button class="raw-page-button" type="button" data-page="${payload.page - 1}" ${payload.page <= 1 ? "disabled" : ""}>Previous</button>
      <span>Page ${payload.page} of ${payload.pages}</span>
      <button class="raw-page-button" type="button" data-page="${payload.page + 1}" ${payload.page >= payload.pages ? "disabled" : ""}>Next</button>
    </div>
  `;
  panel.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", () => rawLoadMonth(monthElement, Number(button.dataset.page)));
  });
  panel.dataset.loaded = "true";
}

async function rawLoadMonth(monthElement, page = 1) {
  const panel = monthElement.querySelector(".raw-month-panel");
  panel.innerHTML = '<p class="raw-loading">Loading files...</p>';
  const parameters = new URLSearchParams({
    location: rawFilePage.location,
    year: monthElement.dataset.year,
    month: monthElement.dataset.month,
    page: String(page),
  });
  try {
    const payload = await rawJson(`/api/measurements/files?${parameters}`);
    rawRenderMonth(monthElement, payload);
  } catch (error) {
    panel.innerHTML = "";
    rawShowError(error);
  }
}

document.querySelectorAll(".raw-month-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    const month = button.closest(".raw-month");
    const panel = month.querySelector(".raw-month-panel");
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!expanded));
    button.querySelector(".raw-month-marker").textContent = expanded ? "+" : "-";
    panel.hidden = expanded;
    if (!expanded && !panel.dataset.loaded) {
      rawLoadMonth(month);
    }
  });
});

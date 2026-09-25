const measurementPage = {
  location: document.body.dataset.location,
  initialSystemId: Number(document.body.dataset.systemId || 0),
  initialSystemName: document.body.dataset.systemName || "",
  options: null,
  performanceMode: "count",
  performanceRows: [],
};

const measurementPalette = ["#12747b", "#d08a22", "#3f6fa9", "#8b5a8f", "#5f8d42", "#b54d59", "#66727b"];
const measurementPlotConfig = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["lasso2d", "select2d"],
};

function measurementElement(id) {
  return document.getElementById(id);
}

async function measurementJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new Error("Authentication required");
  }
  if (!response.ok) throw new Error(payload.error || payload.message || `Request failed (${response.status})`);
  return payload;
}

function measurementShowError(error) {
  const banner = measurementElement("measurementError");
  banner.textContent = error.message || String(error);
  banner.hidden = false;
  window.setTimeout(() => { banner.hidden = true; }, 8000);
}

function measurementEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function measurementFillSelect(element, values, allLabel = "All") {
  const current = element.value;
  element.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = allLabel;
  element.append(all);
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    element.append(option);
  });
  if ([...element.options].some((option) => option.value === current)) element.value = current;
}

function measurementFillSources(options) {
  const select = measurementElement("measurementSource");
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "All robots";
  select.append(all);

  options.systems.forEach((system) => {
    const option = document.createElement("option");
    option.value = `system:${system.id}`;
    option.textContent = system.source_aliases.length
      ? system.name
      : `${system.name} (source not mapped)`;
    select.append(option);
  });
  options.unmapped_sources.forEach((source) => {
    const option = document.createElement("option");
    option.value = `source:${source}`;
    option.textContent = `${source} (unmapped)`;
    select.append(option);
  });
  if (measurementPage.initialSystemId) select.value = `system:${measurementPage.initialSystemId}`;
}

function measurementQuery(extra = {}) {
  const sourceValue = measurementElement("measurementSource").value;
  const parameters = new URLSearchParams({
    location: measurementPage.location,
    start: measurementElement("measurementStart").value,
    end: measurementElement("measurementEnd").value,
    color: measurementElement("measurementColor").value,
    body: measurementElement("measurementBody").value,
    ...extra,
  });
  if (sourceValue.startsWith("system:")) {
    parameters.set("system_id", sourceValue.slice(7));
  } else if (sourceValue.startsWith("source:")) {
    parameters.set("source", sourceValue.slice(7));
  }
  return parameters.toString();
}

function measurementLayout(title, yTitle) {
  return {
    title: { text: title, x: 0.03, font: { size: 19, color: "#243139" } },
    margin: { l: 72, r: 28, t: title ? 108 : 72, b: 78 },
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#ffffff",
    font: { family: "Inter, Segoe UI, sans-serif", size: 14, color: "#43515a" },
    xaxis: { gridcolor: "#e8edef", tickangle: -30, automargin: true, tickfont: { size: 13 } },
    yaxis: { title: { text: yTitle, font: { size: 14 } }, gridcolor: "#e8edef", rangemode: "tozero", tickfont: { size: 13 } },
    legend: { orientation: "h", x: 0.03, y: title ? 1.2 : 1.12, font: { size: 13 } },
    hovermode: "x unified",
  };
}

function measurementGroupedTraces(rows, valueKey) {
  const groups = new Map();
  rows.forEach((row) => {
    if (!groups.has(row.color)) groups.set(row.color, { x: [], y: [] });
    groups.get(row.color).x.push(row.job_date);
    groups.get(row.color).y.push(row[valueKey]);
  });
  return [...groups.entries()].map(([name, values], index) => ({
    type: "bar",
    name,
    x: values.x,
    y: values.y,
    marker: { color: measurementPalette[index % measurementPalette.length] },
  }));
}

function measurementPointTraces(points, valueKey = "mean", errorMode = "") {
  const groups = new Map();
  points.forEach((point) => {
    if (!groups.has(point.series)) groups.set(point.series, []);
    groups.get(point.series).push(point);
  });
  return [...groups.entries()].map(([name, values], index) => {
    const trace = {
      type: "scatter",
      mode: "lines+markers",
      name,
      x: values.map((item) => item.x),
      y: values.map((item) => item[valueKey]),
      customdata: values.map((item) => [item.count, item.min, item.max, item.stdev]),
      hovertemplate: "%{x}<br>%{y:.3f}<br>N=%{customdata[0]}<br>Min=%{customdata[1]:.3f}<br>Max=%{customdata[2]:.3f}<br>Stdev=%{customdata[3]:.3f}<extra>%{fullData.name}</extra>",
      line: { color: measurementPalette[index % measurementPalette.length] },
    };
    if (errorMode === "stdev") {
      trace.error_y = {
        type: "data",
        array: values.map((item) => item.stdev || 0),
        visible: true,
      };
    } else if (errorMode === "minmax") {
      trace.error_y = {
        type: "data",
        symmetric: false,
        array: values.map((item) => Math.max(0, Number(item.max ?? item.mean) - Number(item.mean || 0))),
        arrayminus: values.map((item) => Math.max(0, Number(item.mean || 0) - Number(item.min ?? item.mean))),
        visible: true,
      };
    }
    return trace;
  });
}

function measurementFormatNumber(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function measurementFormatDate(value) {
  if (!value) return "No data";
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" })
    .format(new Date(`${value}T12:00:00`));
}

async function measurementLoadSummary() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 29);
  const formatInputDate = (value) => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };
  const sourceValue = measurementElement("measurementSource").value;
  let scopes;
  if (sourceValue.startsWith("system:")) {
    const systemId = Number(sourceValue.slice(7));
    const system = measurementPage.options.systems.find((item) => item.id === systemId);
    scopes = [{ label: system?.name || "Selected robot", system_id: systemId }];
  } else if (sourceValue.startsWith("source:")) {
    const source = sourceValue.slice(7);
    scopes = [{ label: source, source }];
  } else {
    scopes = [
      ...measurementPage.options.systems.map((system) => ({ label: system.name, system_id: system.id })),
      ...measurementPage.options.unmapped_sources.map((source) => ({ label: `${source} (unmapped)`, source })),
    ];
  }

  const base = {
    location: measurementPage.location,
    start: formatInputDate(start),
    end: formatInputDate(end),
    color: measurementElement("measurementColor").value,
    body: measurementElement("measurementBody").value,
  };
  const summaries = await Promise.all(scopes.map(async (scope) => {
    const parameters = new URLSearchParams(base);
    if (scope.system_id) parameters.set("system_id", scope.system_id);
    if (scope.source) parameters.set("source", scope.source);
    return { scope, payload: await measurementJson(`/api/measurements/summary?${parameters}`) };
  }));

  const container = measurementElement("measurementSummaryGrid");
  if (!summaries.length) {
    container.innerHTML = '<p class="measurement-summary-empty">No robots are configured for this location.</p>';
    return;
  }
  container.innerHTML = summaries.map(({ scope, payload }) => {
    const cells = [
      ["Jobs", measurementFormatNumber(payload.jobs)],
      ["Measurements", measurementFormatNumber(payload.measurements)],
      ["Aligned", `${payload.alignment_percentage.toFixed(1)}%`],
      ["Valid", `${payload.valid_percentage.toFixed(1)}%`],
      ["Latest data", measurementFormatDate(payload.latest_date)],
    ];
    return `
      <article class="measurement-robot-summary">
        <h4>${measurementEscape(scope.label)}</h4>
        <div class="measurement-summary-grid">${cells.map(([label, value]) => `
          <div class="measurement-summary-cell"><span>${measurementEscape(label)}</span><strong>${measurementEscape(value)}</strong></div>
        `).join("")}</div>
      </article>
    `;
  }).join("");
}

async function measurementLoadOperation() {
  const payload = await measurementJson(`/api/measurements/operation?${measurementQuery()}`);
  const jobsLayout = measurementLayout("", "Jobs");
  jobsLayout.barmode = "stack";
  const countLayout = measurementLayout("", "Measurements");
  countLayout.barmode = "stack";
  Plotly.react("measurementJobsChart", measurementGroupedTraces(payload.rows, "jobs"), jobsLayout, measurementPlotConfig);
  Plotly.react("measurementCountChart", measurementGroupedTraces(payload.rows, "measurements"), countLayout, measurementPlotConfig);
}

async function measurementLoadThickness() {
  const view = measurementElement("measurementThicknessView").value;
  const variation = measurementElement("measurementThicknessVariation").value;
  const payload = await measurementJson(`/api/measurements/metrics/thickness?${measurementQuery({ view })}`);
  const titles = {
    car: "Vehicle mean thickness",
    daily: "Daily mean thickness",
    weekly: "Weekly mean thickness",
  };
  measurementElement("measurementThicknessChartTitle").textContent = titles[view];
  const layout = measurementLayout("", "Thickness");
  if (view === "car") {
    const categories = [...new Set(payload.points.map((point) => point.x))];
    const step = Math.max(1, Math.ceil(categories.length / 10));
    const ticks = categories.filter((value, index) => index % step === 0);
    if (categories.length && ticks.at(-1) !== categories.at(-1)) ticks.push(categories.at(-1));
    layout.xaxis = {
      ...layout.xaxis,
      type: "category",
      tickmode: "array",
      tickvals: ticks,
      ticktext: ticks.map((value) => value.split(" / ")[1] || value),
    };
  }
  Plotly.react(
    "measurementThicknessChart",
    measurementPointTraces(payload.points, "mean", variation),
    layout,
    measurementPlotConfig,
  );
}

function measurementRenderPerformance() {
  const rows = measurementPage.performanceRows;
  const isPercentage = measurementPage.performanceMode === "percentage";
  const title = isPercentage
    ? "Alignment and valid measurement rates"
    : "Valid measurements per day";
  measurementElement("measurementPerformanceChartTitle").textContent = title;

  let traces;
  let layout;
  if (isPercentage) {
    traces = [
      { type: "scatter", mode: "lines+markers", name: "Aligned %", x: rows.map((row) => row.date), y: rows.map((row) => row.alignment_percentage), line: { color: measurementPalette[0] } },
      { type: "scatter", mode: "lines+markers", name: "Valid %", x: rows.map((row) => row.date), y: rows.map((row) => row.valid_percentage), line: { color: measurementPalette[1] } },
    ];
    layout = measurementLayout("", "Percent");
    layout.yaxis = { ...layout.yaxis, range: [0, 100], rangemode: undefined };
  } else {
    traces = [
      { type: "bar", name: "Valid measurements", x: rows.map((row) => row.date), y: rows.map((row) => row.valid_count), marker: { color: measurementPalette[0] } },
    ];
    layout = measurementLayout("", "Valid measurements");
  }
  Plotly.react("measurementPerformanceChart", traces, layout, measurementPlotConfig);
}

async function measurementLoadPerformance() {
  const payload = await measurementJson(`/api/measurements/performance?${measurementQuery()}`);
  measurementPage.performanceRows = payload.rows;
  measurementRenderPerformance();
}

async function measurementLoadMisc() {
  const metric = measurementElement("measurementMiscMetric").value;
  if (!metric) {
    Plotly.purge("measurementMiscChart");
    return;
  }
  const view = measurementElement("measurementMiscView").value;
  const statistic = measurementElement("measurementMiscStat").value;
  const payload = await measurementJson(`/api/measurements/metrics/misc?${measurementQuery({ view, metric })}`);
  Plotly.react(
    "measurementMiscChart",
    measurementPointTraces(payload.points, statistic, statistic === "mean" ? "stdev" : ""),
    measurementLayout(`${metric.replaceAll("_", " ")} / ${statistic}`, statistic),
    measurementPlotConfig,
  );
}

async function measurementLoadStatus() {
  const status = await measurementJson(`/api/measurements/status?location=${encodeURIComponent(measurementPage.location)}`);
  measurementElement("measurementStatus").textContent = `${measurementFormatNumber(status.jobs)} jobs / ${status.last_import ? `updated ${new Date(status.last_import).toLocaleString()}` : "not imported yet"}`;
}

function measurementUpdateMappingWarning() {
  const warning = measurementElement("measurementMappingWarning");
  const value = measurementElement("measurementSource").value;
  const systemId = value.startsWith("system:") ? Number(value.slice(7)) : 0;
  const system = measurementPage.options?.systems.find((item) => item.id === systemId);
  if (system && !system.source_aliases.length) {
    warning.textContent = `${system.name} has no measurement source aliases. Add at least one mapping in the admin page.`;
    warning.classList.remove("hidden");
  } else {
    warning.classList.add("hidden");
  }
}

async function measurementLoadOptions() {
  const data = await measurementJson(`/api/measurements/options?location=${encodeURIComponent(measurementPage.location)}`);
  measurementPage.options = data;
  if (!measurementElement("measurementStart").value) {
    measurementElement("measurementStart").value = data.default_start || data.minimum_date || "";
  }
  if (!measurementElement("measurementEnd").value) {
    measurementElement("measurementEnd").value = data.maximum_date || "";
  }
  measurementFillSources(data);
  measurementFillSelect(measurementElement("measurementColor"), data.colors);
  measurementFillSelect(measurementElement("measurementBody"), data.bodies);
  measurementFillSelect(measurementElement("measurementMiscMetric"), data.misc_metrics, "Choose metric");
  measurementUpdateMappingWarning();
}

async function measurementRefreshAll() {
  if (typeof Plotly === "undefined") throw new Error("The chart library could not be loaded.");
  await Promise.all([
    measurementLoadSummary(),
    measurementLoadOperation(),
    measurementLoadThickness(),
    measurementLoadPerformance(),
    measurementLoadMisc(),
    measurementLoadStatus(),
  ]);
}

document.querySelectorAll("[data-measurement-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".measurement-tab,.measurement-panel").forEach((element) => element.classList.remove("active"));
    button.classList.add("active");
    const target = button.dataset.measurementTab;
    measurementElement(`measurementPanel${target[0].toUpperCase()}${target.slice(1)}`).classList.add("active");
    window.dispatchEvent(new Event("resize"));
  });
});

measurementElement("applyMeasurementFilters").addEventListener("click", () => {
  measurementUpdateMappingWarning();
  measurementRefreshAll().catch(measurementShowError);
});
measurementElement("measurementSource").addEventListener("change", measurementUpdateMappingWarning);
measurementElement("measurementThicknessView").addEventListener("change", () => measurementLoadThickness().catch(measurementShowError));
measurementElement("measurementThicknessVariation").addEventListener("change", () => measurementLoadThickness().catch(measurementShowError));
document.querySelectorAll("[data-performance-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    measurementPage.performanceMode = button.dataset.performanceMode;
    document.querySelectorAll("[data-performance-mode]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    measurementRenderPerformance();
  });
});
measurementElement("measurementMiscMetric").addEventListener("change", () => measurementLoadMisc().catch(measurementShowError));
measurementElement("measurementMiscView").addEventListener("change", () => measurementLoadMisc().catch(measurementShowError));
measurementElement("measurementMiscStat").addEventListener("change", () => measurementLoadMisc().catch(measurementShowError));

(async () => {
  try {
    await measurementLoadOptions();
    await measurementRefreshAll();
  } catch (error) {
    measurementShowError(error);
  }
})();

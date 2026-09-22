const measurementPage = {
  location: document.body.dataset.location,
  initialSystemId: Number(document.body.dataset.systemId || 0),
  initialSystemName: document.body.dataset.systemName || "",
  options: null,
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
    car: measurementElement("measurementCar").value,
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
    title: { text: title, x: 0.03, font: { size: 17, color: "#243139" } },
    margin: { l: 64, r: 24, t: 62, b: 74 },
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#ffffff",
    font: { family: "Inter, Segoe UI, sans-serif", color: "#43515a" },
    xaxis: { gridcolor: "#e8edef", tickangle: -30, automargin: true },
    yaxis: { title: yTitle, gridcolor: "#e8edef", rangemode: "tozero" },
    legend: { orientation: "h", y: 1.12 },
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

function measurementPointTraces(points, valueKey = "mean", includeErrors = false) {
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
      customdata: values.map((item) => [item.count, item.min, item.max]),
      hovertemplate: "%{x}<br>%{y:.3f}<br>N=%{customdata[0]}<br>Min=%{customdata[1]:.3f}<br>Max=%{customdata[2]:.3f}<extra>%{fullData.name}</extra>",
      line: { color: measurementPalette[index % measurementPalette.length] },
    };
    if (includeErrors) {
      trace.error_y = {
        type: "data",
        array: values.map((item) => item.stdev || 0),
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
  const payload = await measurementJson(`/api/measurements/summary?${measurementQuery()}`);
  const cells = [
    ["Jobs", measurementFormatNumber(payload.jobs)],
    ["Measurements", measurementFormatNumber(payload.measurements)],
    ["Aligned", `${payload.alignment_percentage.toFixed(1)}%`],
    ["Valid", `${payload.valid_percentage.toFixed(1)}%`],
    ["Latest data", measurementFormatDate(payload.latest_date)],
  ];
  measurementElement("measurementSummaryGrid").innerHTML = cells.map(([label, value]) => `
    <div class="measurement-summary-cell"><span>${measurementEscape(label)}</span><strong>${measurementEscape(value)}</strong></div>
  `).join("");
  const colors = payload.colors.length
    ? payload.colors.map((item) => `${item.color} (${item.jobs})`).join(", ")
    : "no colors recorded";
  const sourceLabel = measurementElement("measurementSource").selectedOptions[0]?.textContent || "All robots";
  measurementElement("measurementSummaryText").textContent = payload.jobs
    ? `${sourceLabel} recorded ${measurementFormatNumber(payload.jobs)} jobs from ${measurementFormatDate(payload.first_date)} through ${measurementFormatDate(payload.latest_date)}. The leading colors in this selection are ${colors}. Alignment is ${payload.alignment_percentage.toFixed(1)}% and ${payload.valid_percentage.toFixed(1)}% of deduplicated measurements are valid.`
    : `No imported measurement data matches the current ${sourceLabel} selection.`;
}

async function measurementLoadOperation() {
  const payload = await measurementJson(`/api/measurements/operation?${measurementQuery()}`);
  const jobsLayout = measurementLayout("Jobs per day by color", "Jobs");
  jobsLayout.barmode = "stack";
  const countLayout = measurementLayout("Recorded measurements per day", "Measurements");
  countLayout.barmode = "stack";
  Plotly.react("measurementJobsChart", measurementGroupedTraces(payload.rows, "jobs"), jobsLayout, measurementPlotConfig);
  Plotly.react("measurementCountChart", measurementGroupedTraces(payload.rows, "measurements"), countLayout, measurementPlotConfig);
}

function measurementFillThicknessTable(points) {
  const body = measurementElement("measurementThicknessTable").querySelector("tbody");
  body.replaceChildren();
  points.slice(0, 500).forEach((point) => {
    const row = document.createElement("tr");
    [point.x, point.series, point.count, point.mean, point.stdev, point.min, point.max].forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = typeof value === "number" && index > 2 ? value.toFixed(3) : (value ?? "-");
      row.append(cell);
    });
    body.append(row);
  });
}

async function measurementLoadThickness() {
  const view = measurementElement("measurementThicknessView").value;
  const payload = await measurementJson(`/api/measurements/metrics/thickness?${measurementQuery({ view })}`);
  Plotly.react(
    "measurementThicknessChart",
    measurementPointTraces(payload.points, "mean", true),
    measurementLayout(`${view === "car" ? "Car" : "Daily"} mean thickness`, "Thickness"),
    measurementPlotConfig,
  );
  measurementFillThicknessTable(payload.points);
}

async function measurementLoadPerformance() {
  const payload = await measurementJson(`/api/measurements/performance?${measurementQuery()}`);
  const rows = payload.rows;
  const percentageLayout = measurementLayout("Alignment and valid measurement rates", "Percent");
  percentageLayout.yaxis = { title: "Percent", range: [0, 100], gridcolor: "#e8edef" };
  Plotly.react("measurementRateChart", [
    { type: "scatter", mode: "lines+markers", name: "Aligned %", x: rows.map((row) => row.date), y: rows.map((row) => row.alignment_percentage), line: { color: measurementPalette[0] } },
    { type: "scatter", mode: "lines+markers", name: "Valid %", x: rows.map((row) => row.date), y: rows.map((row) => row.valid_percentage), line: { color: measurementPalette[1] } },
  ], percentageLayout, measurementPlotConfig);
  Plotly.react("measurementValidChart", [
    { type: "bar", name: "Valid measurements", x: rows.map((row) => row.date), y: rows.map((row) => row.valid_count), marker: { color: measurementPalette[0] } },
  ], measurementLayout("Valid measurements per day", "Valid measurements"), measurementPlotConfig);
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
    measurementPointTraces(payload.points, statistic, statistic === "mean"),
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
  measurementFillSelect(measurementElement("measurementCar"), data.cars);
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

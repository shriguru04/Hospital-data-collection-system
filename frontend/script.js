// ============================================================
// Hospital Data Collection Agent - frontend logic
// Talks to the Flask backend (see backend/app.py for routes).
// No external JS frameworks - plain DOM manipulation.
// ============================================================

const state = {
  filters: null,
  records: [],
  qualityReport: null,
  fields: [],
};

// ---------- DOM refs ----------
const chatLog = document.getElementById("chatLog");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const filterChips = document.getElementById("filterChips");
const runSearchBtn = document.getElementById("runSearchBtn");
const readyHint = document.getElementById("readyHint");

const qualityCard = document.getElementById("qualityCard");
const qualityGrid = document.getElementById("qualityGrid");

const resultsCard = document.getElementById("resultsCard");
const resultsHead = document.getElementById("resultsHead");
const resultsBody = document.getElementById("resultsBody");
const recordCount = document.getElementById("recordCount");

const exportCard = document.getElementById("exportCard");
const formatSelect = document.getElementById("formatSelect");
const exportBtn = document.getElementById("exportBtn");
const downloadArea = document.getElementById("downloadArea");

const customHeaders = document.getElementById("customHeaders");
const guessMappingBtn = document.getElementById("guessMappingBtn");
const mappingTable = document.getElementById("mappingTable");

const sourcePill = document.getElementById("sourcePill");
const plannerPill = document.getElementById("plannerPill");
const footerSourceMode = document.getElementById("footerSourceMode");

// ---------- Pipeline ledger ----------
const LEDGER_STEPS = Array.from(document.querySelectorAll(".ledger__list li"));

function setStamp(step, status) {
  const li = LEDGER_STEPS.find((el) => Number(el.dataset.step) === step);
  if (!li) return;
  const stamp = li.querySelector(".stamp");
  li.classList.toggle("is-active", status === "running");

  stamp.classList.remove("stamp--running", "stamp--done", "stamp--skip", "stamp--warn");
  switch (status) {
    case "running":
      stamp.textContent = "running";
      stamp.classList.add("stamp--running");
      break;
    case "done":
      stamp.textContent = "done";
      stamp.classList.add("stamp--done");
      break;
    case "skip":
      stamp.textContent = "n/a";
      stamp.classList.add("stamp--skip");
      break;
    case "warn":
      stamp.textContent = "issues";
      stamp.classList.add("stamp--warn");
      break;
    default:
      stamp.textContent = "pending";
  }
}

function resetLedgerFrom(step) {
  for (let i = step; i <= 10; i++) setStamp(i, "pending");
}

// ---------- Chat ----------
function addMessage(text, role) {
  const div = document.createElement("div");
  div.className = `msg msg--${role}`;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function renderFilterChips(filters) {
  filterChips.innerHTML = "";
  if (!filters) return;

  const entries = [
    ["area", "Area"],
    ["city", "City"],
    ["state", "State"],
    ["country", "Country"],
    ["postal_code", "Postal code"],
    ["hospital_type", "Type"],
    ["specialty", "Specialty"],
    ["min_rating", "Min rating"],
    ["emergency_only", "Emergency only"],
    ["num_records", "Limit"],
    ["field_preset", "Fields"],
    ["output_format", "Format"],
  ];

  entries.forEach(([key, label]) => {
    const value = filters[key];
    if (value === null || value === undefined || value === false || value === "") return;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `${label}: <strong>${value === true ? "yes" : value}</strong>`;
    filterChips.appendChild(chip);
  });
}

async function sendChat(message) {
  addMessage(message, "user");
  chatInput.value = "";

  setStamp(1, "running");
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, filters: state.filters }),
    });
    const data = await resp.json();

    state.filters = data.filters;
    renderFilterChips(state.filters);
    addMessage(data.reply, "agent");

    setStamp(1, "done");
    setStamp(2, data.ready ? "done" : "pending");

    runSearchBtn.disabled = !data.ready;
    readyHint.textContent = data.ready
      ? "Ready - run the search to fetch matching hospitals."
      : "Add a location to enable search.";

    // A new chat message invalidates downstream results
    resetLedgerFrom(3);
    resultsCard.hidden = true;
    qualityCard.hidden = true;
    exportCard.hidden = true;
  } catch (err) {
    addMessage(`Could not reach the agent backend: ${err.message}`, "error");
    setStamp(1, "pending");
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  sendChat(message);
});

// ---------- Search ----------
runSearchBtn.addEventListener("click", async () => {
  if (!state.filters) return;

  setStamp(3, "running");
  runSearchBtn.disabled = true;

  try {
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filters: state.filters }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      addMessage(data.error || "Search failed.", "error");
      resetLedgerFrom(3);
      return;
    }

    setStamp(3, "done");
    setStamp(4, "done");

    if (!data.records || data.records.length === 0) {
      addMessage(data.message || "No matching hospitals found for the selected filters.", "agent");
      setStamp(5, "skip");
      setStamp(6, "skip");
      setStamp(7, "skip");
      setStamp(8, "skip");
      qualityCard.hidden = true;
      resultsCard.hidden = true;
      exportCard.hidden = true;
      return;
    }

    state.records = data.records;
    state.qualityReport = data.quality_report;
    state.fields = state.filters.fields || Object.keys(data.records[0]).filter((k) => !k.startsWith("_"));

    const hasIssues = (data.quality_report?.rows_with_issues || 0) > 0;
    setStamp(5, hasIssues ? "warn" : "done");
    setStamp(6, "done");
    setStamp(7, "done");
    setStamp(8, "done");

    renderQualityReport(data.quality_report);
    renderResultsTable(state.records, state.fields);

    addMessage(
      `Found ${data.records.length} hospital record(s) after cleaning and deduplication. ` +
        `Review the table on the right, then export below.`,
      "agent"
    );

    exportCard.hidden = false;
  } catch (err) {
    addMessage(`Search request failed: ${err.message}`, "error");
    resetLedgerFrom(3);
  } finally {
    runSearchBtn.disabled = false;
  }
});

// ---------- Quality report ----------
function renderQualityReport(report) {
  qualityGrid.innerHTML = "";
  if (!report) {
    qualityCard.hidden = true;
    return;
  }

  const rows = [
    ["Data source", report.source],
    ["Total raw records", report.total_raw],
    ["Duplicates removed", report.duplicates_removed],
    ["Rows with validation issues", report.rows_with_issues],
    ["Total returned", report.total_returned],
  ];

  if (report.issue_breakdown) {
    Object.entries(report.issue_breakdown).forEach(([key, val]) => {
      if (val > 0) rows.push([`Issue: ${key.replace(/_/g, " ")}`, val]);
    });
  }

  rows.forEach(([label, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    qualityGrid.appendChild(dt);
    qualityGrid.appendChild(dd);
  });

  qualityCard.hidden = false;
}

// ---------- Results table ----------
function renderResultsTable(records, fields) {
  resultsHead.innerHTML = "";
  resultsBody.innerHTML = "";

  fields.forEach((field) => {
    const th = document.createElement("th");
    th.textContent = field.replace(/_/g, " ");
    resultsHead.appendChild(th);
  });

  records.forEach((record) => {
    const tr = document.createElement("tr");
    fields.forEach((field) => {
      const td = document.createElement("td");
      const value = record[field];
      td.textContent = value === null || value === undefined ? "N/A" : value;
      if (value === "N/A") td.classList.add("cell--na");
      tr.appendChild(td);
    });
    resultsBody.appendChild(tr);
  });

  recordCount.textContent = `${records.length} record(s)`;
  resultsCard.hidden = false;
}

// ---------- Custom schema mapping ----------
guessMappingBtn.addEventListener("click", async () => {
  const raw = customHeaders.value.trim();
  if (!raw) {
    mappingTable.innerHTML = "";
    return;
  }
  const headers = raw.split(",").map((h) => h.trim()).filter(Boolean);

  const resp = await fetch("/api/schema/guess", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ headers }),
  });
  const data = await resp.json();
  renderMappingTable(data.mapping);
});

function renderMappingTable(mapping) {
  const allFields = [
    "hospital_name", "phone", "alternate_phone", "email", "website", "address",
    "area", "city", "state", "country", "postal_code", "emergency_contact",
    "hospital_type", "number_of_beds", "specializations", "departments",
    "doctor_information", "rating", "latitude", "longitude",
  ];

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>Your column</th><th>Maps to</th></tr>";
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  mapping.forEach((m, idx) => {
    const tr = document.createElement("tr");
    const tdCol = document.createElement("td");
    tdCol.textContent = m.column;

    const tdField = document.createElement("td");
    const select = document.createElement("select");
    select.dataset.column = m.column;
    select.dataset.idx = idx;

    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = "(none / N/A)";
    select.appendChild(noneOpt);

    allFields.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      if (f === m.field) opt.selected = true;
      select.appendChild(opt);
    });

    tdField.appendChild(select);
    tr.appendChild(tdCol);
    tr.appendChild(tdField);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  mappingTable.innerHTML = "";
  mappingTable.appendChild(table);
}

function getCustomMapping() {
  const selects = mappingTable.querySelectorAll("select");
  if (selects.length === 0) return null;
  return Array.from(selects).map((sel) => ({
    column: sel.dataset.column,
    field: sel.value || null,
  }));
}

// ---------- Export ----------
exportBtn.addEventListener("click", async () => {
  if (state.records.length === 0) return;

  setStamp(9, "running");
  exportBtn.disabled = true;
  downloadArea.innerHTML = "";

  const customMapping = getCustomMapping();
  const fmt = formatSelect.value;

  try {
    const resp = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        records: state.records,
        fields: state.fields,
        format: fmt,
        quality_report: state.qualityReport,
        filters: state.filters,
        custom_mapping: customMapping,
      }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      addMessage(data.error || "Export failed.", "error");
      resetLedgerFrom(9);
      return;
    }

    setStamp(9, "done");
    setStamp(10, "done");

    const link = document.createElement("a");
    link.href = data.download_url;
    link.textContent = `Download ${data.filename}`;
    link.setAttribute("download", data.filename);
    downloadArea.appendChild(link);

    addMessage(`Export ready: ${data.filename} (${fmt.toUpperCase()}).`, "agent");
  } catch (err) {
    addMessage(`Export request failed: ${err.message}`, "error");
    resetLedgerFrom(9);
  } finally {
    exportBtn.disabled = false;
  }
});

// ---------- Health check / init ----------
async function init() {
  addMessage(
    "Tell me what hospital data you need - location is required " +
      '(e.g. "hospitals in Andheri, Mumbai, Maharashtra, India"). ' +
      "You can also specify hospital type, specialty, minimum rating, " +
      'emergency-only, "top N", which fields ("name and phone", "detailed", ' +
      '"full"), and an export format (xlsx, csv, json, pdf).',
    "agent"
  );

  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();

    sourcePill.textContent = `source: ${data.data_source_mode}`;
    sourcePill.classList.add(data.data_source_mode === "mock" ? "pill--off" : "pill--on");

    plannerPill.textContent = `llm planner: ${data.llm_planner_enabled ? "on" : "off"}`;
    plannerPill.classList.add(data.llm_planner_enabled ? "pill--on" : "pill--off");

    footerSourceMode.textContent = data.data_source_mode;
  } catch (err) {
    sourcePill.textContent = "source: unknown";
    plannerPill.textContent = "backend offline";
  }
}

init();

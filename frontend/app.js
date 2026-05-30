const API_DEFAULT = "https://care-planner-api-fmp7.onrender.com";
const SAME_ORIGIN_API =
  window.location.protocol.startsWith("http")
    ? window.location.origin
    : API_DEFAULT;

const scenarios = {
  infant: {
    mode: "MONITOR",
    age_months: 2,
    domains: [
      ["Motor Development", 63.5],
      ["Communication", 72.0],
      ["Social Engagement", 70.5],
    ],
    activities: [
      ["Tummy Time Reach", "Place baby on tummy and hold a bright toy just out of reach."],
      ["Supported Sitting Wobble", "Sit behind baby and gently tilt them side to side."],
      ["Kick and Bat", "Hold a rattle above feet then hands and encourage kicking or batting."],
    ],
  },
  toddler: {
    mode: "CONCERN",
    age_months: 18,
    domains: [
      ["Motor Development", 60.0],
      ["Communication", 58.5],
      ["Social Engagement", 75.0],
    ],
    activities: [
      ["Ball Roll and Chase", "Roll a soft ball and encourage toddler to chase and bring it back."],
      ["Point and Name", "Point to common objects around the house, say the name, wait for imitation."],
      ["Peek-a-Boo Variations", "Use a cloth to hide your face and different objects. Vary pace and surprise."],
    ],
  },
  child: {
    mode: "LEARN",
    age_months: 6,
    domains: [
      ["Motor Development", 78.0],
      ["Communication", 80.0],
      ["Social Engagement", 76.5],
    ],
    activities: [
      ["Reach and Roll", "Place a soft toy slightly to one side and encourage baby to reach and roll toward it."],
      ["Sound Turn", "Make a gentle sound from one side and wait for baby to turn toward you."],
      ["Mirror Smile", "Sit with baby in front of a mirror and copy their smiles and sounds."],
    ],
  },
};

const form = document.querySelector("#plannerForm");
const apiBaseInput = document.querySelector("#apiBase");
const scenarioSelect = document.querySelector("#scenario");
const domainsEl = document.querySelector("#domains");
const activitiesEl = document.querySelector("#activities");
const submitBtn = document.querySelector("#submitBtn");
const healthBtn = document.querySelector("#healthBtn");
const statusPill = document.querySelector("#statusPill");
const message = document.querySelector("#message");
const progress = document.querySelector("#progress");
const resultControls = document.querySelector("#resultControls");
const dayFilters = document.querySelector("#dayFilters");
const copyBtn = document.querySelector("#copyBtn");
const downloadBtn = document.querySelector("#downloadBtn");
const summary = document.querySelector("#summary");
const tips = document.querySelector("#tips");
const plan = document.querySelector("#plan");
const resultTitle = document.querySelector("#resultTitle");

let latestPlan = null;
let selectedDay = "all";

function setStatus(text, kind = "") {
  statusPill.textContent = text;
  statusPill.className = `status-pill ${kind}`.trim();
}

function setMessage(text, kind = "") {
  message.textContent = text;
  message.className = `message ${kind}`.trim();
}

function setProgress(stepIndex) {
  progress.classList.remove("hidden");
  [...progress.querySelectorAll(".progress-step")].forEach((step, index) => {
    step.classList.toggle("active", index <= stepIndex);
  });
}

function cleanBaseUrl() {
  return apiBaseInput.value.trim().replace(/\/+$/, "");
}

function createRows() {
  const domainTemplate = document.querySelector("#domainTemplate");
  const activityTemplate = document.querySelector("#activityTemplate");

  domainsEl.replaceChildren();
  activitiesEl.replaceChildren();

  for (let i = 0; i < 3; i += 1) {
    domainsEl.appendChild(domainTemplate.content.cloneNode(true));
    activitiesEl.appendChild(activityTemplate.content.cloneNode(true));
  }
}

function setMode(mode) {
  const input = form.querySelector(`input[name="mode"][value="${mode}"]`);
  if (input) input.checked = true;
}

function loadScenario(key) {
  const scenario = scenarios[key];
  setMode(scenario.mode);
  document.querySelector("#ageMonths").value = scenario.age_months;

  [...domainsEl.querySelectorAll(".domain-row")].forEach((row, index) => {
    row.querySelector(".domain-name").value = scenario.domains[index][0];
    row.querySelector(".domain-score").value = scenario.domains[index][1];
  });

  [...activitiesEl.querySelectorAll(".activity-row")].forEach((row, index) => {
    row.querySelector(".activity-title").value = scenario.activities[index][0];
    row.querySelector(".activity-description").value = scenario.activities[index][1];
  });
}

function buildPayload() {
  const domainRows = [...domainsEl.querySelectorAll(".domain-row")];
  const activityRows = [...activitiesEl.querySelectorAll(".activity-row")];
  const domains = domainRows.map((row) => ({
    name: row.querySelector(".domain-name").value.trim(),
    zscore: Number(row.querySelector(".domain-score").value),
  }));

  return {
    mode: form.querySelector('input[name="mode"]:checked').value,
    age_months: Number(document.querySelector("#ageMonths").value),
    domain1: domains[0],
    domain2: domains[1],
    domain3: domains[2],
    day1_activities: activityRows.map((row) => ({
      title: row.querySelector(".activity-title").value.trim(),
      description: row.querySelector(".activity-description").value.trim(),
      image_url: null,
    })),
  };
}

function validatePayload(payload) {
  if (!payload.age_months && payload.age_months !== 0) return "Age is required.";
  if (payload.age_months < 0 || payload.age_months > 216) return "Age must be between 0 and 216 months.";
  for (const domain of [payload.domain1, payload.domain2, payload.domain3]) {
    if (!domain.name) return "Each domain needs a name.";
    if (Number.isNaN(domain.zscore) || domain.zscore < 0 || domain.zscore > 100) {
      return "Each domain score must be between 0 and 100.";
    }
  }
  for (const activity of payload.day1_activities) {
    if (!activity.title || !activity.description) return "Each Day 1 activity needs a title and description.";
  }
  return "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderSummary(data) {
  const chips = (data.focus_domains || [])
    .map((domain) => `<span class="chip">${escapeHtml(domain)}</span>`)
    .join("");

  summary.innerHTML = `
    <p>${escapeHtml(data.child_summary || "Plan generated successfully.")}</p>
    <div class="chips">${chips || '<span class="chip">No priority domains</span>'}</div>
  `;
  summary.classList.remove("hidden");
}

function renderTips(items = []) {
  tips.innerHTML = items.map((tip) => `<div class="tip">${escapeHtml(tip)}</div>`).join("");
  tips.classList.toggle("hidden", items.length === 0);
}

function renderControls(days = []) {
  dayFilters.innerHTML = [
    `<button class="day-filter ${selectedDay === "all" ? "active" : ""}" type="button" data-day="all">All</button>`,
    ...days.map((day) => (
      `<button class="day-filter ${String(day.day) === String(selectedDay) ? "active" : ""}" type="button" data-day="${escapeHtml(day.day)}">Day ${escapeHtml(day.day)}</button>`
    )),
  ].join("");
  resultControls.classList.toggle("hidden", days.length === 0);
}

function renderPlan(days = []) {
  const visibleDays = selectedDay === "all"
    ? days
    : days.filter((day) => String(day.day) === String(selectedDay));

  if (visibleDays.length === 0) {
    plan.innerHTML = '<div class="empty-state">No activities for this filter.</div>';
    return;
  }

  plan.innerHTML = visibleDays.map((day) => `
    <article class="day">
      <div class="day-heading">
        <h3>Day ${escapeHtml(day.day)}: ${escapeHtml(day.theme)}</h3>
        <span>${escapeHtml(day.activities?.length || 0)} activities</span>
      </div>
      ${(day.activities || []).map(renderActivity).join("")}
    </article>
  `).join("");
}

function renderActivity(activity) {
  const image = activity.image_url
    ? `<img class="activity-image" src="${escapeHtml(activity.image_url)}" alt="">`
    : `<div class="activity-image" aria-hidden="true"></div>`;

  const steps = (activity.steps || [])
    .map((step) => `<li>${escapeHtml(step)}</li>`)
    .join("");
  const keywords = (activity.image_keywords || [])
    .map((keyword) => `<span class="keyword">${escapeHtml(keyword)}</span>`)
    .join("");

  return `
    <div class="activity-card collapsed">
      ${image}
      <div>
        <div class="activity-meta">
          <span class="badge ${activity.is_priority ? "priority" : ""}">${escapeHtml(activity.domain_focus)}</span>
          <span class="duration">${escapeHtml(activity.duration)}</span>
        </div>
        <div class="activity-title-row">
          <h3>${escapeHtml(activity.title)}</h3>
          <button class="toggle-activity" type="button" aria-label="Expand activity">+</button>
        </div>
        <div class="activity-body">
          <p>${escapeHtml(activity.description)}</p>
          <ol class="steps">${steps}</ol>
          <div class="activity-tip">${escapeHtml(activity.tip)}</div>
          <div class="keywords">${keywords}</div>
        </div>
      </div>
    </div>
  `;
}

async function revealPlan(data) {
  latestPlan = data;
  selectedDay = "all";
  resultTitle.textContent = "Generated care plan";
  setStatus("Rendering", "ok");
  setMessage("Plan generated. Laying it out day by day...");
  setProgress(2);
  renderSummary(data);
  renderTips(data.general_tips || []);
  renderControls(data.plan || []);

  plan.replaceChildren();
  for (const day of data.plan || []) {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderDay(day);
    plan.appendChild(wrapper.firstElementChild);
    await wait(140);
  }

  setStatus("Complete", "ok");
  setMessage("Plan generated successfully. Use the day chips to focus the week, or expand activities for step-by-step detail.");
  progress.classList.add("hidden");
}

function renderDay(day) {
  return `
    <article class="day">
      <div class="day-heading">
        <h3>Day ${escapeHtml(day.day)}: ${escapeHtml(day.theme)}</h3>
        <span>${escapeHtml(day.activities?.length || 0)} activities</span>
      </div>
      ${(day.activities || []).map(renderActivity).join("")}
    </article>
  `;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function checkHealth() {
  setStatus("Checking");
  setMessage("Checking API health...");
  try {
    const response = await fetch(`${cleanBaseUrl()}/health`);
    if (!response.ok) throw new Error(`Health check failed with ${response.status}`);
    const data = await response.json();
    setStatus("Healthy", "ok");
    setMessage(`Connected to ${data.service}. Provider: ${data.llm_provider}, model: ${data.model}.`);
  } catch (error) {
    setStatus("Error", "error");
    setMessage(formatFetchError(error));
  }
}

async function generatePlan(event) {
  event.preventDefault();
  const payload = buildPayload();
  const validation = validatePayload(payload);
  if (validation) {
    setStatus("Fix input", "error");
    setMessage(validation);
    return;
  }

  submitBtn.disabled = true;
  setStatus("Generating");
  setMessage("Generating the weekly plan. This can take a little while on a cold Render service.");
  setProgress(0);
  resultTitle.textContent = "Generating plan";
  latestPlan = null;
  selectedDay = "all";
  summary.classList.add("hidden");
  tips.classList.add("hidden");
  resultControls.classList.add("hidden");
  plan.replaceChildren();

  try {
    setProgress(1);
    const response = await fetch(`${cleanBaseUrl()}/care-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed with ${response.status}`);

    await revealPlan(data);
  } catch (error) {
    resultTitle.textContent = "Generation failed";
    setStatus("Error", "error");
    setMessage(formatFetchError(error));
    progress.classList.add("hidden");
  } finally {
    submitBtn.disabled = false;
  }
}

async function copyLatestPlan() {
  if (!latestPlan) return;
  await navigator.clipboard.writeText(JSON.stringify(latestPlan, null, 2));
  setMessage("Copied the full JSON response to clipboard.");
}

function downloadLatestPlan() {
  if (!latestPlan) return;
  const blob = new Blob([JSON.stringify(latestPlan, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "care-plan.json";
  link.click();
  URL.revokeObjectURL(url);
  setMessage("Downloaded care-plan.json.");
}

function formatFetchError(error) {
  if (error instanceof TypeError && error.message === "Failed to fetch") {
    return [
      "Could not reach the API from this page.",
      "Check that the API Base URL is correct, the Render service is awake, and the latest backend with CORS/frontend support has been deployed.",
    ].join(" ");
  }
  return error.message || "Something went wrong while contacting the API.";
}

createRows();
loadScenario("infant");
apiBaseInput.value = localStorage.getItem("carePlannerApiBase") || SAME_ORIGIN_API;

scenarioSelect.addEventListener("change", () => loadScenario(scenarioSelect.value));
apiBaseInput.addEventListener("change", () => localStorage.setItem("carePlannerApiBase", cleanBaseUrl()));
healthBtn.addEventListener("click", checkHealth);
form.addEventListener("submit", generatePlan);
copyBtn.addEventListener("click", copyLatestPlan);
downloadBtn.addEventListener("click", downloadLatestPlan);
dayFilters.addEventListener("click", (event) => {
  const button = event.target.closest(".day-filter");
  if (!button || !latestPlan) return;
  selectedDay = button.dataset.day;
  renderControls(latestPlan.plan || []);
  renderPlan(latestPlan.plan || []);
});
plan.addEventListener("click", (event) => {
  const button = event.target.closest(".toggle-activity");
  if (!button) return;
  const card = button.closest(".activity-card");
  const collapsed = card.classList.toggle("collapsed");
  button.textContent = collapsed ? "+" : "-";
  button.setAttribute("aria-label", collapsed ? "Expand activity" : "Collapse activity");
});

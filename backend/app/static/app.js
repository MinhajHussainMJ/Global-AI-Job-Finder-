/* Global AI Job Finder — frontend SPA (vanilla JS, no build step).
   Talks only to same-origin /api endpoints; no keys ever live here. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let TOKEN = localStorage.getItem("jf_token") || "";
let ME = null;
let SOURCES = [];
let jobsPage = 1, jobsTotal = 0;

async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); delete opts.json; }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401 && TOKEN) { logout(); throw new Error("Session expired — please log in again."); }
  let data = null;
  try { data = await res.json(); } catch { /* non-json */ }
  if (!res.ok) throw new Error((data && data.detail) ? JSON.stringify(data.detail) === data.detail ? data.detail : data.detail : `HTTP ${res.status}`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (isErr ? " err" : "");
  setTimeout(() => t.classList.add("hidden"), 4200);
}

/* ---------------- auth ---------------- */
$$(".tab").forEach((b) => b.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.remove("active")); b.classList.add("active");
  $("#form-login").classList.toggle("hidden", b.dataset.authtab !== "login");
  $("#form-register").classList.toggle("hidden", b.dataset.authtab !== "register");
}));

$("#form-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const d = await api("/api/auth/login", { method: "POST", json: { email: f.get("email"), password: f.get("password") } });
    enterApp(d.token, d.user);
  } catch (err) { setMsg("#auth-msg", err.message, true); }
});

$("#form-register").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const d = await api("/api/auth/register", { method: "POST", json: {
      email: f.get("email"), password: f.get("password"), full_name: f.get("full_name"), mode: f.get("mode") } });
    enterApp(d.token, d.user);
  } catch (err) { setMsg("#auth-msg", err.message, true); }
});

function setMsg(sel, text, isErr = false) {
  const el = $(sel); if (!el) return;
  el.textContent = text; el.className = "msg " + (isErr ? "err" : "ok");
}

function enterApp(token, user) {
  TOKEN = token; localStorage.setItem("jf_token", token); ME = user;
  $("#view-auth").classList.add("hidden"); $("#shell").classList.remove("hidden");
  $("#user-chip").textContent = `${user.full_name || user.email} · ${user.mode}`;
  $("#consent-banner").classList.toggle("hidden", !!user.consent_accepted);
  route(location.hash.slice(2) || "/dashboard");
}

function logout() {
  TOKEN = ""; ME = null; localStorage.removeItem("jf_token");
  $("#shell").classList.add("hidden"); $("#view-auth").classList.remove("hidden");
}
$("#btn-logout").addEventListener("click", logout);

$("#consent-accept").addEventListener("click", async () => {
  try { const u = await api("/api/auth/consent", { method: "POST", json: { accept: true } });
    ME = u; $("#consent-banner").classList.add("hidden"); toast("Consent recorded ✔"); }
  catch (e) { toast(e.message, true); }
});
$("#consent-decline").addEventListener("click", logout);

/* ---------------- routing ---------------- */
const PAGES = ["dashboard", "jobs", "applications", "profile", "preferences", "integrations", "settings"];
window.addEventListener("hashchange", () => route(location.hash.slice(2)));

function route(path) {
  const page = (path || "/dashboard").slice(1);
  if (!PAGES.includes(page)) return location.hash = "#/dashboard";
  if (!TOKEN) return;
  PAGES.forEach((p) => $("#view-" + p).classList.toggle("hidden", p !== page));
  $$(".nav-link").forEach((a) => a.classList.toggle("active", a.dataset.route === page));
  $("#sidenav").classList.remove("open");
  ({ dashboard: loadDashboard, jobs: loadJobs, applications: loadApplications,
     profile: loadProfile, preferences: loadPreferences, integrations: loadIntegrations,
     settings: loadSettings })[page]().catch((e) => toast(e.message, true));
}
$("#nav-toggle").addEventListener("click", () => $("#sidenav").classList.toggle("open"));

/* ---------------- dashboard ---------------- */
const STAT_DEFS = [
  ["discovered", "Jobs discovered", "acc"], ["matched", "Matching profile", "ok"],
  ["prepared", "Prepared", ""], ["awaiting_user", "Awaiting you", "warn"],
  ["submitted", "Submitted*", "ok"], ["interviews", "Interviews", "ok"],
  ["rejections", "Rejections", "bad"], ["expired_jobs", "Expired", "bad"], ["failed", "Failed", "bad"]];

async function loadDashboard() {
  const d = await api("/api/applications/dashboard");
  $("#dash-counts").innerHTML = STAT_DEFS.map(([k, label, cls]) =>
    `<div class="stat ${cls}"><b>${d.counts[k]}</b>${esc(label)}</div>`).join("") +
    `<div class="card span2 small muted" style="width:100%">* “Submitted” appears only after you record a real confirmation reference.</div>`;
  $("#dash-deadlines").innerHTML = d.upcoming_deadlines.length ? d.upcoming_deadlines.map((x) =>
    `<div class="row gap"><span class="tag">due ${new Date(x.expires_at).toLocaleDateString()}</span>
     <a href="#/jobs" onclick="openJob(${x.job_id})">${esc(x.title)} — ${esc(x.company)}</a></div>`).join("")
    : `<div class="empty">None in the next 7 days.</div>`;
  $("#dash-awaiting").innerHTML =
    d.recent_applications.filter((a) => a.status === "awaiting_user" || a.status === "prepared").map(appRow).join("") +
    (d.follow_ups_due.length ? d.follow_ups_due.map((f) =>
      `<div class="row gap"><span class="tag">follow-up ${new Date(f.follow_up_at).toLocaleDateString()}</span> app #${f.app_id}</div>`).join("") : "")
    || `<div class="empty">Nothing awaiting action.</div>`;
  $("#dash-recent").innerHTML = d.recent_applications.length ? d.recent_applications.map(appRow).join("")
    : `<div class="empty">No applications yet. Set Auto-Apply mode to “Prepare” in Preferences, or prepare one from the Jobs page.</div>`;
  const logs = await api("/api/search/logs?limit=6");
  $("#dash-log").innerHTML = logs.length ? logs.map((l) =>
    `<div>▶ ${new Date(l.started_at).toLocaleString()} (${esc(l.triggered_by)}) — found ${l.found}, added ${l.added}, dupes ${l.duplicates}
     ${l.errors.length ? `<span class="msg err">⚠ ${esc(l.errors.join(" | "))}</span>` : ""}</div>`).join("")
    : `<div class="empty">No searches yet. Press “Run job search now”.</div>`;
  if (d.last_search) $("#search-result-note").textContent = "";
}

$("#btn-run-search").addEventListener("click", async () => {
  const btn = $("#btn-run-search"); btn.disabled = true; btn.textContent = "Searching…";
  try {
    const r = await api("/api/search/run", { method: "POST" });
    $("#search-result-note").innerHTML = `Found ${r.found} listings → <b>${r.added} new</b>, ${r.duplicates_removed} duplicates removed. ` +
      (r.errors.length ? `<span class="msg err">Warnings: ${esc(r.errors.join(" | "))}</span>` : esc(r.note));
    loadDashboard();
  } catch (e) { setMsg("#search-result-note", e.message, true); }
  btn.disabled = false; btn.textContent = "▶ Run job search now";
});

/* ---------------- jobs ---------------- */
function scoreClass(s) { return s >= 70 ? "hi" : s >= 55 ? "mid" : "lo"; }

function jobCard(j) {
  const reasons = (j.match_reasons || []).map((r) => `<li>✔ ${esc(r)}</li>`).join("");
  const missing = (j.missing_skills || []).map((m) => `<li class="miss">✖ ${esc(m)}</li>`).join("");
  const elig = (j.eligibility_notes || []).map((m) => `<li class="elig">⚑ ${esc(m)}</li>`).join("");
  return `<div class="job-card" id="job-${j.id}">
    <div class="job-top">
      <div><h3>${esc(j.title)}</h3>
        <div class="job-meta">${esc(j.company)} · ${esc(j.location || j.country || "—")} ·
          ${j.posted_at ? "posted " + new Date(j.posted_at).toLocaleDateString() : ""} ${j.salary ? "· " + esc(j.salary) : ""}</div>
        <div class="tags"><span class="tag">${esc(j.source)}</span>${j.remote ? '<span class="tag remote">Remote</span>' : ""}
          <span class="tag ${j.status}">${esc(j.status)}</span>${j.application_status ? `<span class="tag">app: ${esc(j.application_status)}</span>` : ""}</div>
      </div>
      <div class="score ${j.match_score != null ? scoreClass(j.match_score) : ""}">${j.match_score != null ? j.match_score : "?"}</div>
    </div>
    <p class="small muted">${esc(j.description_preview)}…</p>
    <ul class="reasons">${reasons}${missing}${elig}</ul>
    <div class="actions">
      <a class="btn primary" target="_blank" rel="noopener noreferrer" href="${esc(j.url)}">Open original listing ↗</a>
      <button class="btn" onclick="openJob(${j.id})">Details</button>
      <button class="btn" onclick="prepareJob(${j.id}, this)">✍ Prepare application</button>
      <button class="btn ghost" onclick="bookmark(${j.id}, this)">${j.bookmarked ? "★ Bookmarked" : "☆ Bookmark"}</button>
      <button class="btn ghost" onclick="hideJob(${j.id})">Hide</button>
    </div></div>`;
}

async function loadJobs(reset = true) {
  if (reset) jobsPage = 1;
  const q = encodeURIComponent($("#jobs-q").value.trim());
  const st = $("#jobs-status").value, sc = $("#jobs-score").value, bm = $("#jobs-bookmarked").checked;
  const d = await api(`/api/jobs?q=${q}&status=${st}&min_score=${sc}&bookmarked=${bm}&page=${jobsPage}`);
  jobsTotal = d.total;
  const html = d.items.map(jobCard).join("") || `<div class="empty card">No jobs stored yet — run a search from the Dashboard. Results appear only from genuinely connected sources.</div>`;
  if (reset) $("#jobs-list").innerHTML = html; else $("#jobs-list").insertAdjacentHTML("beforeend", html);
  const shown = $("#jobs-list").children.length;
  $("#jobs-more").classList.toggle("hidden", shown >= d.total);
}
["jobs-refresh"].forEach((id) => $("#" + id).addEventListener("click", () => loadJobs(true)));
["jobs-q", "jobs-status", "jobs-score", "jobs-bookmarked"].forEach((id) =>
  $("#" + id).addEventListener("change", () => loadJobs(true)));
$("#jobs-q").addEventListener("input", () => { clearTimeout(window._jt); window._jt = setTimeout(() => loadJobs(true), 400); });
$("#jobs-more").addEventListener("click", () => { jobsPage++; loadJobs(false); });

window.openJob = async (id) => {
  const j = await api("/api/jobs/" + id);
  $("#modal-body").innerHTML = `<h3>${esc(j.title)}</h3>
    <div class="job-meta">${esc(j.company)} · ${esc(j.location || j.country || "—")} · source ${esc(j.source)} ·
      match <b>${j.match_score ?? "?"}/100</b></div>
    <div class="tags"><span class="tag">${esc(j.job_type)}</span><span class="tag">${esc(j.category)}</span>
      ${j.remote ? '<span class="tag remote">Remote</span>' : ""}<span class="tag">${esc(j.salary || "no salary stated")}</span>
      <span class="tag">Visa: ${esc(j.visa_note)}</span></div>
    <h4>Why you match</h4><ul class="reasons">${(j.match_reasons || []).map((r) => `<li>✔ ${esc(r)}</li>`).join("") || "<li>—</li>"}</ul>
    <h4>Missing / gaps</h4><ul class="reasons">${(j.missing_skills || []).map((r) => `<li class="miss">✖ ${esc(r)}</li>`).join("") || "<li>None detected</li>"}</ul>
    <h4>Eligibility & work authorization</h4><ul class="reasons">${(j.eligibility_notes || []).map((r) => `<li class="elig">⚑ ${esc(r)}</li>`).join("") || "<li>No special flags detected — still verify before applying.</li>"}</ul>
    <h4>Description</h4><pre class="letter">${esc((j.description || "").slice(0, 5000))}</pre>
    <div class="actions"><a class="btn primary" target="_blank" rel="noopener noreferrer" href="${esc(j.url)}">Apply at original link ↗</a>
    <button class="btn" onclick="prepareJob(${j.id}, this)">✍ Prepare application</button></div>`;
  $("#modal").classList.remove("hidden");
};
$("#modal-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

window.prepareJob = async (id, btn) => {
  if (btn) btn.disabled = true;
  try {
    const a = await api("/api/applications/prepare", { method: "POST", json: { job_id: id } });
    toast("Application prepared — see Applications tab");
    if (btn) btn.textContent = "✓ Prepared";
    showPrepared(a);
  } catch (e) { toast(e.message, true); if (btn) btn.disabled = false; }
};

function showPrepared(a) {
  $("#modal-body").innerHTML = `<h3>Prepared application #${a.id}</h3>
    <p class="small muted">${esc(a.pause_reason)}</p>
    <h4>Cover letter</h4><pre class="letter">${esc(a.cover_letter)}</pre>
    <h4>Tailored CV guidance</h4><pre class="letter">${esc(a.tailored_cv_summary)}</pre>
    <h4>Suggested answers</h4><ul class="reasons">${a.answers.map((x) =>
      `<li><b>${esc(x.question)}</b><br>${esc(x.answer || x.answer_source || "")}</li>`).join("")}</ul>
    <div class="actions"><a class="btn primary" target="_blank" rel="noopener noreferrer"
      href="${esc(a.job ? a.job.url : "#")}" onclick="document.getElementById('modal').classList.add('hidden')">Open application page ↗</a>
      <button class="btn" onclick="markSubmitted(${a.id})">I submitted it (enter confirmation)</button></div>`;
  $("#modal").classList.remove("hidden");
}

window.markSubmitted = async (appId) => {
  const ref = prompt("Paste the confirmation reference / receipt text you received when submitting:\n(The system never marks an application as submitted without real confirmation.)");
  if (!ref) return;
  try { await api(`/api/applications/${appId}`, { method: "PATCH", json: { status: "submitted", confirmation_ref: ref } });
    toast("Marked submitted ✔ follow-up reminder set for +10 days");
    $("#modal").classList.add("hidden"); }
  catch (e) { toast(e.message, true); }
};

window.bookmark = async (id, btn) => { const r = await api(`/api/jobs/${id}/bookmark`, { method: "POST" });
  btn.textContent = r.bookmarked ? "★ Bookmarked" : "☆ Bookmark"; };
window.hideJob = async (id) => { await api(`/api/jobs/${id}/hide`, { method: "POST" });
  const el = $("#job-" + id); if (el) el.remove(); };

/* ---------------- applications ---------------- */
function appRow(a) {
  const j = a.job || {};
  return `<div class="row gap wrap"><span class="tag ${esc(a.status)}">${esc(a.status)}</span>
    <span><b>${esc(j.title || "job #" + a.job_id)}</b> — ${esc(j.company || "")}</span>
    <span class="small muted">${new Date(a.updated_at).toLocaleDateString()}</span>
    ${j.url ? `<a class="small" target="_blank" rel="noopener noreferrer" href="${esc(j.url)}">listing ↗</a>` : ""}
    <button class="btn small" onclick="viewApp(${a.id})">View</button>
    <select onchange="setAppStatus(${a.id}, this.value)">
      ${["prepared","awaiting_user","submitted","interview","rejected","expired","failed"].map((s) =>
        `<option ${s === a.status ? "selected" : ""}>${s}</option>`).join("")}</select></div>`;
}

let APPS = {};
async function loadApplications() {
  const d = await api(`/api/applications?status=${$("#apps-status").value}&q=${encodeURIComponent($("#apps-q").value.trim())}`);
  APPS = Object.fromEntries(d.items.map((a) => [a.id, a]));
  $("#apps-list").innerHTML = d.items.length ? d.items.map((a) => `<div class="card">${appRow(a)}
      ${a.pause_reason ? `<p class="small muted mt">${esc(a.pause_reason)}</p>` : ""}
      <p class="small">docs: ${a.submitted_docs.map(esc).join(", ") || "CV on file"} ${a.confirmation_ref ? "· conf: " + esc(a.confirmation_ref) : ""}</p>
    </div>`).join("")
    : `<div class="card empty">No applications tracked yet. The agent prepares them for high-match jobs when your mode is “Prepare” or “Auto-apply”, or use “Prepare application” on any job.</div>`;
}
$("#apps-status").addEventListener("change", loadApplications);
$("#apps-q").addEventListener("input", () => { clearTimeout(window._at); window._at = setTimeout(loadApplications, 400); });

window.viewApp = (id) => { const a = APPS[id]; if (a) showPrepared(a); };
window.setAppStatus = async (id, status) => {
  let ref = "";
  if (status === "submitted") {
    ref = prompt("Confirmation reference required — paste what the employer/platform showed you:");
    if (!ref) return loadApplications();
  }
  try { await api(`/api/applications/${id}`, { method: "PATCH", json: { status, confirmation_ref: ref } });
    toast("Status updated"); loadApplications(); }
  catch (e) { toast(e.message, true); loadApplications(); }
};

/* ---------------- profile & cv ---------------- */
async function loadProfile() {
  try {
    const p = await api("/api/cv/profile");
    fillProfileForm(p);
    $("#profile-unverified").classList.toggle("hidden", p.verified);
  } catch (e) {
    if (String(e.message).includes("No profile")) $("#form-profile").reset();
  }
  const hist = await api("/api/cv/history");
  $("#cv-history").innerHTML = hist.map((h) =>
    `<div>${h.current ? "●" : "○"} ${esc(h.filename)} (${h.type}, ${(h.size / 1024).toFixed(0)} KB, ${new Date(h.uploaded_at).toLocaleDateString()})</div>`).join("");
}

function fillProfileForm(p) {
  const f = $("#form-profile");
  f.full_name.value = p.full_name; f.email.value = p.email; f.phone.value = p.phone;
  f.location.value = p.location; f.headline.value = p.headline;
  f.summary.value = (p.summary || []).join("\n"); f.education.value = (p.education || []).join("\n");
  f.experience.value = (p.experience || []).join("\n"); f.skills.value = (p.skills || []).join(", ");
  f.certifications.value = (p.certifications || []).join("\n"); f.languages.value = (p.languages || []).join(", ");
  f.years_experience.value = p.years_experience;
}

$("#btn-upload-cv").addEventListener("click", async () => {
  const file = $("#cv-file").files[0];
  if (!file) return setMsg("#cv-msg", "Choose a PDF or DOCX first.", true);
  const fd = new FormData(); fd.append("file", file);
  setMsg("#cv-msg", "Uploading & parsing…");
  try {
    const d = await api("/api/cv/upload", { method: "POST", body: fd });
    setMsg("#cv-msg", d.message, false); fillProfileForm(d.profile);
    $("#profile-unverified").classList.remove("hidden"); loadProfile();
  } catch (e) { setMsg("#cv-msg", e.message, true); }
});

$("#btn-download-cv").addEventListener("click", async () => {
  try { const res = await fetch("/api/cv/download", { headers: { Authorization: "Bearer " + TOKEN } });
    if (!res.ok) throw new Error(await res.text() || "No CV on file");
    const blob = await res.blob(); const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = "my_cv"; a.click(); URL.revokeObjectURL(url);
  } catch (e) { toast(String(e.message || e), true); }
});

$("#btn-example-profile").addEventListener("click", async () => {
  if (!confirm("Load the editable example profile (BS Software Engineering / CS teacher / AI automation)? It overwrites the current profile fields — edit it afterwards with YOUR real facts.")) return;
  try { const d = await api("/api/cv/example-profile", { method: "POST" });
    fillProfileForm(d.profile); $("#profile-unverified").classList.remove("hidden"); setMsg("#cv-msg", d.message); }
  catch (e) { toast(e.message, true); }
});

$("#form-profile").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const lines = (v) => v.split("\n").map((x) => x.trim()).filter(Boolean);
  const commas = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
  try {
    const p = await api("/api/cv/profile", { method: "PUT", json: {
      full_name: f.full_name.value, email: f.email.value, phone: f.phone.value,
      location: f.location.value, headline: f.headline.value, summary: lines(f.summary.value),
      education: lines(f.education.value), experience: lines(f.experience.value),
      skills: commas(f.skills.value), certifications: lines(f.certifications.value),
      languages: commas(f.languages.value), years_experience: parseFloat(f.years_experience.value || 0),
      verified: true } });
    $("#profile-unverified").classList.toggle("hidden", p.verified);
    setMsg("#profile-saved", "Profile saved & confirmed ✔");
  } catch (e2) { setMsg("#profile-saved", e2.message, true); }
});

/* ---------------- preferences ---------------- */
const COUNTRIES = ["Pakistan", "United Kingdom", "Ireland", "Germany", "Netherlands", "France", "Spain",
  "Portugal", "Poland", "Italy", "Sweden", "Norway", "Denmark", "Finland", "Switzerland", "Belgium",
  "United States", "Canada", "Australia", "New Zealand", "United Arab Emirates", "Saudi Arabia", "Qatar",
  "Bahrain", "Oman", "Kuwait", "Türkiye", "Singapore", "Malaysia", "China", "Japan", "South Korea"];
const MODES = ["Remote", "Hybrid", "Onsite"];
const EMP_TYPES = ["Full-time", "Part-time", "Contract", "Internship", "Freelance"];

function chips(container, items, selected = []) {
  container.innerHTML = items.map((c) =>
    `<label class="check"><input type="checkbox" value="${esc(c)}" ${selected.includes(c) ? "checked" : ""}/> ${esc(c)}</label>`).join("");
}
const picked = (container) => $$("input:checked", container).map((i) => i.value);

async function loadPreferences() {
  SOURCES = await api("/api/sources");
  chips($("#country-checks"), COUNTRIES);
  chips($("#mode-checks"), MODES);
  chips($("#emp-checks"), EMP_TYPES);
  $("#source-checks").innerHTML = SOURCES.map((s) =>
    `<label class="check"><input type="checkbox" name="src" value="${s.key}" ${s.state === "active" ? "" : "title='not connected'"} />
      <b>${esc(s.label)}</b> <span class="integ-state ${s.state}">[${s.state.replace(/_/g, " ")}]</span>
      ${s.needs_credentials && !s.server_configured ? `<span class="muted">— <a target="_blank" rel="noopener" href="${esc(s.docs_url)}">how to connect</a></span>` : ""}</label>`).join("");
  const p = await api("/api/preferences");
  const f = $("#form-prefs");
  if (p) {
    f.job_titles.value = p.job_titles.join(", "); f.fields.value = p.fields.join(", ");
    $("#prefs-worldwide").checked = p.worldwide;
    chips($("#country-checks"), COUNTRIES, p.countries);
    chips($("#mode-checks"), MODES, p.work_modes);
    chips($("#emp-checks"), EMP_TYPES, p.employment_types);
    f.cities.value = p.cities.join(", "); f.min_salary.value = p.min_salary; f.currency.value = p.currency;
    f.min_experience_years.value = p.min_experience_years; f.qualifications.value = p.qualifications.join("\n");
    f.visa_sponsorship_required.checked = p.visa_sponsorship_required;
    f.open_to_relocation.checked = p.open_to_relocation; f.english_ok.checked = p.english_ok;
    f.search_frequency_hours.value = p.search_frequency_hours;
    f.max_auto_applications_per_day.value = p.max_auto_applications_per_day;
    $$(`input[name=auto_apply_mode]`).forEach((r) => r.checked = r.value === p.auto_apply_mode);
    f.auto_apply_authorized.checked = p.auto_apply_authorized;
    $$(`input[name=src]`).forEach((c) => c.checked = p.sources.includes(c.value));
  }
}

$("#btn-example-prefs").addEventListener("click", async () => {
  try { const d = await api("/api/preferences/example", { method: "POST" });
    toast(d.message); loadPreferences(); } catch (e) { toast(e.message, true); }
});

$("#form-prefs").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const list = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
  const enabledSources = $$(`#source-checks input:checked`).map((c) => c.value);
  try {
    await api("/api/preferences", { method: "PUT", json: {
      job_titles: list(f.job_titles.value), fields: list(f.fields.value),
      countries: picked($("#country-checks")), cities: list(f.cities.value),
      worldwide: $("#prefs-worldwide").checked, work_modes: picked($("#mode-checks")),
      employment_types: picked($("#emp-checks")), min_salary: parseFloat(f.min_salary.value || 0),
      currency: f.currency.value, qualifications: f.qualifications.value.split("\n").map(x=>x.trim()).filter(Boolean),
      min_experience_years: parseFloat(f.min_experience_years.value || 0),
      visa_sponsorship_required: f.visa_sponsorship_required.checked,
      open_to_relocation: f.open_to_relocation.checked, english_ok: f.english_ok.checked,
      sources: enabledSources, search_frequency_hours: parseInt(f.search_frequency_hours.value || 12),
      auto_apply_mode: ($(`input[name=auto_apply_mode]:checked`) || {}).value || "find_only",
      auto_apply_authorized: f.auto_apply_authorized.checked,
      max_auto_applications_per_day: parseInt(f.max_auto_applications_per_day.value || 5) } });
    setMsg("#prefs-msg", "Preferences saved ✔ Background searches will follow this schedule.", false);
  } catch (e2) { setMsg("#prefs-msg", e2.message, true); }
});

/* ---------------- integrations ---------------- */
async function loadIntegrations() {
  const d = await api("/api/settings/integrations");
  $("#integ-list").innerHTML = d.sources.map((s) => `<div class="card">
    <div class="row gap wrap"><h3 style="margin:0">${esc(s.label)}</h3>
      <span class="integ-state ${s.state}">● ${s.state.replace(/_/g, " ").toUpperCase()}</span>
      <span class="small muted" style="margin-left:auto">${s.user_authorized ? "you authorized " + new Date(s.authorized_at).toLocaleDateString() : "not authorized by you"}</span></div>
    <p class="small muted">${esc(s.description)}</p>
    <div class="actions">
      <a class="btn ghost" target="_blank" rel="noopener noreferrer" href="${esc(s.docs_url)}">Official docs ↗</a>
      <button class="btn" onclick="authorize('${s.key}', ${!s.user_authorized}, this)">
        ${s.user_authorized ? "Revoke my authorization" : "Authorize for auto-apply rules"}</button>
    </div></div>`).join("") +
    `<div class="card small muted">${esc(d.note)}<br>AI provider: <b>${d.ai_configured ? "configured" : "not configured — heuristic engine in use (fully functional, zero cost)"}</b></div>`;
}
window.authorize = async (key, val, btn) => {
  if (val && !confirm("Authorizing means you accept that submissions happen ONLY through officially permitted APIs/integrations, all CAPTCHAs and assessments pause for you, and no information is ever fabricated. Continue?")) return;
  try { await api("/api/settings/integrations/authorize", { method: "POST", json: { source_key: key, authorize: val } });
    toast(val ? "Authorization recorded" : "Authorization revoked"); loadIntegrations(); }
  catch (e) { toast(e.message, true); }
};

/* ---------------- settings ---------------- */
async function loadSettings() {
  const p = await api("/api/settings/privacy");
  $("#privacy-info").innerHTML = `<div>Consent accepted: <b>${p.consent_accepted ? "yes" : "no"}</b></div>
    <div>CV files: ${p.data_stored.cv_files} · jobs seen: ${p.data_stored.jobs_seen} ·
    applications: ${p.data_stored.applications} · search runs logged: ${p.data_stored.search_logs}</div>
    <div class="muted">${esc(p.storage)}</div>`;
}
$("#btn-export").addEventListener("click", async () => {
  const d = await api("/api/settings/export");
  const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "jobfinder-data-export.json"; a.click();
});
$("#form-pass").addEventListener("submit", async (e) => {
  e.preventDefault(); const f = e.target;
  try { await api("/api/settings/change-password", { method: "POST", json: {
    current_password: f.current_password.value, new_password: f.new_password.value } });
    setMsg("#pass-msg", "Password updated ✔"); f.reset(); }
  catch (err) { setMsg("#pass-msg", err.message, true); }
});
$("#form-delete").addEventListener("submit", async (e) => {
  e.preventDefault(); const f = e.target;
  if (!confirm("This permanently deletes your account, CV and ALL data. Are you sure?")) return;
  try { await api("/api/settings/account", { method: "DELETE", json: {
    password: f.password.value, confirm_text: f.confirm_text.value } });
    toast("All data deleted."); logout(); }
  catch (err) { setMsg("#del-msg", err.message, true); }
});

/* ---------------- boot ---------------- */
(async function boot() {
  try {
    const h = await api("/api/health");
    if (h.dev_secret_in_use) console.warn("Dev SECRET_KEY in use — set SECRET_KEY env for production.");
  } catch { /* offline */ }
  if (TOKEN) {
    try { const d = await api("/api/auth/me"); enterApp(TOKEN, d.user); return; }
    catch { logout(); }
  }
})();

const NAV = [
{ id: "live",       label: "Live Feed",     icon: "📷" },
{ id: "upload",     label: "Video Upload",  icon: "🎬" },
{ id: "dashboard",  label: "Dashboard",     icon: "📊" },
{ id: "violations", label: "Violations",    icon: "📸" },
{ id: "health",     label: "System Health", icon: "🖥️" },
];

let currentPage = "live";
let mountedPage = null;

// Populated once at boot from GET /api/solutions/manifest -- the single
// source of truth this whole app's branding/violation types/upload copy
// is driven from. No hardcoded per-solution config lives in JS anymore.
window.ACTIVE_MANIFEST = null;

// Built once at boot from every registered solution's manifest, not
// just the one this process booted as. Needed because ModelManager
// lets a camera slot run a different solution live -- an alert for
// e.g. "drowsy" arriving on a slot that was swapped to Driver
// Monitoring must still render correctly even on a process that
// booted as PPE. theme.js's vtype() checks this as a fallback.
window.ALL_VIOLATION_TYPES = {};

async function loadAllViolationTypes() {
  try {
    const res = await fetch("/api/solutions/available");
    const data = await res.json();
    const names = Array.isArray(data.available) ? data.available : [];

    const results = await Promise.all(
      names.map(name =>
        fetch(`/api/solutions/${name}/manifest`)
          .then(r => r.json())
          .catch(() => null)
      )
    );

    const merged = {};
    for (const result of results) {
      const types = result?.manifest?.violation_types || {};
      Object.assign(merged, types);
    }

    window.ALL_VIOLATION_TYPES = merged;
  } catch (error) {
    console.error("[App] Failed to load violation types:", error);
  }
}

function renderNav() {
  const nav = document.getElementById("nav-list");
  NAV.forEach(n => {
    const btn = document.createElement("button");
    btn.className = "nav-btn";
    btn.id = `nav-${n.id}`;
    btn.style.cssText = `
      padding:10px 14px;border-radius:9px;border:none;text-align:left;
      font-size:13px;font-family:var(--f-body);width:100%;display:flex;
      align-items:center;gap:10px;background:transparent;
    `;
    btn.innerHTML = `<span style="font-size:15px;">${n.icon}</span><span>${n.label}</span>`;
    btn.onclick = () => setPage(n.id);
    nav.appendChild(btn);
  });
}

function updateNavHighlight() {
  NAV.forEach(n => {
    const btn = document.getElementById(`nav-${n.id}`);
    btn.classList.toggle("nav-active", n.id === currentPage);
  });
}

function setPage(id) {
  currentPage = id;
  updateNavHighlight();

  const nav = NAV.find(n => n.id === id);
  if (!nav) { console.error(`Unknown page: ${id}`); return; }

  document.getElementById("page-title").innerText = `${nav.icon} ${nav.label}`;
  const content = document.getElementById("page-content");

  if (mountedPage && typeof mountedPage.destroy === "function") {
    mountedPage.destroy();
  }
  mountedPage = null;

  if (id === "live") {
    LivePage.mount(content);
    return;
  }

  LivePage.hide();

  content.innerHTML = `<div style="text-align:center;padding:80px;color:var(--text-muted);font-family:var(--f-mono);">Loading ${nav.label}...</div>`;

  if (id === "dashboard" && window.DashboardPage) { DashboardPage.render(content); mountedPage = DashboardPage; }
  else if (id === "violations" && window.ViolationsPage) { ViolationsPage.render(content); mountedPage = ViolationsPage; }
  else if (id === "health" && window.HealthPage) { HealthPage.render(content); mountedPage = HealthPage; }
  else if (id === "upload" && window.UploadPage) { UploadPage.render(content); mountedPage = UploadPage; }
}

function updateClock() {
  const now = new Date();
  const el = document.getElementById("sidebar-clock");
  if (el) el.innerHTML = `${window.location.host}<br>${now.toLocaleTimeString()}`;
  const dateEl = document.getElementById("page-date");
  if (dateEl) {
    dateEl.innerText = now.toLocaleDateString("en-IN", { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  }
}

function typeText(el, text, speed = 45) {
  let i = 0;
  const iv = setInterval(() => {
    if (i <= text.length) { el.innerText = text.slice(0, i); i++; }
    else clearInterval(iv);
  }, speed);
}

async function loadManifestAndApplyBranding() {
  try {
    const res = await fetch("/api/solutions/manifest");
    const data = await res.json();
    const m = data.manifest || {};
    window.ACTIVE_MANIFEST = m;

    const setText = (id, text) => { const el = document.getElementById(id); if (el) el.innerText = text; };
    const setHtml = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

    setText("entry-icon", m.icon || "🧩");
    setText("entry-tag-text", `Edge AI · Jetson Orin · ${m.name || "Platform"}`);
    setText("entry-title", m.name || "Edge AI Monitor");
    setText("sidebar-icon", m.icon || "🧩");
    setHtml("sidebar-brand-text", m.sidebar_brand_html || m.name || "Monitor");
    setText("header-model-badge", m.model_badge || "");
    document.title = m.doc_title || m.name || "Edge AI Monitor";

    const featuresEl = document.getElementById("entry-features");
    if (featuresEl && m.description) {
      featuresEl.innerHTML = `<div class="entry-badge" style="padding:6px 14px;border-radius:20px;border:1px solid var(--ivory-border);background:var(--surface-bg);color:var(--text-second);font-size:12px;box-shadow:0 2px 8px rgba(16,24,38,0.04);">${m.description}</div>`;
    }

    const footerEl = document.getElementById("entry-footer-tags");
    if (footerEl && m.upload && m.upload.init_tags) {
      footerEl.innerHTML = m.upload.init_tags
        .map(t => `<span style="font-size:10px;color:var(--text-muted);font-family:var(--f-mono);letter-spacing:1.2px;">${t}</span>`)
        .join("");
    }

    return `${m.name || "Edge AI Monitor"} — Real-Time Monitoring`;
  } catch (error) {
    console.error("[app] Failed to load manifest:", error);
    return "Edge AI Monitor";
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  renderNav();

  setInterval(updateClock, 1000);
  updateClock();

  const [typedText] = await Promise.all([
    loadManifestAndApplyBranding(),
    loadAllViolationTypes(),
  ]);

  setTimeout(() => {
    document.getElementById("entry-content").style.animation = "fadeUp 0.8s cubic-bezier(0.16,1,0.3,1) forwards";
    typeText(document.getElementById("typed-text"), typedText);
  }, 150);

  document.getElementById("launch-btn").onclick = () => {
    document.getElementById("entry-page").style.display = "none";
    document.getElementById("main-app").style.display = "flex";
    setPage("live");
  };
});

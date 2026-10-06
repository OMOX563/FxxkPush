/* FxxkPush GUI 逻辑：pywebview js_api ↔ 液态玻璃界面 */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const EXPECTED = ["ntfy_tunnel.py", "pc_subscriber.py", "notification_listener.py",
                  "ai_triager.py", "vision_listener.py", "watchdog.py"];

let api = null;
let lastStatus = null;
let jobPrev = {};
let formDirty = false;
let refreshing = false;

/* ---------------- 基础 ---------------- */
function toast(msg, type = "ok", ms = 4200) {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s";
    setTimeout(() => el.remove(), 320); }, ms);
}

async function ensureApi() {
  if (window.pywebview && window.pywebview.api) return window.pywebview.api;
  await new Promise((res) => {
    let done = false;
    const fin = () => { if (!done) { done = true; res(); } };
    window.addEventListener("pywebviewready", fin, { once: true });
    setTimeout(fin, 3000);
    const iv = setInterval(() => {
      if (window.pywebview && window.pywebview.api) { clearInterval(iv); fin(); }
    }, 200);
    setTimeout(() => { clearInterval(iv); fin(); }, 8000);
  });
  api = window.pywebview && window.pywebview.api;
  return api;
}

function activate(name) {
  $$(".tab").forEach((b) => {
    const on = b.dataset.pane === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  $$(".pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + name));
}

function setPill(sel, cls, text) {
  const el = $(sel);
  el.className = "pill" + (cls ? " " + cls : "");
  el.querySelector("span").textContent = text;
}

/* ---------------- 渲染 ---------------- */
function fillForm(form) {
  if (formDirty) return;
  $$("#cfgForm [data-k]").forEach((inp) => {
    const k = inp.dataset.k;
    if (k === "password" || k === "token" || k === "api_key") {
      inp.value = "";
      const has = k === "password" ? form.has_password
                : k === "token" ? form.has_token : form.has_api_key;
      inp.placeholder = has ? "已配置 · 点「显示」回读（留空保持）"
                            : (k === "token" ? "tk_xxx（VPS 脚本输出）"
                               : k === "api_key" ? "sk-…" : "原密码（留空保持）");
      return;
    }
    inp.value = form[k] == null ? "" : String(form[k]);
  });
  $("#tokenHint").textContent = form.has_token
    ? "已配置 token（留空 = 保持不变；点「显示」可回读核对）"
    : "尚未配置 token（VPS 脚本输出 tk_ 开头那串）";
}

function renderJobs(st) {
  const map = { start: "#jobStart", check: "#jobCheck",
                move: "#jobMove", uninstall: "#jobUn" };
  for (const [k, sel] of Object.entries(map)) {
    const el = $(sel); if (!el) continue;
    const j = st.jobs && st.jobs[k];
    if (!j || (j.state !== "running" && j.state !== "queued")) { continue; }
    el.textContent = (j.detail || j.state) + "…";
    el.classList.remove("err");
  }
  const edges = [];
  for (const k of Object.keys(map)) {
    const now = (st.jobs || {})[k];
    const prev = jobPrev[k];
    if (now && now.state === "done" &&
        (!prev || prev.state === "running" || prev.state === "queued")) {
      edges.push(k + "|" + (now.rc || 0) + "|" + (now.detail || ""));
    }
  }
  jobPrev = JSON.parse(JSON.stringify(st.jobs || {}));

  const busy = (k) => { const j = (st.jobs || {})[k];
    return j && (j.state === "running" || j.state === "queued"); };
  $("#btnStart").classList.toggle("busy", busy("start"));
  $("#btnStop").classList.toggle("busy", busy("stop"));
  $("#btnCheck").classList.toggle("busy", busy("check"));
  $("#btnPark").classList.toggle("busy", busy("move"));
  $("#btnUnpark").classList.toggle("busy", busy("move"));
  $("#btnUninstall").classList.toggle("busy", busy("uninstall"));
  $("#btnStart").disabled = busy("start") || busy("stop");
  $("#btnStop").disabled = busy("stop") || busy("start");
  return edges;
}

function render(st) {
  const prev = lastStatus;
  lastStatus = st;

  $("#chipVer").textContent = "v" + (st.version || "?");
  setPill("#pillSvc", st.running && st.proc_total > 0 ? "ok" : "bad",
          st.running ? `服务 ${st.proc_total} 进程` : "服务未运行");
  setPill("#pillHealth", st.health && st.health.code === 200 ? "ok" : "bad",
          st.health && st.health.code === 200 ? "隧道 200" : "隧道不通");
  const a = st.autostart || { count: 0, valid: 0, expected: 6 };
  setPill("#pillAuto",
          a.count >= a.expected && a.valid >= a.expected ? "ok"
            : (a.count > 0 ? "warn" : "bad"),
          a.count > 0 ? `自启 ${a.valid}/${a.expected}` : "自启未注册");

  $("#procCount").textContent =
    `${st.proc_total} 进程 · ${Object.keys(st.scripts || {}).length}/6 服务`;
  const wrap = $("#scripts");
  wrap.innerHTML = "";
  EXPECTED.forEach((s) => {
    const n = (st.scripts || {})[s] || 0;
    const tag = document.createElement("span");
    tag.className = "tag" + (n ? "" : " off");
    tag.textContent = s.replace(".py", "") + (n ? ` ×${n}` : " ✕");
    wrap.appendChild(tag);
  });

  $("#healthTxt").textContent = st.health ? st.health.text : "—";
  $("#autoTxt").textContent =
    a.count > 0 ? `${a.valid}/${a.expected} 项路径有效` : "未注册";
  $("#visionTxt").textContent =
    (st.vision_targets && st.vision_targets.length)
      ? st.vision_targets.join(" / ") : "（无）";
  $("#wdLine").textContent = st.watchdog_last
    ? st.watchdog_last.slice(0, 300) : "（暂无心跳）";
  $("#startLog").textContent =
    (st.start_log && st.start_log.length) ? st.start_log.join("\n") : "（暂无）";

  // 配置页
  fillForm(st.form || {});
  const vc = $("#visionChips");
  vc.innerHTML = "";
  (st.vision_targets || []).forEach((t) => {
    const s = document.createElement("span");
    s.className = "tag"; s.textContent = t; vc.appendChild(s);
  });

  // 自启开关
  const on = a.count >= a.expected;
  $("#swAuto").checked = on;
  $("#swAutoLabel").textContent = on
    ? "已开启（6 项，下次登录生效）" : "已关闭";

  const edges = renderJobs(st);
  edges.forEach((raw) => {
    const [k, rc, detail] = raw.split("|");
    const okk = rc === "0";
    const first = (detail || "").split("；")[0];
    if (k === "check") {
      toast(okk ? "自检通过" : "自检发现问题（见启动日志区）", okk ? "ok" : "err");
      if (!okk && detail) $("#startLog").textContent = detail;
    } else if (k === "move") {
      toast(first || "窗口归位完成", okk ? "ok" : "err");
    } else if (k === "uninstall") {
      toast("卸载" + (okk ? "完成" : "未完全成功") + "：" + first,
            okk ? "ok" : "err", 7000);
    } else if (k === "start") {
      toast(okk ? "启动完成：" + first : "启动失败：" + first,
            okk ? "ok" : "err", 6000);
    } else if (k === "stop") {
      toast(first || "已停止", okk ? "ok" : "err");
    }
  });
  if (!prev) window.__ready = true;   // 首次成功刷新（smoke 探针读它）
}

async function refresh() {
  if (!api || refreshing) return;
  refreshing = true;
  try {
    const st = await api.get_status();
    render(st);
  } catch (e) {
    setPill("#pillSvc", "warn", "后端无响应");
    console.error(e);
  } finally {
    refreshing = false;
  }
}

async function act(fn, busySel) {
  const btn = busySel ? $(busySel) : null;
  if (btn) btn.classList.add("busy");
  try {
    const r = await fn();
    if (r && r.ok === false) toast(r.msg || "操作失败", "err");
    else if (r && r.msg && !busySel) toast(r.msg, "ok");
    return r;
  } catch (e) {
    toast(String(e && e.message || e), "err");
    return { ok: false };
  } finally {
    if (btn) btn.classList.remove("busy");
    setTimeout(refresh, 400);
  }
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $$(".tab").forEach((b) => { b.onclick = () => activate(b.dataset.pane); });

  $("#btnStart").onclick = () =>
    act(() => api.start_services(), "#btnStart");
  $("#btnStop").onclick = () =>
    act(() => api.stop_services(), "#btnStop");
  $("#btnCheck").onclick = () => act(() => api.run_check(), "#btnCheck");
  $("#btnLogs").onclick = () => act(() => api.open_dir("logs"));
  $("#btnPark").onclick = () => act(() => api.move_windows("park"), "#btnPark");
  $("#btnUnpark").onclick = () => act(() => api.move_windows("unpark"), "#btnUnpark");

  // 自启开关（失败时按服务端状态回滚）
  $("#swAuto").onchange = async (e) => {
    const want = e.target.checked;
    const r = await act(() => api.toggle_autostart(want));
    if (r && r.ok === false) { await refresh(); }
    else if (r && r.msg) toast(r.msg, "ok");
  };

  // 表单
  $$("#cfgForm [data-k]").forEach((inp) => {
    inp.addEventListener("input", () => { formDirty = true; });
  });
  $$(".eye").forEach((b) => {
    b.onclick = async () => {
      const inp = b.parentElement.querySelector("input");
      // 空字段且后端有存值 → 按需回读（密钥默认不回传，防 DevTools/截屏直读；
      // 「留空=保持原值」的保存语义不变，程序赋值不会触发 input 事件）
      if (inp.type === "password" && !inp.value) {
        try {
          const r = await api.reveal_secret(inp.dataset.k);
          if (r && r.ok && r.value) inp.value = r.value;
          else if (r && r.ok) { toast("该字段当前未配置", "warn"); return; }
          else { toast((r && r.msg) || "回读失败", "err"); return; }
        } catch (e) { toast(String(e), "err"); return; }
      }
      const hide = inp.type === "password";
      inp.type = hide ? "text" : "password";
      b.textContent = hide ? "隐藏" : "显示";
    };
  });
  $("#cfgForm").onsubmit = async (e) => {
    e.preventDefault();
    const form = {};
    $$("#cfgForm [data-k]").forEach((inp) => { form[inp.dataset.k] = inp.value; });
    const btn = $("#btnSave");
    btn.classList.add("busy");
    let r;
    try { r = await api.save_config(form); }
    catch (ex) { r = { ok: false, errors: [String(ex)] }; }
    btn.classList.remove("busy");
    const ban = $("#cfgBanner");
    ban.hidden = false;
    if (!r.ok) {
      ban.className = "banner err";
      ban.innerHTML = "<strong>未保存：</strong><ul>" +
        (r.errors || []).map((x) => `<li>${escapeHtml(x)}</li>`).join("") + "</ul>";
      toast("配置校验未通过", "err");
      return;
    }
    formDirty = false;
    const warn = (r.warnings || []).length
      ? `<div style="margin-top:6px">⚠ ${escapeHtml(r.warnings.join("；"))}</div>` : "";
    if (r.changed && r.changed.length) {
      ban.className = "banner" + (r.warnings.length ? " warn" : "");
      ban.innerHTML = `<strong>已保存：</strong>${escapeHtml(r.changed.join("、"))}` +
        (r.need_restart ? `　<button class="btn primary" id="banRestart" style="padding:6px 14px">一键启动（重启生效）</button>` : "") + warn;
      toast(r.msg || "已保存", r.warnings.length ? "warn" : "ok");
      const rb = $("#banRestart");
      if (rb) rb.onclick = () => act(() => api.start_services(), "#banRestart");
    } else {
      ban.className = "banner";
      ban.innerHTML = "<strong>内容无变化，未写入。</strong>" + warn;
      toast("内容无变化", "ok");
    }
    setTimeout(refresh, 300);
  };
  $("#btnReset").onclick = () => {
    formDirty = false;
    if (lastStatus) fillForm(lastStatus.form || {});
    $("#cfgBanner").hidden = true;
    toast("表单已重置", "ok");
  };

  // 卸载弹窗
  const modal = $("#unModal");
  const syncOk = () => {
    $("#unOk").disabled = $("#unConfirm").value.trim() !== "uninstall";
  };
  $("#btnUninstall").onclick = () => {
    $("#unConfirm").value = ""; $("#unPurge").checked = false;
    syncOk(); modal.hidden = false; $("#unConfirm").focus();
  };
  $("#unCancel").onclick = () => { modal.hidden = true; };
  $("#unConfirm").oninput = syncOk;
  modal.onclick = (e) => { if (e.target === modal) modal.hidden = true; };
  $("#unOk").onclick = async () => {
    modal.hidden = true;
    const purge = $("#unPurge").checked;
    await act(() => api.run_uninstall(purge), "#btnUninstall");
  };
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 启动 ---------------- */
(async function main() {
  bind();
  await ensureApi();
  if (!api) { toast("未连接到后端（pywebview api）", "err", 20000); return; }
  await refresh();
  setInterval(refresh, 2500);
})();

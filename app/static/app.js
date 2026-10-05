// 字幕节奏与镜头边界校正台 —— 原生 JS 前端
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  project: null,
  delivery: null,   // {pid, result} 交付核验结果；项目刷新/变更即失效
};

// ---------- 工具 ----------
function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", isErr);
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

function fmtMs(ms) {
  if (ms == null) return "—";
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const x = ms % 1000;
  return [h, m, s].map(v => String(v).padStart(2, "0")).join(":") +
         "," + String(x).padStart(3, "0");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body instanceof FormData ? {} : {"Content-Type": "application/json"},
    ...opts,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

// ---------- 项目列表 ----------
async function loadProjects(selectId) {
  const rows = await api("/api/projects");
  const sel = $("#projectSelect");
  sel.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = ""; opt0.textContent = "— 选择历史项目 —";
  sel.appendChild(opt0);
  for (const r of rows) {
    const o = document.createElement("option");
    o.value = r.id;
    o.textContent = `#${r.id} ${r.name}（${r.cue_count} 条，采纳 ${r.adopted_count}）`;
    sel.appendChild(o);
  }
}

// ---------- 渲染 ----------
const DECISION_LABEL = { intentional: "有意留白", needs_subtitle: "待补字幕" };

// ---------- 交付字幕核验 ----------
const DCONCL_LABEL = {
  pass: "通过", time_mismatch: "时间不符", missing: "缺失",
  added: "新增", pending: "待确认",
};
const DCONCL_CLS = {
  pass: "dc-pass", time_mismatch: "dc-mismatch", missing: "dc-missing",
  added: "dc-added", pending: "dc-pending",
};

function dcPill(concl) {
  return `<span class="pill ${DCONCL_CLS[concl]}">${DCONCL_LABEL[concl]}</span>`;
}

function dcCandidates(cands, fmtOrd) {
  if (!cands.length) return "—";
  const shown = cands.map(c =>
    `<span class="dc-cand">${fmtOrd(c)} ${c.start_ms}→${c.end_ms}</span>`);
  return shown.join("");
}

function renderDelivery() {
  const stats = $("#deliveryStats");
  const box = $("#deliveryResult");
  const d = state.delivery;
  if (!state.project || !d || d.pid !== state.project.id) {
    stats.innerHTML = "";
    box.innerHTML = "";
    return;
  }
  const r = d.result;
  const s = r.summary;
  stats.innerHTML = [
    ["项目条目", s.project_total, ""],
    ["交付条目", s.delivery_total, ""],
    ["通过", s.pass, s.pass ? "good" : ""],
    ["时间不符", s.time_mismatch, s.time_mismatch ? "bad" : "good"],
    ["缺失", s.missing, s.missing ? "bad" : "good"],
    ["新增", s.added, s.added ? "warn" : "good"],
    ["待确认", s.pending, s.pending ? "warn" : "good"],
  ].map(([k, v, cls]) => `<span class="stat ${cls}">${k} <b>${v}</b></span>`).join("");

  const projRows = r.project_items.map(it => `
    <tr class="${it.conclusion === "pass" ? "" : "dc-row-attn"}">
      <td>${it.ord}</td>
      <td>${esc(it.cue_id)}</td>
      <td class="text-cell">${esc(it.text)}</td>
      <td>${it.start_ms} → ${it.end_ms}</td>
      <td>${dcPill(it.conclusion)}</td>
      <td class="dc-cands">${dcCandidates(it.candidates, c => `交付#${c.ord}`)}</td>
    </tr>`).join("");
  const delRows = r.delivery_items.map(it => `
    <tr class="${it.conclusion === "pass" ? "" : "dc-row-attn"}">
      <td>${it.ord}</td>
      <td class="text-cell">${esc(it.text)}</td>
      <td>${it.start_ms} → ${it.end_ms}</td>
      <td>${dcPill(it.conclusion)}</td>
      <td class="dc-cands">${dcCandidates(it.candidates, c => `项目#${c.ord}`)}</td>
    </tr>`).join("");
  box.innerHTML = `
    <h4>项目侧逐条结论（生效时间轴）</h4>
    <div class="table-wrap"><table class="dc-table">
      <thead><tr><th>#</th><th>cue_id</th><th>文案</th><th>生效起→止 (ms)</th>
        <th>结论</th><th>候选对应（交付行 起→止）</th></tr></thead>
      <tbody>${projRows}</tbody>
    </table></div>
    <h4>交付侧逐条结论（${esc(r.file_name)}）</h4>
    <div class="table-wrap"><table class="dc-table">
      <thead><tr><th>#</th><th>文案</th><th>起→止 (ms)</th>
        <th>结论</th><th>候选对应（项目行 起→止）</th></tr></thead>
      <tbody>${delRows}</tbody>
    </table></div>`;
}

function renderGapReview(p) {
  const panel = $("#gapReviewPanel");
  const gr = p.gap_review;
  if (!gr || !gr.shot_count) {           // 无镜头：不生成审阅项
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("#gapStats").innerHTML = [
    ["留白区间", gr.total, ""],
    ["待审", gr.pending_count, gr.pending_count ? "warn" : "good"],
    ["已审", gr.total - gr.pending_count, "good"],
  ].map(([k, v, cls]) => `<span class="stat ${cls}">${k} <b>${v}</b></span>`).join("");

  const list = $("#gapList");
  list.innerHTML = "";
  const byShot = new Map();              // 相接/重叠镜头分别列出
  for (const it of gr.items) {
    const arr = byShot.get(it.shot_ord) || [];
    arr.push(it);
    byShot.set(it.shot_ord, arr);
  }
  for (const [ord, items] of byShot) {
    const box = document.createElement("div");
    box.className = "shot-gaps";
    const head = document.createElement("div");
    head.className = "shot-head";
    head.textContent = `镜头 #${ord}　${fmtMs(items[0].shot_start_ms)} → ` +
      `${fmtMs(items[0].shot_end_ms)}（${items[0].shot_start_ms} → ${items[0].shot_end_ms} ms）`;
    box.appendChild(head);
    for (const it of items) {
      const row = document.createElement("div");
      row.className = "gap-item";
      const pill = it.decision
        ? `<span class="pill ${it.decision === "intentional" ? "intentional" : "needs-sub"}">${DECISION_LABEL[it.decision]}</span>`
        : `<span class="pill proposed">待审</span>`;
      const attrs = `data-shot="${it.shot_ord}" data-start="${it.gap_start_ms}" data-end="${it.gap_end_ms}"`;
      row.innerHTML = `
        <span class="gap-time">${fmtMs(it.gap_start_ms)} → ${fmtMs(it.gap_end_ms)}<span class="muted">${it.gap_start_ms} → ${it.gap_end_ms} ms · 时长 ${it.duration_ms} ms</span></span>
        ${pill}
        <span class="row-actions">
          <button class="mini" data-gap-act="intentional" ${attrs}>有意留白</button>
          <button class="mini" data-gap-act="needs_subtitle" ${attrs}>待补字幕</button>
          ${it.decision ? `<button class="mini" data-gap-act="clear" ${attrs}>清除</button>` : ""}
        </span>`;
      box.appendChild(row);
    }
    list.appendChild(box);
  }
}

function render() {
  const p = state.project;
  // 项目刷新/变更（导入、采纳、改参数、切换项目）都会使旧核验结果过期：
  // render 是这些路径的统一出口，而核验成功回调不经过 render，
  // 因此在这里清空并依据 state.delivery 重绘核验区即可。
  state.delivery = null;
  renderDelivery();
  if (!p) {
    $("#workspace").classList.add("hidden");
    $("#importPanel").classList.remove("hidden");
    return;
  }
  $("#importPanel").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $("#projectTitle").textContent = `#${p.id} ${p.name}`;
  const sf = $("#settingsForm");
  sf.cps_limit.value = p.cps_limit;
  sf.min_gap_ms.value = p.min_gap_ms;
  sf.tolerance_ms.value = p.tolerance_ms;

  const st = p.analysis.stats;
  $("#stats").innerHTML = [
    ["条目数", st.cue_count, ""],
    ["镜头数", st.shot_count, ""],
    ["间隔/重叠问题", st.gap_violations, st.gap_violations ? "bad" : "good"],
    ["CPS 超限", st.cps_violations, st.cps_violations ? "bad" : "good"],
    ["跨镜头条目", st.cross_shot_count, st.cross_shot_count ? "bad" : "good"],
    ["可提案条目", st.proposal_count, "good"],
    ["需移动条目", st.proposal_moved_count, "warn"],
    ["冲突条目", st.conflict_cue_count, st.conflict_cue_count ? "bad" : "good"],
    ["总移动量", st.total_shift_ms + " ms", ""],
  ].map(([k, v, cls]) => `<span class="stat ${cls}">${k} <b>${v}</b></span>`).join("");

  const conf = $("#conflicts");
  conf.innerHTML = "";
  for (const ch of p.analysis.chains) {
    const div = document.createElement("div");
    div.className = "chain";
    div.innerHTML = `<span class="tag">冲突链 ${ch.cue_ids.join(" → ")}</span>${esc(ch.reason)}`;
    conf.appendChild(div);
  }
  for (const it of p.analysis.intrinsic) {
    const div = document.createElement("div");
    div.className = "chain intrinsic";
    div.innerHTML = `<span class="tag">内在冲突 · ${esc(it.cue_id)}</span>${esc(it.reason)}`;
    conf.appendChild(div);
  }

  const tb = $("#cueTable tbody");
  tb.innerHTML = "";
  for (const c of p.analysis.cues) {
    const tr = document.createElement("tr");
    const flags = [];
    if (c.overlap_with) flags.push(`<span class="flag gap" title="${esc(c.overlap_with)}">间隔</span>`);
    if (!c.cps_ok) flags.push(`<span class="flag" title="CPS ${c.cps} 超限">CPS ${c.cps}</span>`);
    if (c.cross_shot) flags.push(`<span class="flag cross">跨镜头</span>`);

    const propTxt = c.proposal_start_ms == null ? "—（无解）"
      : `${c.proposal_start_ms} → ${c.proposal_end_ms}`;
    let deltaCls = "delta-zero", dTxt = "—";
    if (c.proposal_start_ms != null) {
      const d = c.proposal_start_ms - c.start_ms;
      dTxt = (d > 0 ? "+" : "") + d + " ms";
      deltaCls = d === 0 ? "delta-zero" : "delta-pos";
    }
    let status;
    if (c.adopted) {
      status = `<span class="pill adopted">已采纳</span>`;
    } else if (c.proposal_start_ms != null) {
      status = `<span class="pill proposed">待采纳</span>`;
    } else {
      status = `<span class="pill conflict">冲突</span>`;
    }
    const canAdopt = c.proposal_start_ms != null;
    const actionBtns = `
      <div class="row-actions">
        ${canAdopt ? `<button class="mini primary" data-act="adopt" data-uid="${c.uid}">采纳</button>` : ""}
        ${c.adopted ? `<button class="mini" data-act="revert" data-uid="${c.uid}">撤销</button>` : ""}
      </div>`;
    const startShown = c.adopted_start_ms ?? c.start_ms;
    const endShown = c.adopted_end_ms ?? c.end_ms;
    tr.className = c.proposal_start_ms == null ? "row-bad" : "";
    tr.innerHTML = `
      <td>${c.ord}</td>
      <td>${esc(c.cue_id)}</td>
      <td class="text-cell">${esc(c.text)}<div class="hash">sha256:${c.raw_hash.slice(0, 16)}…</div></td>
      <td>${startShown} → ${endShown}</td>
      <td>${c.duration_ms}</td>
      <td>${c.graphemes}</td>
      <td class="${c.cps_ok ? "cps-ok" : "cps-bad"}">${c.cps === Infinity ? "∞" : c.cps}</td>
      <td>${flags.join("") || "—"}</td>
      <td>${propTxt}</td>
      <td class="${deltaCls}">${dTxt}</td>
      <td>${status}${actionBtns}</td>`;
    tb.appendChild(tr);
  }

  const base = `/api/projects/${p.id}/export`;
  $("#exportJson").href = base + "?fmt=json";
  $("#exportZip").href = base + "?fmt=zip";
  $("#exportSrt").href = base + "?fmt=srt";
  $("#projectSelect").value = String(p.id);
  renderGapReview(p);
}

// 采纳可能因另一标签页并发改参数而被拒（旧提案失效）：重新拉取详情，
// 让表格与统计条回到服务端的最新一致状态。
async function refreshAfterConflict(pid) {
  try {
    state.project = await api(`/api/projects/${pid}`);
    render();
    loadProjects();
  } catch (_) {}
}

// ---------- 事件 ----------
$("#importForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const fd = new FormData();
  fd.append("name", f.name.value.trim() || "未命名项目");
  fd.append("cps_limit", f.cps_limit.value);
  fd.append("min_gap_ms", f.min_gap_ms.value);
  fd.append("tolerance_ms", f.tolerance_ms.value);
  fd.append("subtitle", f.subtitle.files[0]);
  if (f.shots_file.files[0]) fd.append("shots_file", f.shots_file.files[0]);
  try {
    state.project = await api("/api/projects", { method: "POST", body: fd });
    toast("导入并分析完成");
    await loadProjects();
    render();
  } catch (e) { toast(e.message, true); }
});

$("#settingsForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!state.project) return;
  const f = ev.target;
  try {
    state.project = await api(`/api/projects/${state.project.id}/settings`, {
      method: "PUT",
      body: JSON.stringify({
        cps_limit: parseFloat(f.cps_limit.value),
        min_gap_ms: parseInt(f.min_gap_ms.value, 10),
        tolerance_ms: parseInt(f.tolerance_ms.value, 10),
      }),
    });
    toast("已按新参数重新求解");
    render();
    loadProjects();
  } catch (e) { toast(e.message, true); }
});

$("#cueTable").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn || !state.project) return;
  const uid = parseInt(btn.dataset.uid, 10);
  const c = state.project.analysis.cues.find(x => x.uid === uid);
  const pid = state.project.id;
  try {
    if (btn.dataset.act === "adopt") {
      state.project = await api(`/api/projects/${pid}/adopt`, {
        method: "POST",
        body: JSON.stringify({
          cue_uid: uid,
          new_start_ms: c.proposal_start_ms,
          new_end_ms: c.proposal_end_ms,
        }),
      });
      toast(`已采纳 cue ${c.cue_id}`);
    } else {
      state.project = await api(`/api/projects/${pid}/adopt/${uid}`,
                                { method: "DELETE" });
      toast(`已撤销 cue ${c.cue_id}`);
    }
    render();
    loadProjects();
  } catch (e) {
    toast(e.message + "（参数可能已在别处更新，已刷新）", true);
    refreshAfterConflict(pid);
  }
});

$("#adoptAllBtn").addEventListener("click", async () => {
  if (!state.project) return;
  const todo = state.project.analysis.cues.filter(
    c => c.proposal_start_ms != null && !c.adopted);
  if (!todo.length) return toast("没有可采纳的提案");
  try {
    let p = state.project;
    for (const c of todo) {
      p = await api(`/api/projects/${p.id}/adopt`, {
        method: "POST",
        body: JSON.stringify({
          cue_uid: c.uid,
          new_start_ms: c.proposal_start_ms,
          new_end_ms: c.proposal_end_ms,
        }),
      });
    }
    state.project = p;
    toast(`已批量采纳 ${todo.length} 条`);
    render();
    loadProjects();
  } catch (e) {
    toast(e.message + "（参数可能已在别处更新，已刷新）", true);
    refreshAfterConflict(state.project.id);
  }
});

// 镜头留白审阅：标记 / 清除。过期页面（参数或采纳已在别处变化）提交的
// 结论会被服务端 400 拒绝，此时刷新到最新状态，避免误标新留白。
$("#gapList").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-gap-act]");
  if (!btn || !state.project) return;
  const pid = state.project.id;
  const shotOrd = parseInt(btn.dataset.shot, 10);
  const gapStart = parseInt(btn.dataset.start, 10);
  const gapEnd = parseInt(btn.dataset.end, 10);
  const act = btn.dataset.gapAct;
  try {
    if (act === "clear") {
      const q = `shot_ord=${shotOrd}&gap_start_ms=${gapStart}&gap_end_ms=${gapEnd}`;
      state.project = await api(`/api/projects/${pid}/gaps/review?${q}`,
                                { method: "DELETE" });
      toast("已清除审阅标记，回到待审");
    } else {
      state.project = await api(`/api/projects/${pid}/gaps/review`, {
        method: "POST",
        body: JSON.stringify({
          shot_ord: shotOrd, gap_start_ms: gapStart,
          gap_end_ms: gapEnd, decision: act,
        }),
      });
      toast(`已标记为「${DECISION_LABEL[act]}」`);
    }
    render();
  } catch (e) {
    toast(e.message + "（留白已随参数/采纳变化，已刷新）", true);
    refreshAfterConflict(pid);
  }
});

// 交付字幕核验：上传回传字幕，与当前生效时间轴对照。只读操作；
// 结果不随项目持久化，任何项目变更（render）后自动清空。
$("#deliveryForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!state.project) return;
  const f = ev.target;
  const file = f.delivery_file.files[0];
  if (!file) return toast("请选择回传的 SRT / WebVTT 文件", true);
  const fd = new FormData();
  fd.append("delivery_file", file);
  try {
    const result = await api(`/api/projects/${state.project.id}/delivery-check`,
                             { method: "POST", body: fd });
    state.delivery = { pid: state.project.id, result };
    const s = result.summary;
    toast(`核验完成：通过 ${s.pass}、时间不符 ${s.time_mismatch}、缺失 ${s.missing}、新增 ${s.added}、待确认 ${s.pending}`);
    renderDelivery();
  } catch (e) { toast(e.message, true); }
});

$("#deleteProjectBtn").addEventListener("click", async () => {  if (!state.project) return;
  if (!confirm(`确定删除项目「${state.project.name}」？该操作不可恢复。`)) return;
  try {
    await api(`/api/projects/${state.project.id}`, { method: "DELETE" });
    state.project = null;
    toast("项目已删除");
    await loadProjects();
    render();
  } catch (e) { toast(e.message, true); }
});

$("#projectSelect").addEventListener("change", async (ev) => {
  const id = ev.target.value;
  if (!id) return;
  try {
    state.project = await api(`/api/projects/${id}`);
    render();
  } catch (e) { toast(e.message, true); }
});

$("#newProjectBtn").addEventListener("click", () => {
  state.project = null;
  $("#projectSelect").value = "";
  render();
});

// 初始化
loadProjects().catch(() => {});

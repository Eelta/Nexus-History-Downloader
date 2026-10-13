/* Nexus Mods 下载历史仪表盘 —— 前端逻辑（无框架） */
"use strict";

const $ = (id) => document.getElementById(id);

/* 静态预览模式：?preview=1 或 window.__DEMO_DATA__ 存在时，不依赖后端，
   用内嵌演示数据渲染完整界面（可用于离线查看 UI / 无服务器环境验证）。 */
const PREVIEW_MODE =
  new URLSearchParams(location.search).has("preview") ||
  (typeof window.__DEMO_DATA__ !== "undefined" && window.__DEMO_DATA__ !== null);

const INLINE_DEMO = [
  {
    mod_id: 1001, game_domain: "skyrimspecialedition",
    url: "https://www.nexusmods.com/skyrimspecialedition/mods/1001",
    name: "SkyUI SE（静态预览示例）", author: "Sus620",
    summary: "Skyrim 界面增强示例模组（静态预览内置数据）。",
    category: "User Interface",
    updated_at: "2025-03-02T12:00:00+00:00",
    created_at: "2023-01-01T00:00:00+00:00",
    downloaded_at: "2025-02-28T10:00:00+00:00",
    files: {
      "Main files": [{ name: "SkyUI_5.2_SE.7z", version: "5.2", size: "11.5 MB",
        uploaded_at: "2025-02-01T10:00:00+00:00", uploaded_raw: "", file_id: "1000001" }],
      "Optional files": [
        { name: "SkyUI_English_fonts_optional.zip", version: "1.0", size: "2.1 MB",
          uploaded_at: "2025-01-10T08:00:00+00:00", uploaded_raw: "", file_id: "1000002" },
        { name: "SkyUI_Chinese_patch.7z", version: "2.1", size: "890 KB",
          uploaded_at: "2025-01-05T09:30:00+00:00", uploaded_raw: "", file_id: "1000003" },
      ],
      "Old files": [{ name: "SkyUI_5.1_SE.7z", version: "5.1", size: "11.4 MB",
        uploaded_at: "2024-12-01T14:00:00+00:00", uploaded_raw: "", file_id: "1000004" }],
      "Miscellaneous": [],
    },
    changelog: [
      { version: "5.2", date: "2025-02-01T10:00:00+00:00", label: "5.2",
        text: "修复物品栏排序卡顿；适配最新游戏版本。\n- 修复快速存取列表刷新\n- 更新脚本版本" },
      { version: "5.1", date: "2024-12-01T14:00:00+00:00", label: "5.1",
        text: "新增搜索高亮；修复若干本地化问题。" },
    ],
  },
  {
    mod_id: 1002, game_domain: "stardewvalley",
    url: "https://www.nexusmods.com/stardewvalley/mods/1002",
    name: "自动采集（静态预览示例）", author: "ForgeUser",
    summary: "自动收获作物与果树。", category: "Gameplay",
    updated_at: "2025-03-05T20:00:00+00:00",
    created_at: "2024-06-01T00:00:00+00:00",
    downloaded_at: "2025-03-01T09:00:00+00:00",
    files: {
      "Main files": [{ name: "AutoHarvest_1.4.2.dll", version: "1.4.2", size: "96 KB",
        uploaded_at: "2025-03-05T20:00:00+00:00", uploaded_raw: "", file_id: "1000021" }],
      "Optional files": [{ name: "AutoHarvest_Chinese.json", version: "1.4", size: "3 KB",
        uploaded_at: "2025-01-20T11:00:00+00:00", uploaded_raw: "", file_id: "1000022" }],
      "Old files": [], "Miscellaneous": [],
    },
    changelog: [{ version: "1.4.2", date: "2025-03-05T20:00:00+00:00", label: "1.4.2",
      text: "兼容 SMAPI 4.x；修复温室作物漏收。" }],
  },
  {
    mod_id: 1003, game_domain: "cyberpunk2077",
    url: "https://www.nexusmods.com/cyberpunk2077/mods/1003",
    name: "夜城光影（静态预览示例）", author: "NightCityLG",
    summary: "光线重构与体积雾调整。", category: "Visuals",
    updated_at: "2025-03-06T08:00:00+00:00",
    created_at: "2024-02-01T00:00:00+00:00",
    downloaded_at: "2025-03-03T15:00:00+00:00",
    files: {
      "Main files": [{ name: "NCLighting_v3.0.zip", version: "3.0", size: "320 MB",
        uploaded_at: "2025-03-06T08:00:00+00:00", uploaded_raw: "", file_id: "1000031" }],
      "Optional files": [{ name: "NCLighting_NoFog.ini", version: "3.0", size: "1 KB",
        uploaded_at: "2025-03-06T08:05:00+00:00", uploaded_raw: "", file_id: "1000032" }],
      "Old files": [], "Miscellaneous": [],
    },
    changelog: [{ version: "3.0", date: "2025-03-06T08:00:00+00:00", label: "3.0",
      text: "全新体积雾参数，帧数影响降低 15%。" }],
  },
  { mod_id: 1004, game_domain: "baldursgate3", name: "背包整理（静态预览示例）",
    url: "https://www.nexusmods.com/baldursgate3/mods/1004", author: "BagMaster",
    summary: "一键整理与分类背包物品。", category: "User Interface",
    updated_at: "2025-02-20T12:00:00+00:00", created_at: "2024-05-01T00:00:00+00:00",
    downloaded_at: "2025-02-18T10:00:00+00:00",
    files: {
      "Main files": [{ name: "BagSort_v1.9.1.pak", version: "1.9.1", size: "148 KB",
        uploaded_at: "2025-02-20T12:00:00+00:00", uploaded_raw: "", file_id: "1000041" }],
      "Optional files": [{ name: "BagSort_NoDurability.pak", version: "1.9", size: "146 KB",
        uploaded_at: "2025-02-10T09:00:00+00:00", uploaded_raw: "", file_id: "1000042" }],
      "Old files": [], "Miscellaneous": [],
    },
    changelog: [{ version: "1.9.1", date: "2025-02-20T12:00:00+00:00", label: "1.9.1",
      text: "修复与官方补丁 5 的兼容性。" }],
  },
  { mod_id: 1005, game_domain: "witcher3", name: "高清马匹（静态预览示例）",
    url: "https://www.nexusmods.com/witcher3/mods/1005", author: "StableHand",
    summary: "高清马匹模型与纹理。", category: "Models and Textures",
    updated_at: "2025-02-10T12:00:00+00:00", created_at: "2023-09-01T00:00:00+00:00",
    downloaded_at: "2025-02-05T10:00:00+00:00",
    files: {
      "Main files": [{ name: "HDHorses_v2.1.zip", version: "2.1", size: "450 MB",
        uploaded_at: "2025-02-10T12:00:00+00:00", uploaded_raw: "", file_id: "1000051" }],
      "Optional files": [], "Old files": [], "Miscellaneous": [],
    },
    changelog: [],
  },
];

function inlineDemoData() {
  if (typeof window.__DEMO_DATA__ !== "undefined" && window.__DEMO_DATA__) {
    return window.__DEMO_DATA__;
  }
  return INLINE_DEMO;
}

const state = {
  items: [],
  total: 0,
  query: "",
  game: "",
  jobRunning: false,
  pollTimer: null,
  discarded: new Set(),   // 用户手动“丢弃”的 mod_id
  showDiscarded: false,   // 「历史丢弃」开关
  dlMarks: {},            // mod_id -> 点击时间ISO：实时“已下载”标记（N网历史滞后时的即时状态）
};

async function api(path, opts) {
  if (PREVIEW_MODE) return inlineApi(path, opts);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let message = body.slice(0, 160);
    try { const data = JSON.parse(body); message = data.detail || data.message || message; } catch { /* 非 JSON 响应。 */ }
    const error = new Error(message || ('HTTP ' + res.status));
    error.status = res.status;
    throw error;
  }
  return res.json();
}

const previewSettings = {
  '/api/download/dir': { ok: true, dir: 'D:\\Downloads' },
  '/api/takeover/threads': { ok: true, maxThreads: 256 },
  '/api/appearance/settings': { ok: true, fontSize: 13 },
};

/* 静态预览模式的“假后端”：只实现前端用到的几个端点。 */
async function inlineApi(path, opts) {
  const demo = [...inlineDemoData()].sort(
    (a, b) => ((b.updated_at || b.created_at || "") < (a.updated_at || a.created_at || "") ? -1 : 1)
  );
  if (['/api/download/dir', '/api/takeover/threads', '/api/appearance/settings'].includes(path)) {
    const values = previewSettings[path];
    if (opts?.body) Object.assign(values, JSON.parse(opts.body));
    return { ...values };
  }
  if (path.startsWith("/api/mo2/")) return previewMo2Api(path, opts);
  if (path.startsWith("/api/wallpaper/")) return previewWallpaperApi(path, opts);
  if (path === "/api/status") {
    return {
      auth: false, demo: true, mods_count: demo.length,
      last_refresh: "静态预览（无后端）",
      job: { running: false, total: 0, done: 0, current: "", errors: [], started_at: null, finished_at: null },
      login: { state: "idle", message: "" }, max_history: 200,
      version: "preview",
    };
  }
  if (path.startsWith("/api/mods")) {
    const u = new URL(path, location.href);
    const q = (u.searchParams.get("query") || "").toLowerCase();
    const game = u.searchParams.get("game") || "";
    let rows = demo;
    if (q) rows = rows.filter((m) =>
      (m.name || "").toLowerCase().includes(q) ||
      (m.summary || "").toLowerCase().includes(q) ||
      (m.author || "").toLowerCase().includes(q));
    if (game) rows = rows.filter((m) => m.game_domain === game);
    return {
      total: rows.length,
      items: rows,
      games: [...new Set(demo.map((m) => m.game_domain))].sort(),
    };
  }
  if (path === "/api/progress") {
    return { running: false, total: 0, done: 0, current: "", errors: [] };
  }
  if (path === "/api/discarded") {
    return { ids: [] };
  }
  return { ok: true };
}

function fmtDate(iso) {
  if (!iso) return "未知";
  const d = new Date(iso);
  if (isNaN(d)) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(d);
}

function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function fmtCompact(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ------------------------------------------------------------------ status */
async function refreshStatus(showNotice = true) {
  let st;
  try {
    st = await api("/api/status");
  } catch (e) {
    setNotice(`无法连接后端: ${e.message}`);
    return;
  }

  const auth = $("authChip");
  if (st.auth) {
    auth.textContent = "已登录";
    auth.className = "chip chip-ok";
  } else {
    auth.textContent = "未登录";
    auth.className = "chip chip-warn";
  }
  $("btnProfile").textContent = st.auth ? "重新登录" : "登录";
  $("countChip").textContent = st.mods_count || 0;
  const fs = st.filter_stats;
  $("filterChip").textContent = fs && fs.game
    ? (fs.game === "skyrimspecialedition" ? "Skyrim SE" : fs.game)
    : "Skyrim SE";
  $("refreshChip").textContent = st.demo ? "预览" : (st.last_refresh ? String(st.last_refresh).slice(5, 16) : "尚未刷新");
  $("refreshChip").title = st.last_refresh || "";
  $("demoBadge").classList.toggle("hidden", !st.demo);

  state.jobRunning = st.job && st.job.running;
  state.refreshVersion = st.refresh_version || 0;
  const bar = $("progressBar");
  const inner = bar.querySelector(".progress-inner");
  if (state.jobRunning) {
    bar.classList.remove("hidden");
    const { done, total, stage, started_at } = st.job;
    if (total > 0) {
      // 有总量：实心进度 + 百分比
      inner.classList.remove("indet");
      inner.style.width = Math.round((done / total) * 100) + "%";
    } else {
      // 历史页扫描等未知总量阶段：滑动动画，不再是一根不动的横线
      inner.classList.add("indet");
      inner.style.width = "";
    }
    let msg =
      stage === "collecting_history"
        ? `扫描中${total > 0 ? ` · ${done}/${total}` : ""}${started_at ? ` · ${elapsedSec("job:" + started_at, started_at)}s` : ""}`
        : `处理中 · ${done}/${total}`;
    // 预计剩余时间（按已完成的速度外推）
    if (stage !== "collecting_history" && total > 0 && done > 0 && started_at) {
      const elapsed = Date.now() / 1000 - started_at;
      const perItem = elapsed / done;
      const etaSec = perItem * (total - done);
      if (etaSec > 30) {
        msg += ` · 剩余 ${Math.ceil(etaSec / 60)}m`;
      } else if (etaSec > 0) {
        msg += ` · 剩余 ${Math.ceil(etaSec)}s`;
      }
    }
    if (st.job.errors && st.job.errors.length) {
      msg += ` · ${st.job.errors.length} 个错误`;
    }
    $("progressText").textContent = msg;
    ensurePolling();
    hideNotice(); // 进度条本身就是状态，不再挂 409/进行中 提示
  } else {
    bar.classList.add("hidden");
    inner.style.width = "0%";
    stopPolling();
  }

  if (st.refresh_error) {
    setNotice(`刷新失败: ${st.refresh_error}`);
  } else if (showNotice && st.login &&
             (st.login.state === "waiting" || st.login.state === "importing")) {
    setNotice(st.login.message || "正在处理…");
  } else if (showNotice && st.login && st.login.state === "error") {
    setNotice(`登录流程出错: ${st.login.message}`);
  } else if (showNotice) {
    hideNotice();
  }
}

function ensurePolling() {
  if (state.pollTimer) return;
  state.lastRefreshVersion = null;   // 首次 tick 只记录，返回 true 表示应重载
  state.pollTimer = setInterval(async () => {
    await refreshStatus(false);
    const rv = state.refreshVersion;
    if (state.lastRefreshVersion !== null && rv !== state.lastRefreshVersion) {
      state.lastRefreshVersion = rv;
      // 后端刷新版本号变化 = 一次新扫描完成（比 running 翻转可靠）
      await loadMods(true);
      setNotice("已刷新");
      return;
    }
    state.lastRefreshVersion = rv;
  }, 2000);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

/* ---------- “已下载”实时标记（N网历史滞后几分钟时的本地即时标记） ----------
   点击模组标题即标记：卡片整卡变灰并显示“已下载”，不占序号。
   - 持久化在 localStorage：刷新页面/重新加载都不会丢；
   - N网下载历史更新后自动消除（该模组 downloaded_at 已晚于点击时间，
     说明官方记录已就绪）；
   - 下载失败等历史始终不变的情况可手动点击卡上的“已下载”取消。 */
const DL_MARK_KEY = "nexus_dl_marks_v1";
// N网历史时间只精确到分钟（会向下取整），比较时容忍 5 分钟
const DL_MARK_TOLERANCE_MS = 5 * 60 * 1000;

function loadDlMarks() {
  state.dlMarks = {};
  try {
    const raw = JSON.parse(localStorage.getItem(DL_MARK_KEY) || "{}");
    for (const [k, v] of Object.entries(raw)) {
      const id = Number(k);
      if (!Number.isFinite(id) || id <= 0) continue;
      const t = Date.parse(v);
      if (Number.isNaN(t)) continue;
      state.dlMarks[id] = v;
    }
  } catch (e) {
    state.dlMarks = {};
  }
}

function saveDlMarks() {
  try {
    localStorage.setItem(DL_MARK_KEY, JSON.stringify(state.dlMarks));
  } catch (e) { /* 隐私模式等写不了时忽略：标记仅本次会话有效 */ }
}

function isDlMarked(modId) {
  return Object.prototype.hasOwnProperty.call(state.dlMarks, Number(modId));
}

function markDownloaded(modId) {
  const id = Number(modId);
  if (!Number.isFinite(id) || id <= 0) return;
  if (!isDlMarked(id)) {
    state.dlMarks[id] = new Date().toISOString();
    saveDlMarks();
  }
  renderList();
}

function unmarkDownloaded(modId) {
  const id = Number(modId);
  if (!isDlMarked(id)) return;
  delete state.dlMarks[id];
  saveDlMarks();
  renderList();
}

/* N网历史更新后消除标记（渲染前调用；没加载到的模组保留标记） */
function pruneDlMarks(items) {
  let changed = false;
  for (const id of Object.keys(state.dlMarks)) {
    const mid = Number(id);
    const m = (items || []).find((x) => Number(x.mod_id) === mid);
    if (!m) continue;
    const dl = Date.parse(m.downloaded_at || "");
    const markedAt = Date.parse(state.dlMarks[mid]);
    if (!Number.isNaN(dl) && !Number.isNaN(markedAt) &&
        dl + DL_MARK_TOLERANCE_MS >= markedAt) {
      delete state.dlMarks[mid];
      changed = true;
    }
  }
  if (changed) saveDlMarks();
  return changed;
}

/* ---------------- “丢弃”标签（纯后处理，不改任何缓存数据） ---------------- */
async function loadDiscarded() {
  try {
    const d = await api("/api/discarded");
    state.discarded = new Set((d.ids || []).map(Number));
  } catch (e) {
    state.discarded = new Set();
  }
  renderList();   // 丢弃分组/序号在启动加载后立即生效
}

const pendingArchives = new Set();
async function toggleDiscard(modId, button) {
  if (pendingArchives.has(modId)) return;
  pendingArchives.add(modId);
  const card = button?.closest(".mod-card");
  if (button) button.disabled = true;
  card?.classList.add("is-pending");
  try {
    const d = await api("/api/discarded/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mod_id: Number(modId) }),
    });
    state.discarded = new Set((d.ids || []).map(Number));
  } catch (e) {
    setNotice(`归档失败: ${e.message}`);
    return;
  } finally {
    pendingArchives.delete(modId);
    if (button) button.disabled = false;
    card?.classList.remove("is-pending");
  }
  const restoreKeyboardFocus = document.activeElement === button;
  renderList();
  if (restoreKeyboardFocus) $("btnDiscardToggle").focus({ preventScroll: true });
}

function updateDiscardBtn() {
  const b = $("btnDiscardToggle");
  if (!b) return;
  b.textContent = `归档 · ${state.discarded.size}`;
  b.classList.toggle("btn-active", state.showDiscarded);
  b.setAttribute("aria-pressed", String(state.showDiscarded));
}

/* 导入真实浏览器会话后的状态轮询 */
function pollLoginImport() {
  let rounds = 0;
  const t = setInterval(async () => {
    rounds += 1;
    let st;
    try {
      st = await api("/api/status");
    } catch (e) {
      clearInterval(t);
      setNotice(`查询导入状态失败: ${e.message}`);
      return;
    }
    const s = st.login ? st.login.state : "idle";
    if (s === "done") {
      clearInterval(t);
      setNotice("会话已保存，开始抓取");
      await refreshStatus(true);
    } else if (s === "error") {
      clearInterval(t);
      setNotice(`操作失败: ${st.login.message}`);
    } else if (s === "waiting" || s === "importing") {
      setNotice(st.login.message || "处理中…");
    } else if (rounds > 30) {
      clearInterval(t);
      setNotice("等待超时，请查看服务日志");
    }
  }, 2000);
}

let noticeTimer = null;
let settingsNoticeTimer = null;
function setNotice(text) {
  const settingsNotice = $("settingsNotice");
  if ($("settingsDialog")?.open && settingsNotice) {
    settingsNotice.textContent = text;
    settingsNotice.classList.remove("hidden");
    if (settingsNoticeTimer) clearTimeout(settingsNoticeTimer);
    settingsNoticeTimer = setTimeout(() => {
      settingsNotice.classList.add("hidden");
      settingsNotice.textContent = "";
    }, 8000);
    return;
  }
  const n = $("notice");
  $("noticeText").textContent = text;
  n.classList.remove("hidden");
  $("noticeClose").classList.remove("hidden");
  if (noticeTimer) clearTimeout(noticeTimer);
  noticeTimer = setTimeout(hideNotice, 8000);   // 普通消息 8 秒后自动消失
}
function hideNotice() {
  if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null; }
  $("notice").classList.add("hidden");
  $("noticeClose").classList.add("hidden");
}

/* ------------------------------------------------------------------- mods */
async function loadMods() {
  let data;
  try {
    data = await api("/api/mods");
  } catch (e) {
    setNotice(`加载模组失败: ${e.message}`);
    return;
  }
  state.items = data.items || [];
  state.total = data.total || state.items.length;
  renderList();
  $("countChip").textContent = state.total;
}

/* 序号排序键：严格按更新时间（desc）；无更新时间时用创建时间兜底 */
function updTimeMs(m) {
  const t = Date.parse(m.updated_at || m.created_at || "");
  return Number.isNaN(t) ? 0 : t;
}
function byUpdatedDesc(a, b) {
  return updTimeMs(b) - updTimeMs(a);
}

function renderList() {
  pruneDlMarks(state.items);
  const list = $("modList");
  list.innerHTML = "";

  // 整个列表严格按更新时间排序（从历史丢弃恢复后也自动落回正确位置）
  const query = state.query.trim().toLocaleLowerCase();
  const order = [...state.items].sort(byUpdatedDesc).filter((mod) =>
    !query || `${mod.name || ""} ${mod.mod_id || ""}`.toLocaleLowerCase().includes(query));
  const droppedIds = new Set(
    order.filter((m) => state.discarded.has(m.mod_id))
        .map((m) => Number(m.mod_id))
  );

  // 序号只发给「活跃」模组：未丢弃 且 未标记已下载，其余不显示也不占号
  const seq = new Map();
  let n = 0;
  for (const m of order) {
    const id = Number(m.mod_id);
    if (droppedIds.has(id) || isDlMarked(id)) continue;
    n += 1;
    seq.set(id, n);
  }

  for (const m of order) {
    if (droppedIds.has(Number(m.mod_id))) continue;
    const c = buildCard(m, seq.get(Number(m.mod_id)) || null);
    if (isDlMarked(Number(m.mod_id))) c.classList.add("dl-marked");
    list.appendChild(c);
  }
  const dropped = order.filter((m) => droppedIds.has(Number(m.mod_id)));
  if (state.showDiscarded && dropped.length) {
    const h = document.createElement("div");
    h.className = "group-head";
    h.textContent = `已归档 · ${dropped.length}`;
    list.appendChild(h);
    for (const m of dropped) {
      const c = buildCard(m, null);   // 被丢弃的模组不显示序号
      c.classList.add("discarded");
      if (isDlMarked(Number(m.mod_id))) c.classList.add("dl-marked");
      list.appendChild(c);
    }
  }
  updateDiscardBtn();
  $("empty").classList.toggle("hidden", !!list.querySelector(".mod-card"));
  $("empty").querySelector("p").textContent = query ? "无匹配" : "暂无模组";
  $("btnClearSearch")?.classList.toggle("hidden", !state.query);
}

function buildCard(m, n) {
  const card = document.createElement("article");
  card.className = "mod-card";

  // 第一行：序号 + 模组名称 + 更新日期 + 我的下载日期
  const head = document.createElement("div");
  head.className = "mod-head";

  const left = document.createElement("div");
  left.className = "mod-left";
  if (n) {
    const seq = document.createElement("span");
    seq.className = "mod-seq";
    seq.textContent = n + ".";
    left.appendChild(seq);
  }

  const titleWrap = document.createElement("div");
  titleWrap.className = "mod-title";
  const link = document.createElement("a");
  link.href = m.url || "#";
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = m.name || `${m.game_domain}/${m.mod_id}`;
  link.title = link.textContent;
  // 点击模组名：立即标记“已下载”（实时，不等N网历史），并在接管窗口打开文件页
  link.addEventListener("click", (e) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    markDownloaded(m.mod_id);   // 先标记：进下载页即可在监测列表看到全灰+已下载
    if (PREVIEW_MODE) return;   // 静态预览：保持默认新标签行为，仅演示标记效果
    e.preventDefault();
    openTakeoverFor(m.url || link.href);
  });
  titleWrap.appendChild(link);
  left.appendChild(titleWrap);
  head.appendChild(left);

  const dates = document.createElement("div");
  dates.className = "mod-dates";
  const up = document.createElement("span");
  up.className = "updated-badge";
  up.textContent = "更新 " + fmtCompact(m.updated_at || m.created_at).split(" ")[0];
  up.title = "更新 " + fmtCompact(m.updated_at || m.created_at);
  dates.appendChild(up);
  const dl = document.createElement("span");
  dl.className = "chip dl-chip";
  dl.textContent = "下载 " + fmtCompact(m.downloaded_at).split(" ")[0];
  dl.title = "下载 " + fmtCompact(m.downloaded_at);
  dates.appendChild(dl);
  if (isDlMarked(m.mod_id)) {
    const done = document.createElement("button");
    done.type = "button";
    done.className = "chip dl-done-chip";
    done.textContent = "已下载";
    done.title = "实时标记：N网下载历史更新后自动消除；也可点击手动取消";
    done.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      unmarkDownloaded(m.mod_id);
    });
    dates.appendChild(done);
  }
  const db = document.createElement("button");
  db.type = "button";
  db.className = "discard-btn";
  db.textContent = state.discarded.has(m.mod_id) ? "恢复" : "归档";
  db.title = state.discarded.has(m.mod_id) ? "恢复模组" : "归档模组";
  db.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleDiscard(m.mod_id, db);
  });
  dates.appendChild(db);
  head.appendChild(dates);
  card.appendChild(head);
  return card;
}

function waitForJobStart(beforeRefresh = null, beforeVersion = null) {
  // 等待任务启动；任务可能在轮询间已完成（快扫），版本号变化即重载列表
  let rounds = 0;
  if (state.pollTimer) return;
  const t = setInterval(async () => {
    rounds += 1;
    let st;
    try {
      st = await api("/api/status");
    } catch (e) {
      clearInterval(t);
      setNotice(`状态查询失败: ${e.message}`);
      return;
    }
    if (st.refresh_error) {
      clearInterval(t);
      setNotice(`刷新失败: ${st.refresh_error}`);
      return;
    }
    if (beforeVersion != null && st.refresh_version != null &&
        st.refresh_version !== beforeVersion) {
      // 版本变化 = 新结果已就绪；提示统一由 ensurePolling 发出，这里只重载
      clearInterval(t);
      await loadMods(true);
      return;
    }
    if (st.job && st.job.running) {
      // 任务已启动：刷新状态以渲染进度条/唤起进度轮询，但不退出——继续等版本号变化
      await refreshStatus(true);
      return;
    }
    if (rounds > 40) {
      clearInterval(t);
      setNotice("任务启动超时，请查看服务日志");
    }
  }, 1500);
}

async function startRefresh(force = false) {
  // 若任务刚启动（10 秒内），只提示，不打断；超过则视为“重新开始抓取”
  let beforeRefresh = null;
  let beforeVersion = null;
  try {
    const st = await api("/api/status");
    beforeRefresh = st.last_refresh || null;
    beforeVersion = st.refresh_version ?? null;
    if (st.job && st.job.running) {
      // 扫描中一律不重复触发（宿主端也复用不会取消）
      setNotice("扫描进行中，完成后列表自动更新");
      waitForJobStart(beforeRefresh, beforeVersion);
      return;
    }
  } catch (e) {
    setNotice(`查询状态失败: ${e.message}`);
    return;
  }
  $("btnRefresh").disabled = true;
  try {
    const r = await api(`/api/refresh${force ? "?force=true" : ""}`, { method: "POST" });
    if (r.reused) {
      setNotice("扫描已在进行，无需重复刷新；完成后列表自动更新");
      return;
    }
    setNotice("刷新任务已启动");
    // 持续等待任务启动（旧任务收尾最长 15 秒）；任务可能在两次轮询间完成，
    // waitForJobStart 会对比 last_refresh，完成即自动刷新列表
    waitForJobStart(beforeRefresh, beforeVersion);
  } catch (e) {
    setNotice(`启动刷新失败: ${e.message}`);
  } finally {
    $("btnRefresh").disabled = false;
  }
}

/* ------------------------------------------------------------ 下载接管 */
/* 点击模组名：自动打开/激活接管窗口并跳到该模组的文件页（无独立按钮 UI） */
let lastAnnouncedJob = null;   // 已完成/失败提示去重（按任务 id）

async function openTakeoverFor(url) {
  setNotice("正在打开接管窗口…");
  try {
    const r = await api("/api/takeover/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    setNotice(r.message || (r.ok ? "已在接管窗口打开" : "打开失败"));
    if (r.ok) startDlWatcher();   // 打开窗口后确保进度/完成提示监视器在跑（单一通知源）
  } catch (e) {
    setNotice(`打开接管窗口失败: ${e.message}`);
  }
}

/* 无意义文件名（签名 URL 的 UUID 令牌）不参与提示，避免误导 */
function isJunkJob(j) {
  const f = j.filename || String(j.savePath || "").split(/[\\/]/).pop();
  const base = String(f || "").split(".")[0];
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(base);
}

/* 完成/失败提示统一由 dlTick（1.5s 监视器）发出，dlSeen 按任务去重，
   避免多处轮询重复弹通知 */

/* 下载完成/失败：右下角多条 toast 通知，可手动关闭，12 秒自动消失 */
function addToast(title, msg, isError, pathForOpen) {
  const box = document.createElement("div");
  box.className = "toast" + (isError ? " toast-error" : "");
  const body = document.createElement("div");
  body.className = "toast-body";
  const b = document.createElement("b");
  b.textContent = title;
  body.appendChild(b);
  if (msg) {
    const s = document.createElement("div");
    s.className = "toast-msg";
    s.textContent = msg;
    body.appendChild(s);
  }
  const x = document.createElement("button");
  x.className = "toast-x";
  x.type = "button";
  x.textContent = "×";
  x.title = "关闭";
  x.addEventListener("click", () => box.remove());
  body.appendChild(x);
  box.appendChild(body);
  if (pathForOpen) {
    const actions = document.createElement("div");
    actions.className = "toast-actions";
    const open = document.createElement("button");
    open.className = "btn";
    open.type = "button";
    open.textContent = "打开文件夹";
    open.addEventListener("click", () => {
      api("/api/download/openfolder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: pathForOpen }),
      })
        .then((r) => { if (!r.ok) setNotice(r.message || "仅 Windows 支持打开文件夹"); })
        .catch((e) => setNotice(`打开文件夹失败: ${e.message}`));
    });
    actions.appendChild(open);
    box.appendChild(actions);
  }
  $("toasts").appendChild(box);
  setTimeout(() => box.remove(), 12000);
}

function showDoneNotice(path, job) {
  const lines = [path || "(未知位置)"];
  if (job) {
    const parts = [];
    if (job.maxConcurrent) parts.push(`${job.maxConcurrent} 并发`);
    if (job.elapsedSeconds != null) parts.push(`${Number(job.elapsedSeconds).toFixed(1)}s`);
    if (job.avgSpeedBps) parts.push(`平均 ${fmtSpeed(job.avgSpeedBps)}`);
    if (job.peakSpeedBps) parts.push(`峰值 ${fmtSpeed(job.peakSpeedBps)}`);
    if (parts.length) lines.push("速度对比: " + parts.join(" · "));
  }
  addToast("下载完成", lines.join("\n"), false, path);
}

/* 引擎下载进度监视器：实时进度条 + 完成/失败提示（去重） */
let dlTimer = null;
let dlSeen = {};   // jobId -> 已提示过的状态

function fmtBytes(n) {
  n = Number(n || 0);
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + " GB";
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}
function fmtSpeed(n) { return fmtBytes(n) + "/s"; }

/* 任务耗时：从页面第一次观察到该任务起算（key 相同且 startedAt 未变则复用基准），
   避免把后端预热时间也算进来 */
const jobSeen = {};   // key -> { started, seen }
function elapsedSec(jobKey, startedAt) {
  if (!jobKey) return 0;
  const rec = jobSeen[jobKey];
  if (!rec || rec.started !== startedAt) {
    jobSeen[jobKey] = { started: startedAt, seen: Date.now() };
  }
  return Math.max(1, Math.round((Date.now() - jobSeen[jobKey].seen) / 1000));
}
function pruneJobSeen() {
  for (const k in jobSeen) {
    if (Date.now() - jobSeen[k].seen > 600000) delete jobSeen[k];
  }
}

function startDlWatcher() {
  if (dlTimer || PREVIEW_MODE) return;
  dlTimer = setInterval(dlTick, 1500);
}

async function dlTick() {
  pruneJobSeen();
  let st;
  try {
    st = await api("/api/takeover/status");
  } catch { return; }
  const jobs = (st.jobs || []).filter((j) => !isJunkJob(j));
  renderDlList(jobs);

  const recent = (j) => {
    const t = Date.parse((j.startedAt || "").replace(" ", "T"));
    return t && Date.now() - t < 300000;
  };
  const done = jobs.find((j) => j.state === "done" && recent(j) && dlSeen[j.id] !== "done");
  if (done) {
    dlSeen[done.id] = "done";
    lastAnnouncedJob = done.id;
    showDoneNotice(done.savePath || "(未知位置)", done);
    return;
  }
  const bad = jobs.find((j) => j.state === "error" && recent(j) && j.id !== lastAnnouncedJob);
  if (bad) {
    dlSeen[bad.id] = "error";
    lastAnnouncedJob = bad.id;
    addToast("下载失败", bad.error || "未知错误（详见服务日志）", true, null);
  }
}

/* 多任务下载进度：排队中/下载中各自一行 */
function renderDlList(jobs) {
  const list = $("dlList");
  const rows = jobs.filter((j) => j.state === "queued" || j.state === "downloading");
  if (!rows.length) {
    $("dlProgress").classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  $("dlProgress").classList.remove("hidden");
  list.innerHTML = "";
  for (const job of rows) list.appendChild(buildDlRow(job));
}

function buildDlRow(job) {
  const row = document.createElement("div");
  row.className = "dl-row";
  const head = document.createElement("div");
  head.className = "dl-row-head";
  const text = document.createElement("div");
  text.className = "dl-row-text";
  const track = document.createElement("div");
  track.className = "dlprogress-track";
  const bar = document.createElement("div");
  bar.className = "dlprogress-bar";

  const name = job.filename || "下载中";
  let elapsed = "";
  if (job.id) elapsed = ` · ${elapsedSec(job.id, job.startedAt)}s`;

  if (job.state === "queued") {
    text.textContent = `${name} · 排队中${elapsed}`;
  } else {
    const done = Number(job.downloaded || 0);
    const total = Number(job.total || 0);
    const speed = fmtSpeed(job.speed);
    if (total > 0) {
      const pct = Math.min(100, Math.round((done / total) * 100));
      bar.style.width = pct + "%";
      text.textContent =
        `${name} · ${pct}% · ${fmtBytes(done)}/${fmtBytes(total)} · ${speed} · ${job.activeThreads || 0} 连接${elapsed}`;
    } else {
      bar.classList.add("indet");
      text.textContent =
        `${name} · ${fmtBytes(done)} · ${speed} · ${job.activeThreads || 0} 连接${elapsed}`;
    }
  }
  // 取消按钮：排队中/下载中的任务都可实时取消
  const cancel = document.createElement("button");
  cancel.className = "btn dl-cancel";
  cancel.type = "button";
  cancel.textContent = "取消";
  cancel.addEventListener("click", () => cancelJob(job.id, name));
  head.appendChild(text);
  head.appendChild(cancel);
  track.appendChild(bar);
  row.appendChild(head);
  row.appendChild(track);
  return row;
}

/* 实时取消任务 */
async function cancelJob(jobId, name) {
  try {
    const r = await api("/api/takeover/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jobId }),
    });
    setNotice(r.ok ? `已取消：${name}（临时文件已清理）` : (r.message || "取消失败"));
  } catch (e) {
    setNotice(`取消失败: ${e.message}`);
  }
}

/* ------------------------------------------------ 下载目录（默认 + 本次） */
async function loadDir() {
  try {
    const r = await api("/api/download/dir");
    if (!settingEdited("dlDirInput")) $("dlDirInput").value = r.dir || "";
  } catch (e) { /* 后端未就绪时忽略 */ }
}

/* 原生目录选择（Windows 资源管理器），把选中路径填进输入框 */
async function browseDir(inputId) {
  const input = $(inputId);
  input.disabled = true;
  try {
    const r = await api("/api/download/choose-dir", { method: "POST" });
    if (r.ok && r.dir) {
      input.value = r.dir;
      hideNotice();
    } else if (r.ok) {
      hideNotice();   // 用户取消
    } else {
      setNotice(r.message || "选择目录失败");
    }
  } catch (e) {
    setNotice(`选择目录失败: ${e.message}`);
  } finally {
    input.disabled = false;
  }
}

/* --------------------------------------------------------- MO2 安装确认 */
const mo2State = {
  timer: null,
  polling: false,
  settingsLoaded: false,
  settingsDirty: false,
  drafts: new Map(),
  rows: new Map(),
  busy: new Set(),
  jobs: [],
  jobsLoaded: false,
  bridge: { state: "unavailable", message: "" },
};

const mo2Preview = {
  ok: true,
  settings: { enabled: true, mo2Path: "F:\\Skyrim\\iniRePather-Skyrim", deleteAfterInstall: false },
  bridge: { state: "ready", message: "静态预览：实际安装时，请在 MO2 窗口中完成选项。" },
  jobs: [{
    id: "preview-mo2", filename: "SkyUI_5.2_SE.7z", archivePath: "D:\\Downloads\\SkyUI_5.2_SE.7z",
    state: "awaiting_confirmation", suggestedName: "SkyUI - 中文界面", installName: "",
    matchReason: "Nexus 模组编号与已安装模组一致；已安装文件中包含 SkyUI_SE.esp。",
    candidates: [{ name: "SkyUI - 中文界面", reason: "相同 Nexus 模组编号，且包含相同插件文件" },
      { name: "SkyUI", reason: "模组名称相近" }],
  }],
};

function previewMo2Api(path, opts) {
  const data = opts && opts.body ? JSON.parse(opts.body) : {};
  if (path === "/api/mo2/settings") Object.assign(mo2Preview.settings, data);
  const action = path.match(/^\/api\/mo2\/jobs\/([^/]+)\/(confirm|retry|skip)$/);
  if (action) {
    const job = mo2Preview.jobs.find((item) => item.id === decodeURIComponent(action[1]));
    if (job) {
      job.state = { confirm: "queued", retry: "awaiting_confirmation", skip: "cancelled" }[action[2]];
      if (action[2] === "confirm") job.installName = data.installName;
      job.message = "静态预览，不会安装或删除实际文件。";
    }
  }
  return JSON.parse(JSON.stringify(mo2Preview));
}

function mo2Element(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value != null) element.textContent = value;
  return element;
}

function mo2BridgeConnected() {
  return mo2State.bridge.state === "ready" || mo2State.bridge.state === "installing";
}

function showMo2Bridge(bridge) {
  mo2State.bridge = { state: "unavailable", message: "", ...bridge };
  const labels = { ready: "已连接", installing: "安装中", unavailable: "未连接", needs_restart: "未连接", not_configured: "待设置" };
  const connected = mo2BridgeConnected();
  $("mo2Chip").textContent = labels[mo2State.bridge.state] || "未连接";
  $("mo2Chip").title = mo2State.bridge.message || "";
  $("mo2Chip").className = "chip " + (connected ? "chip-ok" : "chip-warn");
  const connection = $("mo2Connection");
  connection.textContent = connected ? "" : mo2State.bridge.state === "not_configured" ? "在设置中选择 MO2" : "打开或重启 MO2";
  connection.title = mo2State.bridge.message || "";
  connection.classList.toggle("hidden", connected);
  connection.classList.toggle("is-ready", connected);
}

function showMo2Status(result, applySettings = false) {
  if (result.ok === false) throw new Error(result.message || "读取安装状态失败");
  if (result.settings && (applySettings || !mo2State.settingsLoaded)) {
    if (!settingEdited("mo2Path")) $("mo2Path").value = result.settings.mo2Path || "F:\\Skyrim\\iniRePather-Skyrim";
    if (!settingEdited("mo2Enabled")) $("mo2Enabled").checked = result.settings.enabled !== false;
    if (!settingEdited("mo2DeleteArchive")) $("mo2DeleteArchive").checked = result.settings.deleteAfterInstall === true;
    mo2State.settingsLoaded = true;
  }
  if (result.bridge) showMo2Bridge(result.bridge);
  if (Array.isArray(result.jobs)) mo2State.jobs = result.jobs;
  renderMo2Jobs();
}

async function refreshMo2Status() {
  if (mo2State.polling) return;
  mo2State.polling = true;
  try {
    showMo2Status(await api("/api/mo2/status"));
  } catch (error) {
    showMo2Bridge({ state: "unavailable", message: error.message });
    $("mo2Chip").textContent = "连接中断";
    $("mo2Connection").textContent = "等待连接恢复";
    renderMo2Jobs();
  } finally {
    mo2State.polling = false;
  }
}

function startMo2Watcher() {
  refreshMo2Status();
  if (!mo2State.timer && !PREVIEW_MODE) mo2State.timer = setInterval(refreshMo2Status, 2000);
}


function renderMo2Jobs() {
  const jobs = mo2State.jobs;
  const ids = new Set(jobs.map((job) => String(job.id)));
  for (const [id, record] of mo2State.rows) {
    if (!ids.has(id)) { record.row.remove(); mo2State.rows.delete(id); mo2State.drafts.delete(id); }
  }
  let active = 0;
  let history = 0;
  let awaiting = 0;
  let installed = 0;
  let skipped = 0;
  let revealHistory = false;
  for (const job of jobs) {
    const id = String(job.id);
    const completed = job.state === "installed" || job.state === "cancelled";
    const parent = completed ? $("mo2HistoryJobs") : $("mo2Jobs");
    if (completed) history++; else active++;
    if (job.state === "awaiting_confirmation") awaiting++;
    if (job.state === "installed") installed++;
    if (job.state === "cancelled") skipped++;
    // 不在轮询中重建未改变的卡片，避免打断输入、候选下拉框和键盘焦点。
    const connectionState = job.state === "queued" || job.state === "installing" ? mo2State.bridge.state : "";
    const signature = JSON.stringify([job, connectionState]);
    const record = mo2State.rows.get(id);
    if (record && (record.signature === signature || mo2State.busy.has(id))) continue;
    if (job.state === "installed" && ((record ? record.state !== "installed" : mo2State.jobsLoaded) || (job.cleanupError && !record?.cleanupError))) revealHistory = true;
    const oldInput = record && record.row.querySelector(".mo2-name-input");
    const selection = oldInput && document.activeElement === oldInput
      ? [oldInput.selectionStart, oldInput.selectionEnd, oldInput.selectionDirection] : null;
    const detailsOpen = record?.row.querySelector(".mo2-job-details")?.open;
    const row = buildMo2Job(job);
    if (detailsOpen && row.querySelector(".mo2-job-details")) row.querySelector(".mo2-job-details").open = true;
    if (record && record.row.parentElement === parent) record.row.replaceWith(row);
    else { if (record) record.row.remove(); parent.appendChild(row); }
    mo2State.rows.set(id, { row, signature, state: job.state, cleanupError: job.cleanupError });
    const newInput = selection && row.querySelector(".mo2-name-input");
    if (newInput) {
      newInput.focus({ preventScroll: true });
      newInput.setSelectionRange(...selection);
    }
  }
  $("mo2Empty").classList.toggle("hidden", active > 0);
  $("mo2History").classList.toggle("hidden", !history);
  $("mo2HistorySummary").textContent = `已安装 ${installed} · 已跳过 ${skipped}`;
  if (revealHistory) $("mo2History").open = true;
  $("mo2JobCount").textContent = awaiting ? `${awaiting} 待确认` : (active ? String(active) : "");
  mo2State.jobsLoaded = true;
}

function buildMo2Job(job) {
  const id = String(job.id);
  const waiting = job.state === "awaiting_confirmation";
  const displayName = job.state === "installed" ? (job.installedName || job.installName) : job.installName;
  const labels = { awaiting_confirmation: "待确认", queued: "未安装", installing: "安装中", installed: "已安装", cancelled: "已跳过", error: "失败" };
  const row = mo2Element("article", "mo2-job" + (waiting ? " is-waiting" : "") + (job.state === "error" ? " is-error" : ""));
  const heading = mo2Element("div", "mo2-job-heading");
  heading.append(mo2Element("div", "mo2-job-title", job.filename || job.installName || "模组安装包"),
    mo2Element("span", "chip " + (job.state === "error" ? "mo2-chip-error" : waiting ? "chip-warn" : ""), labels[job.state] || job.state));
  row.appendChild(heading);
  if (job.archivePath || job.matchReason) {
    const details = mo2Element("details", "mo2-job-details");
    details.appendChild(mo2Element("summary", "", "详情"));
    if (job.matchReason) details.appendChild(mo2Element("p", "mo2-job-reason", job.matchReason));
    if (job.archivePath) details.appendChild(mo2Element("p", "mo2-job-path", job.archivePath));
    row.appendChild(details);
  }
  const error = mo2Element("div", "mo2-job-error hidden");
  error.setAttribute("role", "alert");
  if (waiting) {
    if (!mo2State.drafts.has(id)) mo2State.drafts.set(id, job.installName || job.suggestedName || "");
    const form = mo2Element("form", "mo2-confirm-form");
    const label = mo2Element("label", "mo2-name-label");
    label.appendChild(mo2Element("span", "", "安装名称"));
    const input = mo2Element("input", "mo2-name-input");
    input.type = "text";
    input.required = true;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = mo2State.drafts.get(id);
    input.placeholder = "模组名称";
    input.addEventListener("input", () => { mo2State.drafts.set(id, input.value); input.setCustomValidity(""); });
    label.appendChild(input);
    form.appendChild(label);
    const candidates = Array.isArray(job.candidates) ? job.candidates : [];
    if (candidates.length) {
      const candidatesLabel = mo2Element("label", "mo2-candidates-label");
      candidatesLabel.appendChild(mo2Element("span", "", "候选"));
      const select = mo2Element("select", "mo2-candidates");
      const placeholder = mo2Element("option", "", "已安装模组");
      placeholder.value = "";
      select.appendChild(placeholder);
      for (const candidate of candidates) {
        const option = mo2Element("option", "", candidate.name);
        option.value = candidate.name;
        option.title = candidate.reason || "";
        select.appendChild(option);
      }
      select.addEventListener("change", () => {
        if (!select.value) return;
        input.value = select.value;
        input.setCustomValidity("");
        mo2State.drafts.set(id, select.value);
        const reason = row.querySelector(".mo2-job-reason");
        if (reason) reason.textContent = candidates.find((candidate) => candidate.name === select.value)?.reason || job.matchReason || "";
      });
      candidatesLabel.appendChild(select);
      form.appendChild(candidatesLabel);
    }
    const actions = mo2Element("div", "mo2-job-actions");
    const confirm = mo2Element("button", "btn btn-primary", "安装");
    confirm.title = "确认名称并安装";
    confirm.type = "submit";
    const skip = mo2Element("button", "btn", "跳过");
    skip.title = "跳过安装，保留安装包";
    skip.type = "button";
    skip.addEventListener("click", () => mo2JobAction(job, "skip", {}, row, error));
    actions.append(confirm, skip);
    form.appendChild(actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const installName = input.value.trim();
      if (!installName || /[<>:"/\\|?*\x00-\x1F]/.test(installName) || /[. ]$/.test(installName) || installName === "." || installName === "..") {
        input.setCustomValidity("请输入有效的模组文件夹名称，不能包含 \\ / : * ? \" < > | 或以句点结尾。");
        input.reportValidity();
        return;
      }
      mo2JobAction(job, "confirm", { installName }, row, error);
    });
    row.appendChild(form);
  } else if (displayName) {
    row.appendChild(mo2Element("p", "mo2-job-message", displayName));
  }
  if (job.state === "error" && job.message) row.appendChild(mo2Element("p", "mo2-job-error", job.message));
  if (job.state === "queued" || job.state === "installing") {
    const message = job.state === "queued"
      ? (mo2BridgeConnected() ? "等待 MO2 接收" : "等待 MO2 连接")
      : (mo2BridgeConnected() ? "在 MO2 中完成选项" : "等待 MO2 返回结果");
    const status = mo2Element("p", "mo2-job-message mo2-job-status", message);
    status.setAttribute("role", "status");
    row.appendChild(status);
  }
  if (job.archiveDeleted) row.appendChild(mo2Element("p", "mo2-job-message", "安装包已删除"));
  if (job.cleanupError) row.appendChild(mo2Element("p", "mo2-job-error", `清理失败：${job.cleanupError}`));
  if ((job.state === "error" || job.state === "cancelled") && !job.archiveDeleted) {
    const actions = mo2Element("div", "mo2-job-actions");
    const retry = mo2Element("button", "btn", "重试");
    retry.type = "button";
    retry.addEventListener("click", () => mo2JobAction(job, "retry", {}, row, error));
    actions.appendChild(retry);
    row.appendChild(actions);
  }
  row.appendChild(error);
  return row;
}

async function mo2JobAction(job, action, payload, row, errorElement) {
  const id = String(job.id);
  if (mo2State.busy.has(id)) return;
  mo2State.busy.add(id);
  row.querySelectorAll("button, input, select").forEach((element) => { element.disabled = true; });
  errorElement.classList.add("hidden");
  try {
    const result = await api(`/api/mo2/jobs/${encodeURIComponent(id)}/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (result.ok === false) throw new Error(result.message || "操作失败");
    mo2State.busy.delete(id);
    if (Array.isArray(result.jobs)) showMo2Status(result);
    else await refreshMo2Status();
  } catch (error) {
    errorElement.textContent = error.message;
    errorElement.classList.remove("hidden");
  } finally {
    mo2State.busy.delete(id);
    row.querySelectorAll("button, input, select").forEach((element) => { element.disabled = false; });
  }
}

/* 背景：预加载后淡入，每五分钟随机切换。 */
const wallpaperState = {
  settings: null, dirty: false, timer: null, nextAt: 0,
  version: 0, settingsRevision: 0, loadingVersion: null, token: "", activeLayer: 0,
  imageUrls: [null, null],
};
const wallpaperPreview = {
  settings: { ok: true, enabled: true, directory: "E:\\BaiduSyncdisk\\壁纸图片", opacity: .22, intervalSeconds: 300 },
  next: { ok: false, message: "暂无图片" },
};

function previewWallpaperApi(path, options) {
  if (path === "/api/wallpaper/settings") {
    if (options?.body) Object.assign(wallpaperPreview.settings, JSON.parse(options.body));
    return { ...wallpaperPreview.settings };
  }
  return { ...wallpaperPreview.next };
}

function wallpaperMessage(message = "") {
  $("wallpaperStatus").textContent = message;
  $("wallpaperStatus").classList.toggle("hidden", !message);
}

function previewWallpaperOpacity() {
  const transparency = Number($("wallpaperOpacity").value);
  $("wallpaperOpacityValue").textContent = `${transparency}%`;
  document.documentElement.style.setProperty("--wallpaper-opacity", String((100 - transparency) / 100));
}

function stopWallpaperTimer() {
  if (wallpaperState.timer) clearTimeout(wallpaperState.timer);
  wallpaperState.timer = null;
  wallpaperState.nextAt = 0;
}

function scheduleWallpaper(version, delay = 300000) {
  stopWallpaperTimer();
  if (version !== wallpaperState.version || !wallpaperState.settings?.enabled) return;
  wallpaperState.nextAt = Date.now() + delay;
  wallpaperState.timer = setTimeout(() => rotateWallpaper(version), delay);
}

function preloadWallpaper(url) {
  return new Promise((resolve, reject) => {
    const picture = new Image();
    picture.decoding = "async";
    picture.fetchPriority = "low";
    const timeout = setTimeout(() => {
      picture.onload = picture.onerror = null;
      picture.src = "";
      reject(new Error("图片读取超时"));
    }, 20000);
    picture.onload = async () => {
      try {
        if (picture.decode) await picture.decode();
        resolve();
      } catch { reject(new Error("图片读取失败")); }
      finally { clearTimeout(timeout); }
    };
    picture.onerror = () => { clearTimeout(timeout); reject(new Error("图片读取失败")); };
    picture.src = url;
  });
}

async function rotateWallpaper(version = wallpaperState.version) {
  if (version !== wallpaperState.version || !wallpaperState.settings?.enabled || wallpaperState.loadingVersion === version) return;
  wallpaperState.loadingVersion = version;
  stopWallpaperTimer();
  let delay = 300000;
  let preparedUrl = null;
  try {
    const metadataAbort = new AbortController();
    const metadataTimer = setTimeout(() => metadataAbort.abort(), 20000);
    let result;
    try {
      result = await api(`/api/wallpaper/next?exclude=${encodeURIComponent(wallpaperState.token)}`, { signal: metadataAbort.signal });
    } finally { clearTimeout(metadataTimer); }
    if (version !== wallpaperState.version) return;
    if (!result.ok) throw new Error(result.message || "暂无图片");
    if (typeof result.url !== "string" || !result.url.startsWith("/api/wallpaper/image/")) throw new Error("图片地址无效");
    const abort = new AbortController();
    const fetchTimer = setTimeout(() => abort.abort(), 20000);
    try {
      const response = await fetch(result.url, { signal: abort.signal, cache: "no-store" });
      if (!response.ok) throw new Error("图片读取失败");
      preparedUrl = URL.createObjectURL(await response.blob());
    } finally { clearTimeout(fetchTimer); }
    await preloadWallpaper(preparedUrl);
    if (version !== wallpaperState.version) return;
    const previous = wallpaperState.activeLayer;
    const next = 1 - previous;
    const layers = [$("wallpaperA"), $("wallpaperB")];
    const retiredUrl = wallpaperState.imageUrls[next];
    layers[next].style.backgroundImage = `url(${JSON.stringify(preparedUrl)})`;
    wallpaperState.imageUrls[next] = preparedUrl;
    preparedUrl = null;
    if (retiredUrl) URL.revokeObjectURL(retiredUrl);
    layers[next].classList.add("is-visible");
    layers[previous].classList.remove("is-visible");
    wallpaperState.activeLayer = next;
    wallpaperState.token = result.token || "";
    $("wallpaperBackdrop").classList.toggle("hidden", !$("wallpaperEnabled").checked);
    wallpaperMessage();
  } catch (error) {
    if (version === wallpaperState.version) wallpaperMessage(error.name === "AbortError" ? "图片读取超时" : error.message);
    delay = 30000;
  } finally {
    if (preparedUrl) URL.revokeObjectURL(preparedUrl);
    if (wallpaperState.loadingVersion === version) wallpaperState.loadingVersion = null;
    if (version === wallpaperState.version) scheduleWallpaper(version, delay);
  }
}

function applyWallpaperSettings(settings) {
  const previous = wallpaperState.settings;
  const sourceChanged = !previous || previous.directory !== settings.directory || previous.enabled !== settings.enabled;
  wallpaperState.settings = { ...settings };
  if (!settingEdited("wallpaperEnabled")) $("wallpaperEnabled").checked = settings.enabled;
  if (!settingEdited("wallpaperDir")) $("wallpaperDir").value = settings.directory;
  if (!settingEdited("wallpaperOpacity")) $("wallpaperOpacity").value = Math.round((1 - settings.opacity) * 100);
  previewWallpaperOpacity();
  if (!sourceChanged) return;
  wallpaperState.version++;
  stopWallpaperTimer();
  wallpaperState.token = "";
  if (!settings.enabled) {
    for (const id of ["wallpaperA", "wallpaperB"]) {
      $(id).classList.remove("is-visible");
      $(id).style.backgroundImage = "";
    }
    wallpaperState.imageUrls.forEach((url) => { if (url) URL.revokeObjectURL(url); });
    wallpaperState.imageUrls = [null, null];
  }
  $("wallpaperBackdrop").classList.toggle("hidden", !$("wallpaperEnabled").checked);
  if (settings.enabled) rotateWallpaper(wallpaperState.version);
  else wallpaperMessage();
}

async function loadWallpaperSettings() {
  const revision = ++wallpaperState.settingsRevision;
  try {
    const settings = await api("/api/wallpaper/settings");
    if (revision !== wallpaperState.settingsRevision) return;
    if (!settings.ok) throw new Error(settings.message || "背景设置读取失败");
    applyWallpaperSettings(settings);
  } catch (error) {
    if (revision === wallpaperState.settingsRevision) wallpaperMessage(error.message);
  }
}


/* 设置逐项保存：输入防抖，请求串行，旧响应不能覆盖新输入。 */
const settingFields = {
  dlDirInput: ['download', '/api/download/dir', 'dir'],
  threadRange: ['threads', '/api/takeover/threads', 'maxThreads'],
  mo2Path: ['mo2', '/api/mo2/settings', 'mo2Path'],
  mo2Enabled: ['mo2', '/api/mo2/settings', 'enabled'],
  mo2DeleteArchive: ['mo2', '/api/mo2/settings', 'deleteAfterInstall'],
  wallpaperDir: ['wallpaper', '/api/wallpaper/settings', 'directory'],
  wallpaperEnabled: ['wallpaper', '/api/wallpaper/settings', 'enabled'],
  wallpaperOpacity: ['wallpaper', '/api/wallpaper/settings', 'opacity'],
  fontSizeRange: ['appearance', '/api/appearance/settings', 'fontSize'],
};
const settingsSave = {
  pending: new Map(), revisions: new Map(), desired: new Map(), groups: new Map(),
  errors: new Map(), timer: null, running: false, saved: false,
};

function settingEdited(id) { return settingsSave.revisions.has(id); }

function displaySaveState() {
  const messages = [...settingsSave.errors.values()];
  const status = $('settingsSaveState');
  status.textContent = messages.length ? '未保存' : settingsSave.running || settingsSave.pending.size ? '保存中…' : settingsSave.saved ? '已保存' : '';
  status.title = messages.join('\n');
  const notice = $('settingsNotice');
  if (messages.length) {
    if (settingsNoticeTimer) clearTimeout(settingsNoticeTimer);
    notice.dataset.autosave = 'true';
    notice.textContent = messages.join('\n');
    notice.classList.remove('hidden');
  } else if (notice.dataset.autosave) {
    delete notice.dataset.autosave;
    notice.textContent = '';
    notice.classList.add('hidden');
  }
}

function scheduleSettingsSave() {
  if (settingsSave.timer) clearTimeout(settingsSave.timer);
  settingsSave.timer = null;
  if (!settingsSave.running && settingsSave.pending.size) {
    const due = Math.min(...[...settingsSave.pending.values()].map(item => item.due));
    settingsSave.timer = setTimeout(drainSettingsSave, Math.max(0, due - Date.now()));
  }
  displaySaveState();
}

function queueSetting(id, delay = 0) {
  const control = $(id);
  const [group, endpoint, field] = settingFields[id];
  const value = control.type === 'checkbox' ? control.checked : control.type === 'range'
    ? (id === 'wallpaperOpacity' ? (100 - Number(control.value)) / 100 : Number(control.value)) : control.value.trim();
  if (settingsSave.desired.get(id) === value && !settingsSave.errors.has(id)) {
    if (!delay && settingsSave.pending.has(id)) settingsSave.pending.get(id).due = Date.now();
    scheduleSettingsSave();
    return;
  }
  const revision = (settingsSave.revisions.get(id) || 0) + 1;
  settingsSave.revisions.set(id, revision);
  settingsSave.desired.set(id, value);
  settingsSave.groups.set(group, (settingsSave.groups.get(group) || 0) + 1);
  settingsSave.errors.delete(id);
  control.removeAttribute('aria-invalid');
  if (group === 'mo2') mo2State.settingsDirty = true;
  if (group === 'wallpaper') { wallpaperState.dirty = true; wallpaperState.settingsRevision++; }
  settingsSave.pending.set(id, { id, group, endpoint, field, value, revision, due: Date.now() + delay, attempts: 0 });
  scheduleSettingsSave();
}

function flushSettingsSave() {
  for (const item of settingsSave.pending.values()) item.due = Date.now();
  scheduleSettingsSave();
}

async function drainSettingsSave() {
  if (settingsSave.running) return;
  const item = [...settingsSave.pending.values()].filter(value => value.due <= Date.now()).sort((a, b) => a.due - b.due)[0];
  if (!item) { scheduleSettingsSave(); return; }
  settingsSave.pending.delete(item.id);
  settingsSave.running = true;
  displaySaveState();
  const groupRevision = settingsSave.groups.get(item.group);
  let timeout;
  try {
    if (['dlDirInput', 'mo2Path', 'wallpaperDir'].includes(item.id) && !/^(?:[a-z]:[\\/]|\\\\[^\\]+\\[^\\]+|\/)/i.test(item.value)) {
      const error = new Error('请填写完整目录'); error.status = 400; throw error;
    }
    const abort = new AbortController();
    timeout = setTimeout(() => abort.abort(), 15000);
    const result = await api(item.endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [item.field]: item.value }), signal: abort.signal, keepalive: true,
    });
    if (result.ok === false) throw new Error(result.message || '保存失败');
    if (settingsSave.revisions.get(item.id) === item.revision) {
      settingsSave.errors.delete(item.id);
      $(item.id).removeAttribute('aria-invalid');
      settingsSave.saved = true;
    }
    if (settingsSave.groups.get(item.group) === groupRevision) {
      if (item.group === 'mo2') {
        mo2State.settingsLoaded = true;
        mo2State.settingsDirty = false;
        showMo2Status(result, true);
      } else if (item.group === 'wallpaper') {
        wallpaperState.dirty = false;
        applyWallpaperSettings(result);
      }
    }
    // A corrected directory can make an earlier enable request valid.
    for (const id of [...settingsSave.errors.keys()]) {
      if (id !== item.id && settingFields[id][0] === item.group && !settingsSave.pending.has(id)) queueSetting(id);
    }
  } catch (error) {
    if (settingsSave.revisions.get(item.id) === item.revision) {
      const labels = { download: '下载目录', threads: '并发线程', mo2: 'MO2', wallpaper: '背景', appearance: '字体' };
      settingsSave.errors.set(item.id, labels[item.group] + '：' + (error.name === 'AbortError' ? '保存超时' : error.message));
      $(item.id).setAttribute('aria-invalid', 'true');
      if (!error.status || error.status >= 500) {
        item.attempts++;
        item.due = Date.now() + Math.min(30000, 2000 * 2 ** Math.min(item.attempts - 1, 4));
        settingsSave.pending.set(item.id, item);
      }
    }
  } finally {
    clearTimeout(timeout);
    settingsSave.running = false;
    scheduleSettingsSave();
  }
}

function previewFontSize() {
  const size = Number($('fontSizeRange').value);
  $('fontSizeValue').textContent = size;
  document.documentElement.style.setProperty('--font-scale', String(size / 13));
}

async function loadAppearance() {
  try {
    const result = await api('/api/appearance/settings');
    if (!settingEdited('fontSizeRange') && result.ok && Number.isInteger(result.fontSize)) {
      $('fontSizeRange').value = result.fontSize;
      previewFontSize();
    }
  } catch { /* 默认字号不影响启动；下次修改会自动重试保存。 */ }
}

async function loadThreads() {
  try {
    const result = await api('/api/takeover/threads');
    if (!settingEdited('threadRange') && result.maxThreads) {
      $('threadRange').value = result.maxThreads;
      $('threadValue').textContent = result.maxThreads;
    }
  } catch { /* 宿主未就绪时保持默认 256。 */ }
}

/* ----------------------------------------------------------------- events */
function wireEvents() {
  const settingsDialog = $("settingsDialog");
  $("btnSettings").addEventListener("click", () => {
    if (!settingsDialog.open) {
      hideNotice();
      settingsDialog.showModal();
      document.body.classList.add("dialog-open");
      displaySaveState();
    }
  });
  $("btnSettingsClose").addEventListener("click", () => settingsDialog.close());
  settingsDialog.addEventListener("close", () => {
    flushSettingsSave();
    document.body.classList.remove("dialog-open");
    if (settingsNoticeTimer) clearTimeout(settingsNoticeTimer);
    $("settingsNotice").classList.add("hidden");
    $("settingsNotice").textContent = "";
  });
  settingsDialog.addEventListener("click", (event) => {
    if (event.target !== settingsDialog) return;
    const bounds = settingsDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) settingsDialog.close();
  });
  $("btnRefresh").addEventListener("click", () => startRefresh(true));
  $("btnDiscardToggle").addEventListener("click", () => {
    state.showDiscarded = !state.showDiscarded;
    renderList();
  });

  $("btnProfile").addEventListener("click", async () => {
    try {
      await api("/api/auth/profile", { method: "POST" });
      setNotice("等待浏览器登录");
      pollLoginImport();
    } catch (e) {
      setNotice(`启动失败: ${e.message}`);
    }
  });


  $('noticeClose').addEventListener('click', hideNotice);
  for (const id of Object.keys(settingFields)) {
    const control = $(id);
    control.addEventListener('compositionstart', () => {
      settingsSave.revisions.set(id, (settingsSave.revisions.get(id) || 0) + 1);
      const group = settingFields[id][0];
      settingsSave.groups.set(group, (settingsSave.groups.get(group) || 0) + 1);
      settingsSave.pending.delete(id);
      settingsSave.desired.delete(id);
      if (group === 'wallpaper') wallpaperState.settingsRevision++;
      scheduleSettingsSave();
    });
    control.addEventListener('input', event => {
      if (id === 'threadRange') $('threadValue').textContent = control.value;
      if (id === 'wallpaperOpacity') previewWallpaperOpacity();
      if (id === 'fontSizeRange') previewFontSize();
      if (id === 'wallpaperEnabled') $('wallpaperBackdrop').classList.toggle('hidden', !control.checked);
      if (!event.isComposing) queueSetting(id, control.type === 'checkbox' ? 0 : control.type === 'range' ? 150 : 550);
    });
    control.addEventListener('change', () => queueSetting(id));
    control.addEventListener('compositionend', () => queueSetting(id, 550));
    control.addEventListener('blur', () => {
      if (settingsSave.pending.has(id)) { settingsSave.pending.get(id).due = Date.now(); scheduleSettingsSave(); }
    });
    control.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); queueSetting(id); }
    });
  }
  for (const id of ['mo2Settings', 'wallpaperSettings']) {
    $(id).addEventListener('submit', event => { event.preventDefault(); flushSettingsSave(); });
  }
  for (const [button, input] of [['btnBrowseDir', 'dlDirInput'], ['btnMo2Browse', 'mo2Path'], ['btnWallpaperBrowse', 'wallpaperDir']]) {
    $(button).addEventListener('click', async () => {
      const previous = $(input).value;
      await browseDir(input);
      if ($(input).value !== previous) queueSetting(input);
    });
  }
  let searchFrame = null;
  $("modSearch").addEventListener("input", () => {
    state.query = $("modSearch").value;
    if (searchFrame) cancelAnimationFrame(searchFrame);
    searchFrame = requestAnimationFrame(() => { searchFrame = null; renderList(); });
  });
  const clearSearch = () => {
    $("modSearch").value = state.query = "";
    renderList();
    $("modSearch").focus({ preventScroll: true });
  };
  $("btnClearSearch").addEventListener("click", clearSearch);
  $("modSearch").addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !settingsDialog.open) { event.preventDefault(); clearSearch(); }
  });
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.isComposing || settingsDialog.open) return;
    if ((event.ctrlKey || event.metaKey) && event.key === ",") {
      event.preventDefault();
      $("btnSettings").click();
      return;
    }
    const target = event.target;
    const editing = target?.matches?.("input, textarea, select") || target?.isContentEditable;
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !editing) {
      event.preventDefault();
      $("modSearch").focus({ preventScroll: true });
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) flushSettingsSave();
    if (!document.hidden && wallpaperState.nextAt && Date.now() >= wallpaperState.nextAt) rotateWallpaper();
  });
  window.addEventListener("pagehide", () => { flushSettingsSave(); drainSettingsSave(); wallpaperState.version++; stopWallpaperTimer(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) rotateWallpaper(); });
}

/* ------------------------------------------------------------------- boot */
async function boot() {
  if (PREVIEW_MODE) document.body.classList.add("preview");
  loadDlMarks();           // 恢复本地“已下载”实时标记（必须在首次渲染之前）
  wireEvents();
  startMo2Watcher();
  loadWallpaperSettings();
  loadAppearance();
  await refreshStatus(true);
  await loadMods(true);
  await loadDiscarded();
  await loadDir();
  await loadThreads();
  if (!PREVIEW_MODE) startDlWatcher();   // 实时下载进度 + 失败提示

  // 每次启动自动强制刷新（爬取很快，不等待缓存 TTL）
  try {
    const st = await api("/api/status");
    if (!st.demo && st.auth && !(st.job && st.job.running)) {
      await startRefresh(true);
    }
  } catch (e) {
    /* 后台未就绪时忽略，用户可手动点刷新 */
  }
}

boot().catch((e) => {
  setNotice("初始化失败: " + e.message);
});

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
    throw new Error(`HTTP ${res.status}: ${body.slice(0, 160)}`);
  }
  return res.json();
}

/* 静态预览模式的“假后端”：只实现前端用到的几个端点。 */
async function inlineApi(path) {
  const demo = [...inlineDemoData()].sort(
    (a, b) => ((b.updated_at || b.created_at || "") < (a.updated_at || a.created_at || "") ? -1 : 1)
  );
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
    auth.textContent = `会话: 已登录`;
    auth.className = "chip chip-ok";
  } else {
    auth.textContent = "会话: 未登录";
    auth.className = "chip chip-warn";
  }
  $("countChip").textContent = `模组: ${st.mods_count}`;
  const fs = st.filter_stats;
  $("filterChip").textContent = fs && fs.game
    ? `筛选: ${fs.game === "skyrimspecialedition" ? "Skyrim SE" : fs.game} · 保留(更新≥下载) ${fs.kept || 0} · 去除 ${fs.removed_old_downloads || 0}`
    : "筛选: -";
  $("refreshChip").textContent = st.demo ? "数据: 演示数据" : `更新: ${st.last_refresh || "-"}`;
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
        ? `正在抓取下载历史页面…${total > 0 ? ` ${done}/${total}（${Math.round((done / total) * 100)}%）` : ""}${started_at ? ` · 已 ${elapsedSec("job:" + started_at, started_at)} 秒` : ""}`
        : `正在处理 ${done}/${total}${st.job.current ? " · " + st.job.current : ""}`;
    // 预计剩余时间（按已完成的速度外推）
    if (stage !== "collecting_history" && total > 0 && done > 0 && started_at) {
      const elapsed = Date.now() / 1000 - started_at;
      const perItem = elapsed / done;
      const etaSec = perItem * (total - done);
      if (etaSec > 30) {
        msg += ` · 预计剩余约 ${Math.ceil(etaSec / 60)} 分钟`;
      } else if (etaSec > 0) {
        msg += ` · 预计剩余约 ${Math.ceil(etaSec)} 秒`;
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
      setNotice("扫描完成，列表已更新");
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

async function toggleDiscard(modId) {
  try {
    const d = await api("/api/discarded/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mod_id: Number(modId) }),
    });
    state.discarded = new Set((d.ids || []).map(Number));
  } catch (e) {
    setNotice(`丢弃标记失败: ${e.message}`);
    return;
  }
  renderList();
}

function updateDiscardBtn() {
  const b = $("btnDiscardToggle");
  if (!b) return;
  b.textContent = `历史丢弃 (${state.discarded.size})`;
  b.classList.toggle("btn-active", state.showDiscarded);
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
function setNotice(text) {
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
  $("countChip").textContent = `模组: ${state.total}`;
  $("empty").classList.toggle("hidden", state.total > 0);
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
  const order = [...state.items].sort(byUpdatedDesc);
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
    h.textContent = `历史丢弃 (${dropped.length})`;
    list.appendChild(h);
    for (const m of dropped) {
      const c = buildCard(m, null);   // 被丢弃的模组不显示序号
      c.classList.add("discarded");
      if (isDlMarked(Number(m.mod_id))) c.classList.add("dl-marked");
      list.appendChild(c);
    }
  }
  updateDiscardBtn();
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
  up.textContent = "更新 " + fmtCompact(m.updated_at || m.created_at);
  dates.appendChild(up);
  const dl = document.createElement("span");
  dl.className = "chip dl-chip";
  dl.textContent = "下载 " + fmtCompact(m.downloaded_at);
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
  db.textContent = state.discarded.has(m.mod_id) ? "恢复" : "丢弃";
  db.title = "标记为丢弃（不再显示，可在「历史丢弃」中找回），不影响缓存数据";
  db.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleDiscard(m.mod_id);
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
  if (PREVIEW_MODE) return;
  try {
    const r = await api("/api/download/dir");
    $("dlDirInput").value = r.dir || "";
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

/* 并发线程滑块：实时调整宿主并发（对新下载任务生效） */
async function loadThreads() {
  if (PREVIEW_MODE) return;
  try {
    const r = await api("/api/takeover/threads");
    if (r.ok && r.maxThreads) {
      $("threadRange").value = r.maxThreads;
      $("threadValue").textContent = r.maxThreads;
    }
  } catch (e) { /* 宿主未就绪时保持默认 64 */ }
}

function saveThreads() {
  const v = Number($("threadRange").value);
  $("threadValue").textContent = v;
  api("/api/takeover/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ maxThreads: v }),
  })
    .then((r) => { if (!r.ok) setNotice(r.message || "设置线程数失败"); })
    .catch((e) => setNotice(`设置线程数失败: ${e.message}`));
}

function saveDir() {
  const d = $("dlDirInput").value.trim();
  if (!d) { setNotice("下载目录不能为空"); return; }
  api("/api/download/dir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir: d }),
  })
    .then((r) => {
      $("dlDirInput").value = r.dir || d;
      setNotice(`默认下载目录已保存：${r.dir || d}`);
    })
    .catch((e) => setNotice(`保存下载目录失败: ${e.message}`));
}

/* ----------------------------------------------------------------- events */
function wireEvents() {
  $("btnRefresh").addEventListener("click", () => startRefresh(true));
  $("btnDiscardToggle").addEventListener("click", () => {
    state.showDiscarded = !state.showDiscarded;
    renderList();
  });

  $("btnProfile").addEventListener("click", async () => {
    try {
      await api("/api/auth/profile", { method: "POST" });
      setNotice("正在加载浏览器资料；若窗口空白请手动打开 N 网并登录，完成后自动继续");
      pollLoginImport();
    } catch (e) {
      setNotice(`启动失败: ${e.message}`);
    }
  });

  $("btnDlDir").addEventListener("click", saveDir);
  $("btnBrowseDir").addEventListener("click", () => browseDir("dlDirInput"));
  $("noticeClose").addEventListener("click", hideNotice);
  $("threadRange").addEventListener("input", () => {
    $("threadValue").textContent = $("threadRange").value;
  });
  $("threadRange").addEventListener("change", saveThreads);
}

/* ------------------------------------------------------------------- boot */
async function boot() {
  if (PREVIEW_MODE) document.body.classList.add("preview");
  loadDlMarks();           // 恢复本地“已下载”实时标记（必须在首次渲染之前）
  wireEvents();
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
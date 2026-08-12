/* ════════════════════════════════════════
   全台堰塞湖清冊 · 邏輯
   資料由 data/lakes.js 提供（window.BARRIER_LAKES）
   來源：農村水保署堰塞湖清冊，座標已轉為 WGS84
   ════════════════════════════════════════ */

'use strict';

const LAKES = (window.BARRIER_LAKES || []).slice();

/* 風險模型輸出（見 pipeline/ingest/risk.py）：以清冊名稱去頭尾空白比對，
   目前 71/75 筆有對應，其餘 4 筆沒有評估——顯示為「無風險評估」，
   絕不能預設成低風險。 */
const RISK = window.LAKE_RISK || {};
const RISK_META = window.RISK_MODEL_META || null;

LAKES.forEach(lake => {
  lake.risk = RISK[(lake.name || '').trim()] || null;
  lake.cap = (typeof CAP !== 'undefined')
    ? CAP.build(lake, lake.risk, RISK_META)
    : null;
});

const STATUS_TEXT = { watch: '監測中', stable: '存在已穩定', gone: '已消失' };
const CAUSE_TEXT  = { quake: '地震', typhoon: '颱風', rain: '降雨', slide: '崩塌', other: '未記載' };
const SEVERITY_TEXT = { Extreme: '非常嚴重', Severe: '嚴重', Moderate: '有威脅', Minor: '輕微', Unknown: '未知' };
const URGENCY_TEXT  = { Immediate: '立即', Expected: '應盡快', Future: '未來', Past: '已過期', Unknown: '未知' };
const CERTAINTY_TEXT = { Observed: '已確認', Likely: '可能發生', Possible: '有可能', Unlikely: '不太可能', Unknown: '未知' };

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  selected: null,
  status: 'all',
  cause: 'all',
  year: null,
  risk: 'all',       // all / high / low / none
  county: 'all',
  hasCap: false,     // 只顯示有 CAP 草稿（= 有風險評估）的湖泊
  keyword: ''
};

/* 供搜尋/篩選使用的風險分類，跟 riskBadgeInfo() 用同一套邏輯，
   避免兩處各自判斷造成不一致。 */
function riskCategory(lake) {
  if (!lake.risk) return 'none';
  return lake.risk.risk_level === '高' ? 'high' : 'low';
}


/* ── 1. 篩選 ───────────────────────────── */

function matches(lake) {
  if (state.status !== 'all' && lake.statusKey !== state.status) return false;
  if (state.cause  !== 'all' && lake.causeKey  !== state.cause)  return false;
  if (state.year   !== null  && lake.year      !== state.year)   return false;
  if (state.risk   !== 'all' && riskCategory(lake) !== state.risk) return false;
  if (state.county !== 'all' && lake.county !== state.county) return false;
  if (state.hasCap && !(lake.risk && lake.cap)) return false;
  if (state.keyword) {
    const kw = state.keyword.trim().toLowerCase();
    if (kw) {
      const hay = `${lake.name} ${lake.county} ${lake.town} ${lake.village} ${lake.landmark || ''}`.toLowerCase();
      if (!hay.includes(kw)) return false;
    }
  }
  return true;
}

function visibleLakes() {
  return LAKES.filter(matches);
}


/* ── 2. 地圖（3D 立體地形，見 map3d.js）──── */

/* 立體地形本身的形狀直接來自 data/terrain.js 的高程網格（由 DEM tif 縮小
   取樣而來），這裡只負責：初始化場景、把清冊座標交給 Map3D 建立標記、
   以及在篩選/選取狀態改變時同步視覺狀態（dim/active/CAP 高風險環）。 */

function initMap3D() {
  const wrap = $('#map3dWrap');
  const canvas = $('#map3dCanvas');
  const labels = $('#map3dLabels');
  const resetBtn = $('#map3dReset');

  if (typeof THREE === 'undefined' || typeof Map3D === 'undefined' || !window.TAIWAN_TERRAIN) {
    if (wrap) {
      wrap.innerHTML = '<p class="map3d-fallback">立體地形載入失敗——請確認可連線至 three.js CDN，' +
        '且 data/terrain.js 已正確載入。</p>';
    }
    return;
  }

  Map3D.init(canvas, labels, resetBtn, {
    onSelect: id => select(id),
    onHoverStart: (id, coords) => { setPreview(id); showHoverCard(id, coords); },
    onHoverMove: (id, coords) => positionHoverCard(coords),
    onHoverEnd: id => { clearPreview(id); hideHoverCard(); },
  });
  Map3D.setLakes(LAKES);
  bindFullscreenToggle(wrap);
}

/* 全螢幕：用瀏覽器原生 Fullscreen API，讓地圖區塊(.map3d-wrap)整個佔滿螢幕。
   進出全螢幕時容器尺寸會改變，Map3D 本身已經用 ResizeObserver 監聽
   wrapEl，會自動重新適應尺寸，這裡不用另外呼叫 resize()。 */
function bindFullscreenToggle(wrap) {
  const btn = $('#map3dFullscreen');
  if (!btn || !wrap) return;

  btn.addEventListener('click', () => {
    if (document.fullscreenElement === wrap) {
      document.exitFullscreen();
    } else if (wrap.requestFullscreen) {
      wrap.requestFullscreen().catch(() => {});
    }
  });

  document.addEventListener('fullscreenchange', () => {
    const isFs = document.fullscreenElement === wrap;
    wrap.classList.toggle('is-fullscreen', isFs);
    btn.textContent = isFs ? '離開全螢幕' : '全螢幕';
    btn.setAttribute('aria-label', isFs ? '離開全螢幕' : '全螢幕檢視地圖');
  });
}

/* 地圖 Hover 資訊卡：只顯示簡要摘要，不放完整 CAP 內容；
   無風險評估顯示「尚無評估」，不可顯示成 0%。 */
function hoverCardRiskText(lake) {
  if (lake.statusKey === 'gone') return '不適用';
  if (!lake.risk) return '尚無評估';
  return `${lake.risk.risk_level}風險 ${(lake.risk.risk_prob * 100).toFixed(0)}%`;
}

function showHoverCard(id, coords) {
  const card = $('#mapHoverCard');
  const lake = LAKES.find(l => l.id === id);
  if (!card || !lake) return;
  card.innerHTML = `
    <div class="hc-name">${lake.name}</div>
    <div class="hc-meta">${lake.county}${lake.town}</div>
    <div class="hc-meta">${STATUS_TEXT[lake.statusKey]}｜${hoverCardRiskText(lake)}</div>
    <div class="hc-meta">蓄水量 ${lake.volume ? lake.volume.toLocaleString() : '—'} 萬 m³</div>`;
  positionHoverCard(coords);
  card.hidden = false;
}

function positionHoverCard(coords) {
  const card = $('#mapHoverCard');
  if (!card || !coords) return;
  card.style.left = `${coords.x + 16}px`;
  card.style.top = `${coords.y + 16}px`;
}

function hideHoverCard() {
  const card = $('#mapHoverCard');
  if (card) card.hidden = true;
}

function syncMarkers() {
  const visible = visibleLakes();
  const visibleIds = new Set(visible.map(l => l.id));
  const highRiskIds = new Set(LAKES.filter(l => CAP.shouldAlert(l)).map(l => l.id));

  const selectedLake = LAKES.find(l => l.id === state.selected);
  const capArea = (selectedLake && selectedLake.risk && selectedLake.cap && selectedLake.cap.info.area.circle)
    ? { lakeId: selectedLake.id, radiusKm: parseFloat(selectedLake.cap.info.area.circle.split(' ')[1]) }
    : null;

  if (typeof Map3D !== 'undefined') {
    Map3D.sync({ selectedId: state.selected, visibleIds, highRiskIds, capArea });
  }

  $('[data-bind="mapCount"]').textContent = `顯示 ${visibleIds.size} / ${LAKES.length} 處`;
}


/* ── 4. 年度分布 ───────────────────────── */

const TL = { w: 1200, h: 260, padL: 40, padR: 20, padT: 30, padB: 46 };

function buildTimeline() {
  const years = LAKES.map(l => l.year).filter(Boolean);
  const y0 = Math.min(...years), y1 = Math.max(...years);
  const span = y1 - y0 + 1;

  const buckets = {};
  for (let y = y0; y <= y1; y++) buckets[y] = { watch: 0, stable: 0, gone: 0, events: {} };
  LAKES.forEach(l => {
    if (!l.year) return;
    buckets[l.year][l.statusKey]++;
    if (l.event) buckets[l.year].events[l.event] = (buckets[l.year].events[l.event] || 0) + 1;
  });

  const counts = Object.values(buckets).map(b => b.watch + b.stable + b.gone);
  const maxCount = Math.max(...counts);

  const plotW = TL.w - TL.padL - TL.padR;
  const plotH = TL.h - TL.padT - TL.padB;
  const slot = plotW / span;
  const barW = Math.max(6, slot * 0.62);
  const baseY = TL.padT + plotH;

  const xOf = y => TL.padL + (y - y0) * slot + (slot - barW) / 2;
  const hOf = n => (n / maxCount) * plotH;

  /* 標註最高的兩年 */
  const peaks = Object.entries(buckets)
    .map(([y, b]) => ({ year: +y, n: b.watch + b.stable + b.gone, b }))
    .sort((a, b) => b.n - a.n)
    .slice(0, 2)
    .filter(p => p.n >= 4);

  let svg = `<line class="tl-axis" x1="${TL.padL - 6}" y1="${baseY}" x2="${TL.w - TL.padR}" y2="${baseY}"/>`;

  for (let y = y0; y <= y1; y++) {
    const b = buckets[y];
    const n = b.watch + b.stable + b.gone;
    const x = xOf(y);
    let cursor = baseY;
    let segs = '';

    [['gone', b.gone], ['stable', b.stable], ['watch', b.watch]].forEach(([key, cnt]) => {
      if (!cnt) return;
      const h = hOf(cnt);
      cursor -= h;
      segs += `<rect class="seg-${key}" x="${x.toFixed(1)}" y="${cursor.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}"/>`;
    });

    const dim = state.year !== null && state.year !== y;
    const on = state.year === y;

    svg += `
      <g class="tl-bar${dim ? ' is-dim' : ''}${on ? ' is-on' : ''}" data-year="${y}"
         tabindex="${n ? 0 : -1}" role="button" aria-label="${y} 年，${n} 處">
        <rect class="hit" x="${xOf(y).toFixed(1)}" y="${TL.padT}" width="${barW.toFixed(1)}" height="${plotH}"/>
        ${segs}
        ${n >= 4 ? `<text class="tl-count" x="${(x + barW / 2).toFixed(1)}" y="${(cursor - 6).toFixed(1)}" text-anchor="middle">${n}</text>` : ''}
      </g>`;

    if (y % 5 === 0 || y === y0 || y === y1) {
      svg += `<text class="tl-tick" x="${(x + barW / 2).toFixed(1)}" y="${baseY + 20}" text-anchor="middle">${y}</text>`;
    }
  }

  peaks.forEach((p, i) => {
    const topEvent = Object.entries(p.b.events).sort((a, b) => b[1] - a[1])[0];
    if (!topEvent) return;
    const x = xOf(p.year) + barW / 2;
    const labelY = TL.padT - 8 + i * 0;
    svg += `
      <line class="tl-anno-line" x1="${x.toFixed(1)}" y1="${baseY - hOf(p.n) - 22}" x2="${x.toFixed(1)}" y2="${labelY + 4}"/>
      <text class="tl-anno" x="${x.toFixed(1)}" y="${labelY}" text-anchor="${i === 0 ? 'start' : 'end'}">${topEvent[0]}</text>`;
  });

  $('#timeline').innerHTML = svg;

  $$('#timeline .tl-bar').forEach(el => {
    const y = +el.dataset.year;
    const toggle = () => { state.year = state.year === y ? null : y; refresh(); };
    el.addEventListener('click', toggle);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });

  $('[data-bind="timelineNote"]').textContent = state.year
    ? `已選取 ${state.year} 年 · 再次點選取消`
    : `色塊由下而上為已消失、已穩定、監測中 · 高峰年多對應單一重大事件`;
}


/* ── 5. 清單 ───────────────────────────── */

function renderList() {
  const visible = visibleLakes();

  $('#lakeList').innerHTML = visible.map(lake => {
    const risk = riskBadgeInfo(lake);
    return `
    <button class="lake ${lake.id === state.selected ? 'is-active' : ''}" data-id="${lake.id}">
      <span class="yr">${lake.year || '—'}</span>
      <span>
        <span class="name">${lake.name}</span>
        <span class="meta">${lake.county}${lake.town} · ${CAUSE_TEXT[lake.causeKey]}${lake.event ? ' · ' + lake.event : ''}</span>
      </span>
      <span class="vol">
        <span class="num">${lake.volume ? lake.volume.toLocaleString() : '—'}</span><span class="u">萬 m³</span>
      </span>
      <span class="pills">
        <span class="pill s-${lake.statusKey}">${STATUS_TEXT[lake.statusKey]}</span>
        <span class="pill risk-pill ${risk.cls}">${risk.text}</span>
      </span>
    </button>`;
  }).join('');

  $('#listEmpty').hidden = visible.length > 0;
  if (!visible.length) {
    $('#listEmpty').innerHTML = `
      <b>沒有符合條件的紀錄</b>
      目前套用了 ${activeFilterChips().length} 項篩選條件，放寬篩選條件或按「清除全部」再試一次。`;
  }

  renderActiveFilterChips();

  $('[data-bind="listCount"]').textContent = `${visible.length} 筆`;
}

/* 目前生效的篩選條件，統一整理成 { key, label, clear() } 陣列，
   同時給清單數量文字、chips、空清單訊息共用，避免三處各自維護一份邏輯。 */
function activeFilterChips() {
  const chips = [];
  if (state.status !== 'all') chips.push({ key: 'status', label: `存續：${STATUS_TEXT[state.status]}`, clear: () => { state.status = 'all'; } });
  if (state.cause  !== 'all') chips.push({ key: 'cause', label: `誘因：${CAUSE_TEXT[state.cause]}`, clear: () => { state.cause = 'all'; } });
  if (state.year   !== null)  chips.push({ key: 'year', label: `${state.year} 年`, clear: () => { state.year = null; } });
  if (state.risk   !== 'all') chips.push({ key: 'risk', label: `風險：${{ high: '高風險', low: '低風險', none: '尚無評估' }[state.risk]}`, clear: () => { state.risk = 'all'; } });
  if (state.county !== 'all') chips.push({ key: 'county', label: state.county, clear: () => { state.county = 'all'; } });
  if (state.hasCap) chips.push({ key: 'hasCap', label: '僅看有 CAP 草稿', clear: () => { state.hasCap = false; } });
  if (state.keyword.trim()) chips.push({ key: 'keyword', label: `搜尋：${state.keyword.trim()}`, clear: () => { state.keyword = ''; const input = $('#searchInput'); if (input) input.value = ''; } });
  return chips;
}

function renderActiveFilterChips() {
  const box = $('#activeChips');
  if (!box) return;
  const chips = activeFilterChips();

  box.innerHTML = chips.map(c =>
    `<button class="chip" type="button" data-clear="${c.key}" aria-label="清除篩選：${c.label}">${c.label} ×</button>`
  ).join('');

  $$('#activeChips .chip').forEach((el, i) => {
    el.addEventListener('click', () => { chips[i].clear(); syncFilterControls(); refresh(); });
  });

  $('#resetBtn').hidden = chips.length === 0;
}

/* 篩選條件被 chips 的 x 清掉時，要把對應的按鈕/輸入框視覺同步回「全部」 */
function syncFilterControls() {
  $$('.filter').forEach(b => {
    const kind = b.dataset.kind;
    if (kind && state[kind] !== undefined) b.classList.toggle('is-on', b.dataset.val === state[kind]);
  });
  const countySelect = $('#countySelect');
  if (countySelect) countySelect.value = state.county;
  const capToggle = $('#hasCapToggle');
  if (capToggle) capToggle.checked = state.hasCap;
  const input = $('#searchInput');
  if (input) input.value = state.keyword;
}


/* ── 6. 詳情 ───────────────────────────── */

function renderDetail() {
  const lake = LAKES.find(l => l.id === state.selected);
  if (!lake) return;

  renderEventHeader(lake);
  renderDecisionSummary(lake);
  renderConclusion(lake);
  renderActionCard(lake);
  renderEvidenceCard(lake);
  renderBasicFacts(lake);
  renderCapDraft(lake);
}

/* 存續狀態（監測中/已穩定/已消失）與風險等級是兩個不同概念，
   絕不能只顯示一個標籤讓評審誤把「監測中」當成風險高低。 */
function riskBadgeInfo(lake) {
  if (lake.statusKey === 'gone') return { text: '不適用', cls: 'is-na' };
  if (!lake.risk) return { text: '尚無評估', cls: 'is-none' };
  return lake.risk.risk_level === '高'
    ? { text: '高風險', cls: 'is-high' }
    : { text: '低風險', cls: 'is-low' };
}

function renderEventHeader(lake) {
  const set = (key, val) => {
    const el = $(`[data-bind="${key}"]`);
    if (el) el.textContent = val;
  };

  const statusBadge = $('[data-bind="statusBadge"]');
  statusBadge.className = `badge badge-status s-${lake.statusKey}`;
  statusBadge.textContent = lake.status || STATUS_TEXT[lake.statusKey];

  const risk = riskBadgeInfo(lake);
  const riskBadge = $('[data-bind="riskBadge"]');
  riskBadge.hidden = false;
  riskBadge.className = `badge badge-risk ${risk.cls}`;
  riskBadge.textContent = risk.text;

  const capBadge = $('.badge-cap');
  const capLabel = capBadgeText(lake);
  capBadge.hidden = !capLabel;
  if (capLabel) capBadge.textContent = capLabel;

  set('name', lake.name);
  set('where', `${lake.county}${lake.town}${lake.village} · ${lake.lat.toFixed(4)}°N ${lake.lon.toFixed(4)}°E`);
}

/* CAP 標籤三態：已消失→已過期（urgency=Past，跟 cap.js 邏輯一致）；
   已穩定且有風險評估→草稿（嚴重度已被現況下修，非最終判定）；
   監測中且有風險評估→CAP TEST；沒有風險評估則不顯示（沒有可用的 CAP）。 */
function capBadgeText(lake) {
  if (!lake.cap) return null;
  if (lake.statusKey === 'gone') return '已過期';
  if (!lake.risk) return null;
  if (lake.statusKey === 'stable') return '草稿';
  return 'CAP TEST';
}

/* 快速決策摘要：風險機率／CAP 嚴重度／CAP 急迫性／模型確定性
   全部沿用 cap.js 既有的判定結果，這裡不重新計算任何風險邏輯。 */
function renderDecisionSummary(lake) {
  const box = $('#decisionSummary');
  if (!box) return;

  const prob = (lake.risk && lake.risk.risk_prob != null)
    ? `${(lake.risk.risk_prob * 100).toFixed(0)}%` : '尚無評估';
  const info = lake.cap ? lake.cap.info : null;
  const isHigh = !!(lake.risk && lake.risk.risk_level === '高');

  const cell = (label, value, sub, danger) => `
    <div class="cell">
      <span class="label">${label}</span>
      <div class="num${danger ? ' risk-high' : ''}">${value}</div>
      ${sub ? `<div class="sub">${sub}</div>` : ''}
    </div>`;

  box.innerHTML = [
    cell('風險機率', prob, null, isHigh),
    cell('CAP 嚴重度', info ? info.severity.value : '—', info ? SEVERITY_TEXT[info.severity.value] : null),
    cell('CAP 急迫性', info ? info.urgency.value : '—', info ? URGENCY_TEXT[info.urgency.value] : null),
    cell('模型確定性', info ? info.certainty.value : '—', info ? CERTAINTY_TEXT[info.certainty.value] : null),
  ].join('');
}

/* 系統結論：把既有的 severity/topDrivers 組成 2～3 行摘要，
   文字本身不誇大（不用「AI 已預測潰決」等超出模型能力的說法）。 */
function conclusionHeadline(lake, info) {
  if (lake.statusKey === 'gone') return '壩體已消失，無需示警。';
  if (!lake.risk) return '尚無風險模型評估，請以清冊現況與官方公告為準。';
  if (info.severity.value === 'Severe' || info.severity.value === 'Extreme') return '高風險，建議短期內密切注意。';
  if (info.severity.value === 'Moderate') return '風險經現況修正下修一級，維持例行觀察。';
  return '低風險，維持例行監控。';
}

function renderConclusion(lake) {
  const box = $('#conclusion');
  if (!box) return;

  const info = lake.cap ? lake.cap.info : null;
  const drivers = lake.risk ? CAP.topDrivers(lake.risk, RISK_META) : [];

  box.innerHTML = `
    <div class="line1">系統判定：${conclusionHeadline(lake, info)}</div>
    <div class="line2">主要依據：${drivers.length ? drivers.join('、') : '清冊登載之存續狀態'}</div>
    ${lake.risk ? '<div class="line3">限制：本結果為批次模型推論，仍須配合現地觀測。</div>' : ''}`;
}

/* 建議行動：直接沿用 cap.js 的 instructionFor() 輸出文字，
   只是把整段文字拆成條列，不新增或改寫任何行動內容。 */
function splitInstruction(text) {
  return text.split(/[；。]/).map(s => s.trim()).filter(Boolean);
}

function renderActionCard(lake) {
  const box = $('#actionCard');
  if (!box) return;
  if (!lake.cap) { box.innerHTML = ''; return; }

  const steps = splitInstruction(lake.cap.info.instruction);
  box.innerHTML = `
    <span class="at">建議行動</span>
    <ol>${steps.map(s => `<li>${s}</li>`).join('')}</ol>`;
}

/* 判定依據與限制：整合 severity/urgency/certainty 的 basis 文字、
   topDrivers、以及原本 renderNarrative() 的成因敘述與命中規則，
   資料來源全部已存在，這裡只是重新組織呈現順序。 */
function renderEvidenceCard(lake) {
  const box = $('#evidenceCard');
  if (!box) return;

  if (!lake.risk || !lake.cap) {
    box.innerHTML = `
      <div class="et">判定依據</div>
      <p class="narr-empty">此筆紀錄尚無風險模型評估，無法提供判定依據。</p>`;
    return;
  }

  const info = lake.cap.info;
  const drivers = CAP.topDrivers(lake.risk, RISK_META);
  const rules = lake.rulesFired || [];

  box.innerHTML = `
    <div class="et">判定依據</div>
    <dl class="evidence-row">
      <dt>風險機率</dt><dd>${(lake.risk.risk_prob * 100).toFixed(0)}%</dd>
      <dt>原始模型分級</dt><dd>${lake.risk.risk_level}風險</dd>
      <dt>現況修正</dt><dd>${info.severity.basis}</dd>
      ${RISK_META && RISK_META.mode ? `
      <dt>資料來源</dt><dd>${RISK_META.mode}${
        lake.risk.nearest_station_km != null ? `｜距最近雨量站 ${lake.risk.nearest_station_km} km` : ''}</dd>` : ''}
    </dl>
    ${drivers.length ? `
      <span class="label" style="display:block;margin-bottom:6px">主要驅動因子</span>
      <ul class="evidence-list">${drivers.map(d => `<li>${d}</li>`).join('')}</ul>` : ''}
    <div class="limitation-note">模型限制：${info.certainty.basis}</div>
    ${lake.narrative ? `
      <details class="narr-rules" style="margin-top:12px">
        <summary>成因敘述與命中規則${rules.length ? `（${rules.length} 條）` : ''}</summary>
        <p class="narr-text" style="margin:8px 0 0">${lake.narrative}</p>
        ${rules.length ? `<ul>${rules.map(r => `<li>${r}</li>`).join('')}</ul>` : ''}
      </details>` : ''}`;
}

/* 基本資料：內容與原本完全相同，只是搬進 <details> 收合區塊
   （見 index.html 的 .basic-facts），不再放在畫面第一屏。 */
function renderBasicFacts(lake) {
  const set = (key, val) => {
    const el = $(`[data-bind="${key}"]`);
    if (el) el.textContent = val;
  };

  set('volume', lake.volume ? lake.volume.toLocaleString() : '—');
  set('volumeNote', lake.volume
    ? (lake.volume >= 1000 ? '屬大型，潰決影響範圍可觀' : '清冊登載值')
    : '清冊未登載或規模極小');

  set('year', lake.year || '—');
  set('formed', lake.formed ? `形成於 ${lake.formed}` : '形成日期未記載');

  /* 持續時間欄位混用日數與「持續至今」「<24HR」等文字 */
  const dur = (lake.duration || '').trim();
  const durNum = Number(dur);
  if (dur === '') {
    set('duration', '—'); set('durationUnit', ''); set('durationNote', '未記載');
  } else if (!Number.isNaN(durNum)) {
    set('duration', durNum.toLocaleString()); set('durationUnit', '日');
    set('durationNote', durNum >= 365 ? `約 ${(durNum / 365).toFixed(1)} 年` : '自形成至潰決或穩定');
  } else {
    set('duration', dur); set('durationUnit', ''); set('durationNote', '清冊原始登載');
  }

  const rows = [
    ['誘因',     lake.cause || '未記載',      !lake.cause],
    ['觸發事件', lake.event || '未記載',      !lake.event],
    ['地標',     lake.landmark || '未記載',   !lake.landmark],
    ['坐落區位', lake.setting || '未記載',    !lake.setting],
    ['潰決時間', lake.breachDate || '無紀錄', !lake.breachDate],
    ['潰決原因', lake.breachCause || '無紀錄',!lake.breachCause],
    ['清冊項次', `#${lake.seq}`,              false, true]
  ];

  $('#facts').innerHTML = rows.map(([k, v, muted, mono]) =>
    `<dt>${k}</dt><dd class="${muted ? 'muted' : ''}${mono ? ' mono' : ''}">${v}</dd>`
  ).join('');
}


/* CAP 示警草稿：改成「摘要層（預設展開）＋技術層（點開才展開）」，
   避免評審一開始就看到滿版技術欄位。摘要層只放事件／範圍／有效期間／狀態，
   技術層才放完整的 Event/Urgency/Severity/Certainty/Area/Description/Instruction/basis。
   沒有風險評估的湖泊顯示提示文字，不偽造成低風險。 */
function renderCapDraft(lake) {
  const box = $('#capBlock');
  if (!box) return;

  if (!lake.risk || !lake.cap) {
    box.innerHTML = `
      <div class="cap-summary-card">
        <div class="cs-head"><span class="cs-title">CAP 1.2 示警草稿</span></div>
        <p class="narr-empty">此筆紀錄尚無風險模型評估，無法產生示警草稿。</p>
      </div>`;
    return;
  }

  const info = lake.cap.info;
  const drivers = CAP.topDrivers(lake.risk, RISK_META);

  box.innerHTML = `
    <div class="cap-summary-card">
      <div class="cs-head">
        <span class="cs-title">CAP 1.2 示警草稿</span>
        <span class="cap-status-test">TEST</span>
      </div>
      <div class="cs-row">事件：<b>${info.event}</b></div>
      <div class="cs-row">範圍：<b>${info.area.areaDesc}</b></div>
      <div class="cs-row">有效期間：<b>${fmtDateTime(info.effective)} → ${fmtDateTime(info.expires)}</b></div>
      <div class="cs-row">狀態：<b>待人工確認</b></div>

      <div class="cap-primary-actions">
        <button class="cap-btn ghost" type="button" id="capMapPreviewBtn">地圖預覽</button>
        <button class="cap-btn ghost" type="button" data-toggle="capTech">檢視完整內容</button>
        <button class="cap-btn" type="button" id="capDownloadBtn">下載 CAP XML</button>
      </div>
      <div class="cap-disclaimer">CAP 1.2 測試輸出・非正式對外示警</div>

      <details class="narr-rules cap-tech" id="capTech">
        <summary>技術欄位（Event／Urgency／Severity／Certainty／Area／Effective-Expires／Description／Instruction）</summary>
        <dl class="cap-fields">
          <div class="cap-field"><dt>Event</dt><dd>${info.event}</dd></div>
          <div class="cap-field"><dt>Urgency</dt><dd>${info.urgency.value}（${URGENCY_TEXT[info.urgency.value] || info.urgency.value}）</dd></div>
          <div class="cap-field"><dt>Severity</dt><dd>${info.severity.value}（${SEVERITY_TEXT[info.severity.value] || info.severity.value}）</dd></div>
          <div class="cap-field"><dt>Certainty</dt><dd>${info.certainty.value}（${CERTAINTY_TEXT[info.certainty.value] || info.certainty.value}）</dd></div>
          <div class="cap-field"><dt>Area</dt><dd>${info.area.areaDesc}${info.area.circle ? `（半徑範圍：${info.area.circle.split(' ')[1]} km，暫用圓形頂著，待 DEM 淹沒模擬完成後換成多邊形）` : ''}</dd></div>
          <div class="cap-field"><dt>Effective / Expires</dt><dd>${fmtDateTime(info.effective)} → ${fmtDateTime(info.expires)}</dd></div>
        </dl>
        <div class="cap-text-block">
          <span class="cap-text-label">Description</span>
          <p class="cap-desc">${info.description}</p>
        </div>
        <div class="cap-text-block">
          <span class="cap-text-label">Instruction</span>
          <p class="cap-instruction">${info.instruction}</p>
        </div>
        <details class="narr-rules cap-basis">
          <summary>示警依據（severity / urgency / certainty）</summary>
          <ul>
            <li>severity：${info.severity.basis}</li>
            <li>urgency：${info.urgency.basis}</li>
            <li>certainty：${info.certainty.basis}</li>
            ${drivers.length ? `<li>主要驅動特徵：${drivers.join('、')}</li>` : ''}
          </ul>
        </details>
      </details>`;

  const techEl = $('#capTech');
  const toggleBtn = box.querySelector('[data-toggle="capTech"]');
  if (techEl && toggleBtn) {
    toggleBtn.addEventListener('click', () => { techEl.open = !techEl.open; });
  }

  $('#capDownloadBtn').addEventListener('click', () => downloadCapXml(lake));

  const previewBtn = $('#capMapPreviewBtn');
  if (previewBtn) {
    previewBtn.addEventListener('click', () => {
      const capAreaCheckbox = $('#layerCapArea');
      if (capAreaCheckbox && !capAreaCheckbox.checked) {
        capAreaCheckbox.checked = true;
        if (typeof Map3D !== 'undefined') Map3D.setLayers({ capArea: true });
      }
      $('.map-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('zh-TW', {
      timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch (e) {
    return iso;
  }
}

function downloadCapXml(lake) {
  if (!lake.cap) return;
  const xml = CAP.toXML(lake.cap);
  const blob = new Blob([xml], { type: 'application/xml' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `cap-${lake.id}.xml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  showToast(`已下載 cap-${lake.id}.xml`);
}

/* 簡單的短暫成功提示，2.5 秒後自動移除，不依賴任何 UI 框架 */
function showToast(msg) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = msg;
  toast.setAttribute('role', 'status');
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('is-visible'));
  setTimeout(() => {
    toast.classList.remove('is-visible');
    setTimeout(() => toast.remove(), 220);
  }, 2500);
}


/* CAP 示警橫幅：列出目前風險模型判定為「高」的湖泊。
   與篩選狀態無關——即使使用者篩掉了某湖，示警仍要看得到。 */
function renderCapBar() {
  const bar = $('#capBar');
  if (!bar) return;

  const highRisk = LAKES.filter(l => CAP.shouldAlert(l));
  if (!highRisk.length) {
    bar.hidden = true;
    return;
  }

  const snapshotDates = LAKES.map(l => l.risk && l.risk.date).filter(Boolean).sort();
  const snapshotDate = snapshotDates.length ? fmtDateOnly(snapshotDates[snapshotDates.length - 1]) : '—';

  bar.hidden = false;
  $('[data-bind="capBarText"]').textContent =
    `高風險監測事件 ${highRisk.length} 處　｜　風險快照：${snapshotDate}`;

  $('#capBarChips').innerHTML = highRisk
    .sort((a, b) => (b.risk.risk_prob || 0) - (a.risk.risk_prob || 0))
    .map(l => `<button class="capbar-chip" type="button" data-id="${l.id}">${l.name} ${(l.risk.risk_prob * 100).toFixed(0)}%</button>`)
    .join('');

  $$('#capBarChips .capbar-chip').forEach(el =>
    el.addEventListener('click', () => select(el.dataset.id))
  );

  const viewAllBtn = $('#capBarViewAll');
  if (viewAllBtn) {
    viewAllBtn.onclick = () => {
      state.status = 'watch'; state.risk = 'high';
      $$('.filter[data-kind="status"]').forEach(b => b.classList.toggle('is-on', b.dataset.val === 'watch'));
      syncFilterControls();
      refresh();
      $('#lakeList').scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  }
}


/* ── 7. 統計 ───────────────────────────── */

function renderStats() {
  const by = key => LAKES.filter(l => l.statusKey === key).length;

  const countHighRisk = LAKES.filter(l => CAP.shouldAlert(l)).length;
  const countUnassessed = LAKES.filter(l => l.risk === null).length;

  /* 風險模型是逐湖批次跑出快照，沒有單一全域日期欄位——
     取所有有評估紀錄中最新的一筆快照日期，代表「目前最新一批模型跑到哪一天」。 */
  const snapshotDates = LAKES.map(l => l.risk && l.risk.date).filter(Boolean).sort();
  const riskSnapshotDate = snapshotDates.length
    ? fmtDateOnly(snapshotDates[snapshotDates.length - 1])
    : '—';

  $('[data-bind="countAll"]').textContent          = `${LAKES.length}`;
  $('[data-bind="countWatch"]').textContent        = `${by('watch')} 處`;
  $('[data-bind="countHighRisk"]').textContent      = `${countHighRisk} 處`;
  $('[data-bind="countUnassessed"]').textContent    = `${countUnassessed} 處`;
  $('[data-bind="countStableOrGone"]').textContent  = `${by('stable') + by('gone')} 處`;
  $('[data-bind="riskSnapshotDate"]').textContent    = riskSnapshotDate;

  const riskSourceEl = $('[data-bind="riskSource"]');
  if (riskSourceEl) {
    riskSourceEl.textContent = (RISK_META && RISK_META.mode) || '農村水保署／ERA5-Land';
  }
}

function fmtDateOnly(dateStr) {
  if (!dateStr) return '—';
  const [y, m, d] = dateStr.split('-');
  return `${y}/${m}/${d}`;
}


/* ── 8. 互動 ───────────────────────────── */

function select(id) {
  state.selected = id;
  syncMarkers();
  $$('#lakeList .lake').forEach(b => b.classList.toggle('is-active', b.dataset.id === id));
  renderDetail();
  const panel = $('.detail-panel');
  if (panel) panel.scrollTop = 0;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* 地圖 ↔ 清單 hover 雙向高亮：已經是選取狀態的項目不需要再疊加 preview 效果 */
function setPreview(id) {
  if (!id || id === state.selected) return;
  $$(`.lake[data-id="${id}"]`).forEach(l => l.classList.add('is-preview'));
  if (typeof Map3D !== 'undefined') Map3D.hoverMarker(id);
}
function clearPreview(id) {
  if (!id) return;
  $$(`.lake[data-id="${id}"]`).forEach(l => l.classList.remove('is-preview'));
  if (typeof Map3D !== 'undefined') Map3D.unhoverMarker(id);
}

/* 清單用事件委派綁在容器上一次即可，篩選重繪清單時不用每次重新 addEventListener */
function bindListInteractions() {
  const list = $('#lakeList');
  if (!list) return;
  list.addEventListener('click', e => {
    const btn = e.target.closest('.lake');
    if (btn) select(btn.dataset.id);
  });
  list.addEventListener('mouseover', e => {
    const btn = e.target.closest('.lake');
    if (btn) setPreview(btn.dataset.id);
  });
  list.addEventListener('mouseout', e => {
    const btn = e.target.closest('.lake');
    if (btn) clearPreview(btn.dataset.id);
  });
}

function refresh() {
  syncMarkers();
  buildTimeline();
  renderList();

  /* 若目前選取的紀錄被篩掉，改選第一筆可見紀錄 */
  const visible = visibleLakes();
  if (visible.length && !visible.some(l => l.id === state.selected)) {
    select(visible[0].id);
  }
}

/* 地圖圖層控制：四個勾選框直接對應 Map3D.setLayers() 的四個開關 */
function bindLayerControls() {
  const map = {
    layerPoints: 'points',
    layerHighRisk: 'highRisk',
    layerCapArea: 'capArea',
    layerLabels: 'labels',
  };
  Object.entries(map).forEach(([elId, layerKey]) => {
    const el = $(`#${elId}`);
    if (!el) return;
    el.addEventListener('change', () => {
      if (typeof Map3D !== 'undefined') Map3D.setLayers({ [layerKey]: el.checked });
    });
  });
}

function populateCountyOptions() {
  const select = $('#countySelect');
  if (!select) return;
  const counties = [...new Set(LAKES.map(l => l.county))].sort((a, b) => a.localeCompare(b, 'zh-Hant'));
  select.innerHTML = '<option value="all">全部縣市</option>' +
    counties.map(c => `<option value="${c}">${c}</option>`).join('');
}

function bindFilters() {
  $$('.filter').forEach(btn => {
    btn.addEventListener('click', () => {
      const { kind, val } = btn.dataset;
      state[kind] = val;
      $$(`.filter[data-kind="${kind}"]`).forEach(b =>
        b.classList.toggle('is-on', b.dataset.val === val));
      refresh();
    });
  });

  const countySelect = $('#countySelect');
  if (countySelect) {
    countySelect.addEventListener('change', () => {
      state.county = countySelect.value;
      refresh();
    });
  }

  const capToggle = $('#hasCapToggle');
  if (capToggle) {
    capToggle.addEventListener('change', () => {
      state.hasCap = capToggle.checked;
      refresh();
    });
  }

  /* 關鍵字搜尋用簡單 debounce，避免每敲一個字就重新渲染整份清單 */
  const searchInput = $('#searchInput');
  if (searchInput) {
    let debounceTimer = null;
    searchInput.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        state.keyword = searchInput.value;
        refresh();
      }, 250);
    });
  }

  $('#resetBtn').addEventListener('click', () => {
    state.status = 'all'; state.cause = 'all'; state.year = null;
    state.risk = 'all'; state.county = 'all'; state.hasCap = false; state.keyword = '';
    $$('.filter').forEach(b => b.classList.toggle('is-on', b.dataset.val === 'all'));
    syncFilterControls();
    refresh();
  });
}


/* ── 9. 啟動 ───────────────────────────── */

function init() {
  if (!LAKES.length) {
    $('#lakeList').innerHTML =
      '<div class="empty"><b>找不到清冊資料</b>請確認 data/lakes.js 已產生，' +
      '或執行 tools/csv_to_js.py 重新轉換。</div>';
    return;
  }

  initMap3D();
  renderStats();
  renderCapBar();
  bindFilters();
  bindLayerControls();
  populateCountyOptions();
  bindListInteractions();

  /* 預設選最近一筆監測中的紀錄，沒有就選第一筆 */
  const first = LAKES.find(l => l.statusKey === 'watch') || LAKES[0];
  state.selected = first.id;

  refresh();
  renderDetail();
}

document.addEventListener('DOMContentLoaded', init);
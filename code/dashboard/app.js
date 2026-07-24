/* ════════════════════════════════════════
   全台堰塞湖清冊 · 邏輯
   資料由 data/lakes.js 提供（window.BARRIER_LAKES）
   來源：農村水保署堰塞湖清冊，座標已轉為 WGS84
   ════════════════════════════════════════ */

'use strict';

const LAKES = (window.BARRIER_LAKES || []).slice();

const STATUS_TEXT = { watch: '監測中', stable: '存在已穩定', gone: '已消失' };
const CAUSE_TEXT  = { quake: '地震', typhoon: '颱風', rain: '降雨', slide: '崩塌', other: '未記載' };

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  selected: null,
  status: 'all',
  cause: 'all',
  year: null
};


/* ── 1. 投影：經緯度 → 地圖座標 ─────────── */

const BOUNDS = { west: 119.95, east: 122.05, south: 21.85, north: 25.35 };
const MAP = { w: 460, h: 780 };

function project(lon, lat) {
  return {
    x: (lon - BOUNDS.west) / (BOUNDS.east - BOUNDS.west) * MAP.w,
    y: (BOUNDS.north - lat) / (BOUNDS.north - BOUNDS.south) * MAP.h
  };
}

const COASTLINE = [
  [121.53,25.30],[121.86,25.13],[121.83,24.72],[121.66,24.40],[121.61,23.98],
  [121.50,23.50],[121.38,23.10],[121.15,22.75],[120.86,22.20],[120.85,21.90],
  [120.72,22.00],[120.68,22.20],[120.55,22.55],[120.28,22.61],[120.15,23.00],
  [120.13,23.38],[120.15,23.75],[120.42,24.08],[120.50,24.29],[120.93,24.83],
  [121.10,25.05]
];

const RIDGE = [
  [121.35,24.95],[121.45,24.55],[121.30,24.10],[121.25,23.60],[121.10,23.20],
  [120.95,22.80],[120.80,22.35],[120.72,22.55],[120.85,23.00],[121.00,23.45],
  [121.08,23.95],[121.18,24.45],[121.20,24.90]
];

function toPath(coords) {
  return coords.map(([lon, lat], i) => {
    const p = project(lon, lat);
    return `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
  }).join(' ') + ' Z';
}


/* ── 2. 篩選 ───────────────────────────── */

function matches(lake) {
  if (state.status !== 'all' && lake.statusKey !== state.status) return false;
  if (state.cause  !== 'all' && lake.causeKey  !== state.cause)  return false;
  if (state.year   !== null  && lake.year      !== state.year)   return false;
  return true;
}

function visibleLakes() {
  return LAKES.filter(matches);
}


/* ── 3. 地圖 ───────────────────────────── */

const MAX_VOL = Math.max(...LAKES.map(l => l.volume || 0)) || 1;

/* 蓄水量差距達四個數量級，用平方根壓縮才不會讓小型湖消失 */
function radius(vol) {
  if (!vol) return 3.2;
  return 3.2 + Math.sqrt(vol / MAX_VOL) * 15;
}

function renderBase() {
  $('#mapBase').innerHTML =
    `<path class="coast" d="${toPath(COASTLINE)}"/>` +
    `<path class="ridge" d="${toPath(RIDGE)}"/>`;
}

function renderMarkers() {
  /* 大的畫在下層，小的畫在上層，避免被蓋住 */
  const ordered = LAKES.slice().sort((a, b) => (b.volume || 0) - (a.volume || 0));

  $('#mapMarkers').innerHTML = ordered.map(lake => {
    const p = project(lake.lon, lake.lat);
    const r = radius(lake.volume);
    return `
      <g class="marker s-${lake.statusKey}" data-id="${lake.id}"
         tabindex="0" role="button"
         aria-label="${lake.name}，${lake.year} 年，${STATUS_TEXT[lake.statusKey]}">
        <circle class="halo" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${(r + 6).toFixed(1)}"/>
        <circle class="dot"  cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r.toFixed(1)}"/>
        <text x="${(p.x + r + 8).toFixed(1)}" y="${(p.y + 4).toFixed(1)}">${lake.name}</text>
      </g>`;
  }).join('');

  $$('#mapMarkers .marker').forEach(el => {
    const pick = () => select(el.dataset.id);
    el.addEventListener('click', pick);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
    });
  });
}

function syncMarkers() {
  const visible = new Set(visibleLakes().map(l => l.id));
  $$('#mapMarkers .marker').forEach(m => {
    m.classList.toggle('is-dim', !visible.has(m.dataset.id));
    m.classList.toggle('is-active', m.dataset.id === state.selected);
  });
  $('[data-bind="mapCount"]').textContent = `顯示 ${visible.size} / ${LAKES.length} 處`;
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

  $('#lakeList').innerHTML = visible.map(lake => `
    <button class="lake ${lake.id === state.selected ? 'is-active' : ''}" data-id="${lake.id}">
      <span class="yr">${lake.year || '—'}</span>
      <span>
        <span class="name">${lake.name}</span>
        <span class="meta">${lake.county}${lake.town} · ${CAUSE_TEXT[lake.causeKey]}${lake.event ? ' · ' + lake.event : ''}</span>
      </span>
      <span class="vol">
        <span class="num">${lake.volume ? lake.volume.toLocaleString() : '—'}</span><span class="u">萬 m³</span>
      </span>
      <span class="pill s-${lake.statusKey}">${STATUS_TEXT[lake.statusKey]}</span>
    </button>`).join('');

  $$('#lakeList .lake').forEach(el =>
    el.addEventListener('click', () => select(el.dataset.id))
  );

  $('#listEmpty').hidden = visible.length > 0;

  const bits = [];
  if (state.status !== 'all') bits.push(STATUS_TEXT[state.status]);
  if (state.cause  !== 'all') bits.push(CAUSE_TEXT[state.cause]);
  if (state.year !== null)    bits.push(`${state.year} 年`);
  $('[data-bind="listCount"]').textContent =
    `${visible.length} 筆` + (bits.length ? ` · ${bits.join(' · ')}` : ' · 全部');

  $('#resetBtn').hidden = bits.length === 0;
}


/* ── 6. 詳情 ───────────────────────────── */

function renderDetail() {
  const lake = LAKES.find(l => l.id === state.selected);
  if (!lake) return;

  const set = (key, val) => {
    const el = $(`[data-bind="${key}"]`);
    if (el) el.textContent = val;
  };

  const badge = $('[data-bind="status"]');
  badge.className = `grade s-${lake.statusKey}`;
  badge.textContent = lake.status || STATUS_TEXT[lake.statusKey];

  set('name', lake.name);
  set('where', `${lake.county}${lake.town}${lake.village} · ${lake.lat.toFixed(4)}°N ${lake.lon.toFixed(4)}°E`);

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


/* ── 7. 統計 ───────────────────────────── */

function renderStats() {
  const by = key => LAKES.filter(l => l.statusKey === key).length;
  $('[data-bind="countAll"]').textContent    = `${LAKES.length} 處`;
  $('[data-bind="countWatch"]').textContent  = `${by('watch')} 處`;
  $('[data-bind="countStable"]').textContent = `${by('stable')} 處`;
  $('[data-bind="countGone"]').textContent   = `${by('gone')} 處`;
}


/* ── 8. 互動 ───────────────────────────── */

function select(id) {
  state.selected = id;
  syncMarkers();
  $$('#lakeList .lake').forEach(b => b.classList.toggle('is-active', b.dataset.id === id));
  renderDetail();
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

  $('#resetBtn').addEventListener('click', () => {
    state.status = 'all'; state.cause = 'all'; state.year = null;
    $$('.filter').forEach(b => b.classList.toggle('is-on', b.dataset.val === 'all'));
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

  renderBase();
  renderMarkers();
  renderStats();
  bindFilters();

  /* 預設選最近一筆監測中的紀錄，沒有就選第一筆 */
  const first = LAKES.find(l => l.statusKey === 'watch') || LAKES[0];
  state.selected = first.id;

  refresh();
  renderDetail();
}

document.addEventListener('DOMContentLoaded', init);
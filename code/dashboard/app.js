/* ════════════════════════════════════════
   堰塞湖監測儀表板 · 邏輯
   資料為示範值，替換 LAKES 即可接上真實管線輸出
   ════════════════════════════════════════ */

'use strict';

/* ── 1. 資料 ───────────────────────────── */

const LAKES = [
  {
    id: 'mataian',
    name: '馬太鞍溪堰塞湖',
    where: '花蓮縣光復鄉',
    lon: 121.3945, lat: 23.6812,
    grade: 'A',
    confirm: 'SAR + 光學雙重確認',
    volume: 1075, volumeRange: '910–1,240',
    floorEl: 640.0, crestEl: 682.0, waterEl: 675.6,
    rise: 1.8, fillHours: 38,
    people: 2847, roads: 4, isolated: 2,
    shelter: '光復國中（淹沒範圍外 1.4 km）'
  },
  {
    id: 'zhuoshui',
    name: '濁水溪上游堰塞湖',
    where: '南投縣仁愛鄉',
    lon: 121.0500, lat: 23.8500,
    grade: 'A',
    confirm: 'SAR + 光學雙重確認',
    volume: 684, volumeRange: '590–790',
    floorEl: 1180.0, crestEl: 1216.0, waterEl: 1202.4,
    rise: 2.4, fillHours: 61,
    people: 1132, roads: 3, isolated: 1,
    shelter: '親愛國小（淹沒範圍外 2.1 km）'
  },
  {
    id: 'liwu',
    name: '立霧溪上游堰塞湖',
    where: '花蓮縣秀林鄉',
    lon: 121.4500, lat: 24.1800,
    grade: 'B',
    confirm: '僅 SAR，多時相確認',
    volume: 458, volumeRange: '390–530',
    floorEl: 892.0, crestEl: 934.0, waterEl: 906.5,
    rise: 0.9, fillHours: 142,
    people: 640, roads: 2, isolated: 1,
    shelter: '富世國小（淹沒範圍外 3.6 km）'
  },
  {
    id: 'laonong',
    name: '荖濃溪支流堰塞湖',
    where: '高雄市桃源區',
    lon: 120.7900, lat: 23.1800,
    grade: 'B',
    confirm: '僅 SAR，多時相確認',
    volume: 312, volumeRange: '265–360',
    floorEl: 764.0, crestEl: 812.0, waterEl: 792.8,
    rise: 0.6, fillHours: 196,
    people: 486, roads: 2, isolated: 0,
    shelter: '桃源國中（淹沒範圍外 4.2 km）'
  },
  {
    id: 'dajia',
    name: '大甲溪上游待觀察水體',
    where: '臺中市和平區',
    lon: 121.2000, lat: 24.2500,
    grade: 'C',
    confirm: '單景偵測，待下次過境確認',
    volume: null, volumeRange: '—',
    floorEl: null, crestEl: null, waterEl: null,
    rise: null, fillHours: null,
    people: null, roads: null, isolated: null,
    shelter: null
  },
  {
    id: 'beinan',
    name: '卑南溪支流待觀察水體',
    where: '臺東縣延平鄉',
    lon: 121.0200, lat: 22.9500,
    grade: 'C',
    confirm: '單景偵測，待下次過境確認',
    volume: null, volumeRange: '—',
    floorEl: null, crestEl: null, waterEl: null,
    rise: null, fillHours: null,
    people: null, roads: null, isolated: null,
    shelter: null
  }
];

const ALERTS = [
  { time: '07/23 07:05', tag: '升級', cls: 'up',
    text: '馬太鞍溪由 B 級升為 A 級：光學影像確認水體範圍，距壩頂縮短至 6.4 m' },
  { time: '07/23 06:40', tag: 'CAP', cls: 'cap',
    text: '已發布示警至災害示警平台，涵蓋光復鄉 3 村里' },
  { time: '07/22 22:18', tag: '升級', cls: 'up',
    text: '濁水溪上游水位 24 小時內上升 2.4 m，維持 A 級並加密監測' },
  { time: '07/22 18:12', tag: '新增', cls: '',
    text: '大甲溪上游偵測到新增水體，列為 C 級待觀察' },
  { time: '07/21 06:08', tag: '更新', cls: '',
    text: '荖濃溪支流蓄水量更新為 312 萬 m³，維持 B 級' }
];


/* ── 2. 投影：經緯度 → 地圖座標 ─────────── */

const BOUNDS = { west: 119.95, east: 122.05, south: 21.85, north: 25.35 };
const MAP = { w: 460, h: 780 };

function project(lon, lat) {
  return {
    x: (lon - BOUNDS.west) / (BOUNDS.east - BOUNDS.west) * MAP.w,
    y: (BOUNDS.north - lat) / (BOUNDS.north - BOUNDS.south) * MAP.h
  };
}

/* 海岸線與中央山脈（簡化輪廓，順時針自北端起） */
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
  return coords
    .map(([lon, lat], i) => {
      const p = project(lon, lat);
      return `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
    })
    .join(' ') + ' Z';
}


/* ── 3. 剖面圖幾何 ─────────────────────── */

/* 剖面 SVG 中的固定地形節點（見 index.html） */
const SEC = {
  leftTop:  { x: 0,   y: 60  },
  basinL:   { x: 250, y: 235 },
  basinR:   { x: 470, y: 228 },
  crest:    { x: 575, y: 95  },
  floorY: 235,
  crestY: 95
};

/* 依水位比例回傳水面 y 座標 */
function waterY(ratio) {
  const r = Math.max(0, Math.min(1, ratio));
  return SEC.floorY - r * (SEC.floorY - SEC.crestY);
}

/* 水面 y → 左岸交點 x（沿左側坡面） */
function leftBankX(wy) {
  const { leftTop, basinL } = SEC;
  const t = (wy - leftTop.y) / (basinL.y - leftTop.y);
  return leftTop.x + t * (basinL.x - leftTop.x);
}

/* 水面 y → 壩體上游面交點 x */
function damFaceX(wy) {
  const { basinR, crest } = SEC;
  const t = (basinR.y - wy) / (basinR.y - crest.y);
  return basinR.x + t * (crest.x - basinR.x);
}


/* ── 4. 渲染 ───────────────────────────── */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { selected: LAKES[0].id, filter: 'all' };

/* 底圖 */
function renderBase() {
  const g = $('#mapBase');
  g.innerHTML = `
    <path class="coast" d="${toPath(COASTLINE)}"/>
    <path class="ridge" d="${toPath(RIDGE)}"/>
  `;
}

/* 標記 */
function renderMarkers() {
  const g = $('#mapMarkers');
  const maxVol = Math.max(...LAKES.map(l => l.volume || 0));

  g.innerHTML = LAKES.map(lake => {
    const p = project(lake.lon, lake.lat);
    const r = lake.volume ? 5 + (lake.volume / maxVol) * 8 : 4;
    return `
      <g class="marker g-${lake.grade}" data-id="${lake.id}"
         tabindex="0" role="button"
         aria-label="${lake.name}，${lake.grade} 級">
        <circle class="halo" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${(r + 7).toFixed(1)}"/>
        <circle class="dot"  cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r.toFixed(1)}"/>
        <text x="${(p.x + r + 12).toFixed(1)}" y="${(p.y + 4).toFixed(1)}">${lake.name}</text>
      </g>`;
  }).join('');

  $$('.marker', g).forEach(el => {
    const pick = () => select(el.dataset.id);
    el.addEventListener('click', pick);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
    });
  });
}

/* 清單 */
function renderList() {
  const visible = LAKES.filter(l => state.filter === 'all' || l.grade === state.filter);
  const maxVol = Math.max(...LAKES.map(l => l.volume || 1));

  $('#lakeList').innerHTML = visible.map(lake => {
    const pct = v => v ? Math.round(v * 100) : 12;
    const volPct  = pct(lake.volume ? lake.volume / maxVol : 0);
    const gapVal  = lake.crestEl ? (lake.crestEl - lake.waterEl).toFixed(1) : '—';
    const gapPct  = lake.crestEl
      ? Math.round((1 - (lake.crestEl - lake.waterEl) / (lake.crestEl - lake.floorEl)) * 100)
      : 12;
    const peoplePct = lake.people ? Math.round(lake.people / 3000 * 100) : 12;

    return `
      <button class="lake ${lake.id === state.selected ? 'is-active' : ''}" data-id="${lake.id}">
        <span class="tier g-${lake.grade}">${lake.grade}</span>
        <span>
          <span class="name">${lake.name}</span>
          <span class="meta">${lake.where} · ${lake.confirm}</span>
        </span>
        <span class="kv">
          <span class="label">蓄水量</span>
          <span class="num">${lake.volume ? lake.volume.toLocaleString() : '—'}</span>
          <span class="bar"><i class="g-${lake.grade}" style="width:${volPct}%"></i></span>
        </span>
        <span class="kv">
          <span class="label">距壩頂</span>
          <span class="num">${gapVal}</span>
          <span class="bar"><i class="g-${lake.grade}" style="width:${gapPct}%"></i></span>
        </span>
        <span class="kv">
          <span class="label">暴露人口</span>
          <span class="num">${lake.people ? lake.people.toLocaleString() : '—'}</span>
          <span class="bar"><i class="g-${lake.grade}" style="width:${peoplePct}%"></i></span>
        </span>
      </button>`;
  }).join('');

  $$('#lakeList .lake').forEach(el =>
    el.addEventListener('click', () => select(el.dataset.id))
  );

  $('#listEmpty').hidden = visible.length > 0;
  $('[data-bind="listCount"]').textContent =
    `${visible.length} 處` + (state.filter === 'all' ? ' · 依風險排序' : ` · ${state.filter} 級`);
}

/* 示警紀錄 */
function renderLog() {
  $('#logList').innerHTML = ALERTS.map(a => `
    <div class="log-row">
      <time>${a.time}</time>
      <span class="tag ${a.cls}">${a.tag}</span>
      <span>${a.text}</span>
    </div>`).join('');
}

/* 詳情面板 */
function renderDetail() {
  const lake = LAKES.find(l => l.id === state.selected);
  const set = (key, val) => {
    const el = $(`[data-bind="${key}"]`);
    if (el) el.textContent = val;
  };

  const gradeEl = $('[data-bind="grade"]');
  gradeEl.className = `grade g-${lake.grade}`;
  gradeEl.textContent = `${lake.grade} 級 · ${lake.confirm}`;

  set('name', lake.name);
  set('where', `${lake.where} · ${lake.lat.toFixed(4)}°N ${lake.lon.toFixed(4)}°E`);

  const known = lake.crestEl !== null;
  const gap = known ? (lake.crestEl - lake.waterEl) : null;

  set('volume', lake.volumeRange);
  set('volumeNote', known
    ? '以最近一景影像水面範圍反演，含地形不確定性區間'
    : '待下次過境取得第二景影像後估算');
  set('gap', known ? gap.toFixed(1) : '—');
  set('gapNote', known ? `較前次過境上升 ${lake.rise} m` : '尚未確認壩頂高程');
  set('fill', known ? lake.fillHours : '—');
  set('fillNote', known ? '依上游 24 小時累積雨量推估入流' : '—');

  set('people', lake.people ? lake.people.toLocaleString() : '—');
  set('roads', lake.roads !== null ? lake.roads : '—');
  set('isolated', lake.isolated !== null ? lake.isolated : '—');
  set('impactNote', known
    ? `採 DEM 填洼快估，假設瞬時全潰。實際洪峰沿程衰減未計入，屬保守上限。最近避難收容處所：${lake.shelter}`
    : '此處尚未確認為堰塞湖，暫不進行淹沒模擬。下次過境若水體仍存在，將自動升級並產出評估。');

  drawSection(lake, known, gap);
}

/* 剖面圖 */
function drawSection(lake, known, gap) {
  const ratio = known
    ? (lake.waterEl - lake.floorEl) / (lake.crestEl - lake.floorEl)
    : 0.18;
  const wy = waterY(ratio);
  const lx = leftBankX(wy);
  const rx = damFaceX(wy);

  $('#waterBody').setAttribute('d',
    `M${lx.toFixed(1)} ${wy.toFixed(1)} L${rx.toFixed(1)} ${wy.toFixed(1)} ` +
    `L${SEC.basinR.x} ${SEC.basinR.y} L${SEC.basinL.x} ${SEC.basinL.y} Z`);

  const line = $('#waterLine');
  line.setAttribute('x1', lx.toFixed(1)); line.setAttribute('y1', wy.toFixed(1));
  line.setAttribute('x2', rx.toFixed(1)); line.setAttribute('y2', wy.toFixed(1));

  $('#crestLabel').textContent = known ? `壩頂 EL. ${lake.crestEl.toFixed(1)} m` : '壩頂高程未定';
  const levelLabel = $('#levelLabel');
  levelLabel.textContent = known ? `水位 EL. ${lake.waterEl.toFixed(1)} m` : '水位待確認';
  levelLabel.setAttribute('y', (wy - 4).toFixed(1));

  const caliper = $('#caliper');
  const gapLabel = $('#gapLabel');
  if (known) {
    caliper.style.display = '';
    gapLabel.style.display = '';
    $('#calLine').setAttribute('y2', wy.toFixed(1));
    $('#calFoot').setAttribute('y1', wy.toFixed(1));
    $('#calFoot').setAttribute('y2', wy.toFixed(1));
    gapLabel.setAttribute('y', ((SEC.crestY + wy) / 2 + 5).toFixed(1));
    gapLabel.textContent = `${gap.toFixed(1)} m`;
  } else {
    caliper.style.display = 'none';
    gapLabel.style.display = 'none';
  }
}

/* 狀態列統計 */
function renderStats() {
  $('[data-bind="countAll"]').textContent = `${LAKES.length} 處`;
  $('[data-bind="countA"]').textContent = `${LAKES.filter(l => l.grade === 'A').length} 處`;
}


/* ── 5. 互動 ───────────────────────────── */

function select(id) {
  state.selected = id;
  $$('.marker').forEach(m => m.classList.toggle('is-active', m.dataset.id === id));
  $$('#lakeList .lake').forEach(b => b.classList.toggle('is-active', b.dataset.id === id));
  renderDetail();
}

function applyFilter(f) {
  state.filter = f;
  $$('.filter').forEach(b => b.classList.toggle('is-on', b.dataset.filter === f));
  $$('.marker').forEach(m => {
    const lake = LAKES.find(l => l.id === m.dataset.id);
    m.classList.toggle('is-dim', f !== 'all' && lake.grade !== f);
  });
  renderList();
}


/* ── 6. 啟動 ───────────────────────────── */

function init() {
  renderBase();
  renderMarkers();
  renderList();
  renderLog();
  renderStats();
  select(state.selected);

  $$('.filter').forEach(btn =>
    btn.addEventListener('click', () => applyFilter(btn.dataset.filter))
  );
}

document.addEventListener('DOMContentLoaded', init);

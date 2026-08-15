/* ════════════════════════════════════════
   map3d.js — 3D 立體台灣地形地圖

   讀取 data/terrain.js（window.TAIWAN_TERRAIN，來源為 DEM tif 縮小取樣後
   的高程網格）建立立體地形網格；湖泊標記依照與地形相同的經緯度→世界座標
   換算方式擺放，貼齊地表高度。

   互動：
     · 拖曳（滑鼠/觸控）→ 環繞旋轉（改變方位角 theta / 仰角 phi）
     · 滾輪 / 雙指縮放 → 改變攝影機與地形的距離（radius）
     · 點擊標記 → 觸發 onSelect(id)
     · 滑入/移出標記 → 觸發 onHoverStart(id) / onHoverEnd(id)

   對外 API（window.Map3D）：
     Map3D.init(canvasEl, labelsEl, handlers)
     Map3D.setLakes(lakes)                 // 建立所有標記（僅需呼叫一次）
     Map3D.sync({ selectedId, visibleIds, highRiskIds, capArea })
     Map3D.hoverMarker(id) / Map3D.unhoverMarker(id)
     Map3D.resize()

   地形形狀完全來自 terrain.js 的網格資料，標記位置完全來自既有清冊的
   lon/lat；本檔本身不做「新的地理判斷」。CAP 示警範圍是唯一的例外：
   有 pipeline.assess.run 算出的淹沒多邊形時會畫出該形狀（見
   setCapArea/applyCapAreaVisibility），沒有時才退回固定半徑示意圈。
   ════════════════════════════════════════ */

'use strict';

const Map3D = (() => {

  // ── 色票（對齊 styles.css 的設計變數）───────────
  const COL = {
    coastDark: 0x18211C,   // 對齊 --stone 系，海岸/低窪過渡色
    lowland:   0x3F5C4C,   // 低地：偏暗的苔綠
    mid:       0x7CA48A,   // 中海拔：--moss
    high:      0xC98A2E,   // 高海拔：--ochre
    peak:      0xE9F1EB,   // 高峰：--snow
    watch:     0xC2503C,   // --clay
    stable:    0xC98A2E,   // --ochre
    gone:      0x7CA48A,   // --moss
    jade:      0x3FA9A0,   // --jade（hover/select 光環）
    alert:     0xF5D547,   // 高風險警示環專用亮黃色，刻意跟監測中的紅點區隔開
    locate:    0x38E1FF,   // 垂直定位線專用亮青色，純粹是「這裡有東西」的視覺提示，跟狀態色無關
    trigger:   0xC98A2E,   // 觸發任務 AOI 專用色（= --ochre），跟 CAP 範圍的 jade 區隔開，
                           // 因為兩者可能同時畫在地圖上（觸發任務跟被選取的湖是不同概念）
  };

  let renderer, scene, camera, canvas, labelsEl, wrapEl;
  let terrainMesh, groundPlate;
  let markerGroup;
  let markers = new Map(); // id -> { group, dot, selectRing, capRing, label, baseY, r }
  let handlers = {};
  let TERRAIN = null;

  let WORLD = { width: 12, depth: 20 };
  let HEIGHT_SCALE = 1;

  // ── 攝影機環繞控制狀態 ─────────────────────
  const view = {
    theta: -0.35,           // 方位角（水平旋轉）
    phi: 0.78,               // 仰角（0=正上方往下看，越大越貼近水平）
    radius: 0,               // 於 init 時依 WORLD 尺寸設定
    target: { x: 0, y: 0, z: 0 },
  };
  const PHI_MIN = 0.18, PHI_MAX = 1.48;
  let RADIUS_MIN = 6, RADIUS_MAX = 48;

  let dragging = false;
  let dragMode = 'rotate'; // 'rotate' | 'pan'
  let lastX = 0, lastY = 0, downX = 0, downY = 0;
  let hoveredId = null;
  let currentSelectedId = null; // 供閒置自動旋轉判斷「目前該對準哪個湖」
  let idleTimer = null;
  let isIdleRotating = false;
  const IDLE_MS = 30000;          // 靜置多久後開始自動旋轉
  const IDLE_ROTATE_SPEED = 0.105; // 弧度/秒，約 60 秒繞一圈
  let clock = 0;

  /* 圖層開關狀態（對應任務六的圖層控制 UI）。
     points/highRisk 直接套用在既有標記上；capArea 是「選取事件時才畫出
     的 CAP 示警範圍圈／淹沒多邊形」，跟每個標記常駐的 highRisk 環是兩件事；
     labels 開啟時強制顯示全部點位名稱，off 時維持原本只在 hover/選取時顯示。 */
  let layers = { points: true, highRisk: true, capArea: true, labels: false, triggerAreas: true };
  let capAreaRing = null;      // 沒有淹沒多邊形時的固定半徑示意圈（circle 版）
  let capPolygonMesh = null;   // 有淹沒多邊形時的實際形狀（polygon 版，取代上面那個圈）
  let capPolygonBorder = null;
  let lastCapArea = null; // { lakeId, radiusKm, polygon } | null；polygon 為 [[lon,lat],...] 或 null

  let triggerTasksData = [];   // pipeline.trigger.service 產生的 window.TRIGGER_TASKS（見 setTriggerAreas）
  let triggerAreaGroups = [];  // 目前畫在場景裡的 AOI 圈（跟 CAP 範圍圈不同：可以同時畫很多個，
                                // 不是只有「目前選取的湖」才有一個）
  let currentSelectedTriggerId = null;

  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function lerpColor(c1, c2, t) {
    const r1 = (c1 >> 16) & 255, g1 = (c1 >> 8) & 255, b1 = c1 & 255;
    const r2 = (c2 >> 16) & 255, g2 = (c2 >> 8) & 255, b2 = c2 & 255;
    return new THREE.Color(
      lerp(r1, r2, t) / 255, lerp(g1, g2, t) / 255, lerp(b1, b2, t) / 255
    );
  }

  /* 地形高度顏色：低→高分三段漸層，靠海（land 占比低）的網格往海岸暗色過渡，
     藉此讓縮小取樣後的海岸線看起來平滑，而不是方塊鋸齒狀邊界。 */
  function elevationColor(elev, landFrac) {
    const t = clamp(elev / (TERRAIN.maxElevation || 1), 0, 1);
    let base;
    if (t < 0.35) base = lerpColor(COL.lowland, COL.mid, t / 0.35);
    else if (t < 0.75) base = lerpColor(COL.mid, COL.high, (t - 0.35) / 0.4);
    else base = lerpColor(COL.high, COL.peak, (t - 0.75) / 0.25);

    if (landFrac < 0.7) {
      const coast = new THREE.Color(COL.coastDark);
      base = coast.lerp(base, landFrac / 0.7);
    }
    return base;
  }

  /* 網格座標（列 r／欄 c）→世界座標 XZ 平面；與經緯度為線性對應，
     因此標記（lon/lat）與地形（r/c）共用同一套換算，不會對不齊。 */
  function gridToWorld(r, c) {
    const fx = c / (TERRAIN.cols - 1);
    const fz = r / (TERRAIN.rows - 1);
    return { x: (fx - 0.5) * WORLD.width, z: (fz - 0.5) * WORLD.depth };
  }

  function lonLatToWorld(lon, lat) {
    const fx = (lon - TERRAIN.bounds.west) / (TERRAIN.bounds.east - TERRAIN.bounds.west);
    const fz = (TERRAIN.bounds.north - lat) / (TERRAIN.bounds.north - TERRAIN.bounds.south);
    return { x: (fx - 0.5) * WORLD.width, z: (fz - 0.5) * WORLD.depth };
  }

  /* 公里 → 世界單位，僅供「CAP 示警範圍」示意圈使用（3km 半徑本身就是
     cap.js 裡的示意值，不是精確淹沒模擬），用緯度方向的世界縮放換算，
     1 緯度約 111.32 公里，避免額外處理經度隨緯度變化的 cos 修正。 */
  function kmToWorldUnits(km) {
    const worldUnitsPerDegreeLat = WORLD.depth / (TERRAIN.bounds.north - TERRAIN.bounds.south);
    const kmPerDegreeLat = 111.32;
    return (km / kmPerDegreeLat) * worldUnitsPerDegreeLat;
  }

  function sampleElevation(lon, lat) {
    const cols = TERRAIN.cols, rows = TERRAIN.rows;
    const fx = (lon - TERRAIN.bounds.west) / (TERRAIN.bounds.east - TERRAIN.bounds.west) * (cols - 1);
    const fy = (TERRAIN.bounds.north - lat) / (TERRAIN.bounds.north - TERRAIN.bounds.south) * (rows - 1);
    const cx = clamp(Math.round(fx), 0, cols - 1);
    const cy = clamp(Math.round(fy), 0, rows - 1);
    const idx = cy * cols + cx;
    const e = TERRAIN.elevation[idx];
    return e > -9999 ? e : 0;
  }

  /* ── 建立地形網格 ─────────────────────────
     只有「至少一角有陸地」的方格才畫出三角面，完全是海的方格整片跳過——
     這樣立體地形本身的輪廓就是台灣海岸線，不需要另外準備向量圖形。
     採用不共用頂點（每個三角形自帶三個頂點）以取得平面切割（faceted）
     的低多邊形地形質感，並用 computeVertexNormals 取得逐面法線。 */
  function buildTerrain() {
    const cols = TERRAIN.cols, rows = TERRAIN.rows;
    const positions = [];
    const colors = [];

    function vAt(r, c) {
      const idx = r * cols + c;
      const landFrac = TERRAIN.landFrac[idx] / 100;
      const rawElev = TERRAIN.elevation[idx];
      const elev = rawElev > -9999 ? rawElev : 0;
      const { x, z } = gridToWorld(r, c);
      const y = elev * HEIGHT_SCALE * landFrac; // 海岸往 0 平滑過渡
      const col = elevationColor(elev, landFrac);
      return { x, y, z, col };
    }

    for (let r = 0; r < rows - 1; r++) {
      for (let c = 0; c < cols - 1; c++) {
        const i00 = r * cols + c, i10 = r * cols + c + 1;
        const i01 = (r + 1) * cols + c, i11 = (r + 1) * cols + c + 1;
        const l00 = TERRAIN.landFrac[i00], l10 = TERRAIN.landFrac[i10];
        const l01 = TERRAIN.landFrac[i01], l11 = TERRAIN.landFrac[i11];
        if (l00 === 0 && l10 === 0 && l01 === 0 && l11 === 0) continue;

        const v00 = vAt(r, c), v10 = vAt(r, c + 1);
        const v01 = vAt(r + 1, c), v11 = vAt(r + 1, c + 1);

        // 三角形 1: 00,01,10  三角形 2: 10,01,11（法線朝上，逆時鐘為正面）
        [[v00, v01, v10], [v10, v01, v11]].forEach(tri => {
          tri.forEach(v => {
            positions.push(v.x, v.y, v.z);
            colors.push(v.col.r, v.col.g, v.col.b);
          });
        });
      }
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true, flatShading: true, roughness: 0.92, metalness: 0.02,
    });
    return new THREE.Mesh(geo, mat);
  }

  /* 地形下方一片暗色底盤，純粹視覺上把浮空的立體地形「壓住」，
     不代表任何地理資料。 */
  function buildGroundPlate() {
    const geo = new THREE.PlaneGeometry(WORLD.width * 1.5, WORLD.depth * 1.5, 1, 1);
    geo.rotateX(-Math.PI / 2);
    const mat = new THREE.MeshBasicMaterial({
      color: 0x0F1613, transparent: true, opacity: 0.35, depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.y = -0.06;
    return mesh;
  }

  /* ── 標記 ─────────────────────────────── */

  function markerRadius(vol, maxVol) {
    const base = 0.045;
    if (!vol) return base;
    return base + Math.sqrt(vol / maxVol) * 0.11;
  }

  function buildMarker(lake, maxVol) {
    const { x, z } = lonLatToWorld(lake.lon, lake.lat);
    const baseY = sampleElevation(lake.lon, lake.lat) * HEIGHT_SCALE;
    const r = markerRadius(lake.volume, maxVol);
    const isWatch = lake.statusKey === 'watch';
    const color = isWatch ? COL.watch
      : lake.statusKey === 'stable' ? COL.stable : COL.gone;

    // 監測中（紅點）刻意放大，讓它在清冊眾多標記裡第一眼就跳出來，
    // 不必依賴顏色辨識或穿模示警才能發現。
    const dotR = isWatch ? r * 1.6 : r;

    const group = new THREE.Group();
    group.position.set(x, baseY, z);
    group.userData.id = lake.id;

    const dotGeo = new THREE.SphereGeometry(dotR, 16, 14);
    const dotMat = new THREE.MeshStandardMaterial({
      color, roughness: 0.5, metalness: 0.1,
      emissive: isWatch ? COL.watch : 0x000000,
      emissiveIntensity: isWatch ? 0.45 : 0,
    });
    const dot = new THREE.Mesh(dotGeo, dotMat);
    dot.position.y = dotR * 0.7;
    dot.userData.id = lake.id;
    group.add(dot);

    // 選取/預覽光環（貼地平放）
    const ringGeo = new THREE.RingGeometry(r * 1.6, r * 2.1, 28);
    const ringMat = new THREE.MeshBasicMaterial({
      color: COL.jade, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false,
    });
    const selectRing = new THREE.Mesh(ringGeo, ringMat);
    selectRing.rotation.x = -Math.PI / 2;
    selectRing.position.y = 0.01;
    group.add(selectRing);

    // CAP 高風險示警環（旋轉、脈動）：刻意加寬（原本只有 0.3r 寬，太細看不出來）
    // 並改用亮黃色，跟監測中紅點本體的顏色區隔開，避免兩者融在一起難以分辨。
    const capGeo = new THREE.RingGeometry(r * 2.1, r * 3.1, 28);
    const capMat = new THREE.MeshBasicMaterial({
      color: COL.alert, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false,
    });
    const capRing = new THREE.Mesh(capGeo, capMat);
    capRing.rotation.x = -Math.PI / 2;
    capRing.position.y = 0.012;
    capRing.visible = false;
    group.add(capRing);

    // 「透視示警」替身：任何標記被地形擋住時，這顆關閉深度測試的替身會
    // 穿透山體持續顯示，讓使用者不必刻意轉到縫隙那側也能察覺該處有標記。
    // 原本只有監測中（紅點）湖泊才有這個機制，現在擴大到全部標記——
    // 已穩定/已消失的小標記一樣容易被複雜地形擋住、找不到。
    // 替身顏色沿用該標記本身的存續狀態顏色，維持跟圖例一致的視覺語意。
    const ghostGeo = new THREE.SphereGeometry(Math.max(dotR * 0.85, 0.012), 10, 8);
    const ghostMat = new THREE.MeshBasicMaterial({
      color, transparent: true, opacity: 0,
      depthTest: false, depthWrite: false,
    });
    const ghostDot = new THREE.Mesh(ghostGeo, ghostMat);
    ghostDot.position.y = dotR * 0.7;
    ghostDot.renderOrder = 999;
    ghostDot.visible = false;
    group.add(ghostDot);

    // 垂直定位線：從替身往上拉出一條線，即使替身本身很小，
    // 在複雜地形的側面輪廓上也還是能靠這條線注意到「這裡有標記」。
    // 用鮮豔青色而不是存續狀態色——這條線純粹是「這裡有東西」的視覺
    // 提示，跟狀態沒有關係，用鮮豔色才能在各種地形背景上都夠明顯。
    // 同樣關閉深度測試，才不會又被地形擋住。
    const pinHeight = Math.max(dotR * 6, 0.16);
    const pinGeo = new THREE.CylinderGeometry(0.014, 0.014, pinHeight, 8);
    const pinMat = new THREE.MeshBasicMaterial({
      color: COL.locate, transparent: true, opacity: 0.85,
      depthTest: false, depthWrite: false,
    });
    const pinLine = new THREE.Mesh(pinGeo, pinMat);
    pinLine.position.y = dotR * 0.7 + pinHeight / 2;
    pinLine.renderOrder = 998;
    pinLine.visible = false;
    group.add(pinLine);

    // 定位線頂端加一顆小球當「頭」，比一條純線更容易被注意到
    const pinHeadGeo = new THREE.SphereGeometry(0.026, 10, 8);
    const pinHeadMat = new THREE.MeshBasicMaterial({
      color: COL.locate, transparent: true, opacity: 0.95,
      depthTest: false, depthWrite: false,
    });
    const pinHead = new THREE.Mesh(pinHeadGeo, pinHeadMat);
    pinHead.position.y = dotR * 0.7 + pinHeight;
    pinHead.renderOrder = 998;
    pinHead.visible = false;
    group.add(pinHead);

    markerGroup.add(group);

    // 名稱標籤（HTML 覆蓋層，跟著投影座標更新位置）
    const label = document.createElement('span');
    label.className = 'map3d-label';
    label.textContent = lake.name;
    labelsEl.appendChild(label);

    return { group, dot, selectRing, capRing, ghostDot, pinLine, pinHead, label, baseY, r, dotR, lake, _ghostEligible: true };
  }

  function setMarkerHoverVisual(m, on) {
    m.selectRing.material.opacity = (on || m._active) ? 0.9 : 0;
    m.label.classList.toggle('is-visible', layers.points && (on || m._active || layers.labels));
  }

  function setLakes(lakes) {
    markers.forEach(m => { labelsEl.removeChild(m.label); markerGroup.remove(m.group); });
    markers = new Map();
    const maxVol = Math.max(...lakes.map(l => l.volume || 0)) || 1;
    lakes.forEach(lake => {
      if (lake.lon == null || lake.lat == null) return;
      markers.set(lake.id, buildMarker(lake, maxVol));
    });
  }

  /* ── CAP 示警範圍（任務六 + 淹沒多邊形接入）──────────
     兩種畫法共用同一組視覺語意（比照氣象警報圖：半透明實心色塊 + 較粗外框）：
       · 有淹沒多邊形（pipeline.assess.run 算出來的）時，畫多邊形本身的形狀。
         這是「真的」形狀計算結果，但若其 method 為 synthetic_demo_dem，
         代表地形是合成示範資料——UI 端（app.js renderEvidenceCard）
         另外用文字揭露這件事，3D 地圖本身不重複畫警語。
       · 沒有多邊形（湖泊不是 watch、或沒跑 assess）時，退回舊版固定半徑
         示意圈，純粹表示「這裡有 CAP 示警」，不代表任何地理範圍判斷。 */
  function buildCapAreaRing() {
    const group = new THREE.Group();

    const fillGeo = new THREE.CircleGeometry(1, 64);
    const fill = new THREE.Mesh(fillGeo, capFillMat());

    const borderGeo = new THREE.RingGeometry(0.9, 1, 64);
    const border = new THREE.Mesh(borderGeo, capBorderMat());
    // 群組本身會整體旋轉 -90°(貼地)，這裡在旋轉前沿本地 Z 軸墊高，
    // 旋轉後才會變成世界座標的「垂直方向微幅抬升」，避免跟 fill 共平面 z-fighting。
    border.position.z = 0.002;

    group.add(fill, border);
    group.rotation.x = -Math.PI / 2;
    group.visible = false;
    scene.add(group);
    return group;
  }

  /* 圓圈用的材質是共用的一個 group（先旋轉再縮放平移即可）；多邊形每次
     選取的湖不同、形狀不同，沒辦法比照圓圈只縮放，所以材質抽出來共用
     實例，幾何體則每次重建。 */
  let _capFillMat = null, _capBorderMat = null;
  function capFillMat() {
    if (!_capFillMat) {
      _capFillMat = new THREE.MeshBasicMaterial({
        color: COL.jade, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false,
      });
    }
    return _capFillMat;
  }
  function capBorderMat() {
    if (!_capBorderMat) {
      _capBorderMat = new THREE.MeshBasicMaterial({
        color: COL.jade, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false,
      });
    }
    return _capBorderMat;
  }

  /* 多邊形頂點（[lon,lat] 陣列）→ 世界座標三角化網格（扇形三角化，
     以形心為共同頂點）。淹沒範圍的邊界（來自 skimage.find_contours
     再抽稀）通常接近星狀（相對形心大致單調），扇形三角化在這個前提下
     足夠正確；不是通用多邊形三角化演算法，複雜的凹多邊形可能會畫錯，
     但比固定圓圈更接近真實形狀已經是這次要達成的目標。 */
  function polygonWorldPoints(lonLatPoints, y) {
    return lonLatPoints.map(([lon, lat]) => {
      const { x, z } = lonLatToWorld(lon, lat);
      return new THREE.Vector3(x, y, z);
    });
  }

  function buildPolygonFillGeometry(points3d) {
    const centroid = points3d.reduce((acc, p) => acc.add(p.clone()), new THREE.Vector3())
      .multiplyScalar(1 / points3d.length);
    const positions = [];
    for (let i = 0; i < points3d.length; i++) {
      const a = points3d[i], b = points3d[(i + 1) % points3d.length];
      positions.push(centroid.x, centroid.y, centroid.z, a.x, a.y, a.z, b.x, b.y, b.z);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.computeVertexNormals();
    return geo;
  }

  function buildPolygonBorderGeometry(points3d) {
    const positions = [];
    points3d.forEach(p => positions.push(p.x, p.y, p.z));
    positions.push(points3d[0].x, points3d[0].y, points3d[0].z); // 閉合
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    return geo;
  }

  function clearCapPolygon() {
    if (capPolygonMesh) { scene.remove(capPolygonMesh); capPolygonMesh.geometry.dispose(); capPolygonMesh = null; }
    if (capPolygonBorder) { scene.remove(capPolygonBorder); capPolygonBorder.geometry.dispose(); capPolygonBorder = null; }
  }

  function applyCapAreaVisibility() {
    if (!capAreaRing) return;

    if (!lastCapArea || !layers.capArea) {
      capAreaRing.visible = false;
      clearCapPolygon();
      return;
    }
    const m = markers.get(lastCapArea.lakeId);
    if (!m) {
      capAreaRing.visible = false;
      clearCapPolygon();
      return;
    }

    if (lastCapArea.polygon && lastCapArea.polygon.length >= 3) {
      capAreaRing.visible = false;
      clearCapPolygon();
      const y = m.group.position.y + 0.02;
      const points3d = polygonWorldPoints(lastCapArea.polygon, y);
      capPolygonMesh = new THREE.Mesh(buildPolygonFillGeometry(points3d), capFillMat());
      capPolygonBorder = new THREE.LineLoop(buildPolygonBorderGeometry(points3d), capBorderMat());
      scene.add(capPolygonMesh, capPolygonBorder);
      return;
    }

    clearCapPolygon();
    if (!lastCapArea.radiusKm) { capAreaRing.visible = false; return; }
    const radiusWorld = Math.max(0.05, kmToWorldUnits(lastCapArea.radiusKm));
    capAreaRing.position.set(m.group.position.x, m.group.position.y + 0.02, m.group.position.z);
    capAreaRing.scale.set(radiusWorld, radiusWorld, 1);
    capAreaRing.visible = true;
  }

  /* lakeId=null 代表目前選取的事件沒有可示警的 CAP 範圍（例如尚無風險評估），
     這時候就不畫圈/多邊形，不要硬套一個沒有意義的範圍。
     polygon 有值時優先畫多邊形，radiusKm 只在沒有多邊形時當退回方案用。 */
  function setCapArea(lakeId, radiusKm, polygon) {
    lastCapArea = lakeId ? { lakeId, radiusKm: radiusKm || 3, polygon: polygon || null } : null;
    applyCapAreaVisibility();
  }

  /* ── 觸發任務 AOI（架構文件 §04，任務：讓前端讀 trigger_tasks.js）──
     跟上面的 CAP 範圍圈是不同的東西，畫法故意用不同顏色（COL.trigger）
     區隔：CAP 範圍是「選取某個湖時才畫、綁在該湖標記上」的單一範圍；
     觸發任務 AOI 是「不管有沒有選湖，只要圖層開著就全部畫出來」的
     一組獨立範圍，中心可能是震央或雨量站座標，不一定剛好落在某個
     堰塞湖標記上，所以不能沿用 CAP 範圍圈綁在 marker.group.position
     的做法，改成直接用經緯度＋地形取樣算世界座標。 */
  let _triggerFillMat = null, _triggerBorderMat = null;
  function triggerFillMat() {
    if (!_triggerFillMat) {
      _triggerFillMat = new THREE.MeshBasicMaterial({
        color: COL.trigger, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false,
      });
    }
    return _triggerFillMat;
  }
  function triggerBorderMat() {
    if (!_triggerBorderMat) {
      _triggerBorderMat = new THREE.MeshBasicMaterial({
        color: COL.trigger, transparent: true, opacity: 0.8, side: THREE.DoubleSide, depthWrite: false,
      });
    }
    return _triggerBorderMat;
  }

  function buildCircleAt(lat, lon, radiusKm) {
    const { x, z } = lonLatToWorld(lon, lat);
    const y = sampleElevation(lon, lat) * HEIGHT_SCALE + 0.03;
    const radiusWorld = Math.max(0.05, kmToWorldUnits(radiusKm || 3));

    const group = new THREE.Group();
    const fill = new THREE.Mesh(new THREE.CircleGeometry(1, 48), triggerFillMat());
    const border = new THREE.Mesh(new THREE.RingGeometry(0.92, 1, 48), triggerBorderMat());
    border.position.z = 0.002;
    group.add(fill, border);
    group.rotation.x = -Math.PI / 2;
    group.position.set(x, y, z);
    group.scale.set(radiusWorld, radiusWorld, 1);
    group.visible = false;
    scene.add(group);
    return group;
  }

  function clearTriggerAreaGroups() {
    triggerAreaGroups.forEach(g => {
      scene.remove(g);
      g.children.forEach(m => m.geometry && m.geometry.dispose());
    });
    triggerAreaGroups = [];
  }

  function applyTriggerAreaVisibility() {
    clearTriggerAreaGroups();
    if (!layers.triggerAreas) return;
    triggerTasksData.forEach(t => {
      if (t.centerLat == null || t.centerLon == null) return;
      const group = buildCircleAt(t.centerLat, t.centerLon, t.radiusKm);
      group.visible = true;
      // 目前選取的任務用較亮的外框強調，其餘任務仍畫出但淡一點，
      // 讓使用者一眼看出「這是我點的那個」而不是全部長得一樣。
      const isActive = t.taskId === currentSelectedTriggerId;
      group.children[1].material = group.children[1].material.clone();
      group.children[1].material.opacity = isActive ? 0.95 : 0.5;
      group.children[0].material = group.children[0].material.clone();
      group.children[0].material.opacity = isActive ? 0.22 : 0.10;
      triggerAreaGroups.push(group);
    });
  }

  /* tasks：[{ taskId, centerLat, centerLon, radiusKm }, ...]，通常直接把
     window.TRIGGER_TASKS 的 aoi 欄位攤平後傳進來（見 app.js）。
     沒有資料（陣列空）時就是不畫，不用另外判斷。 */
  function setTriggerAreas(tasks) {
    triggerTasksData = Array.isArray(tasks) ? tasks : [];
    applyTriggerAreaVisibility();
  }

  /* 高亮「目前選取的觸發任務」，null 代表沒有選取（全部用淡色畫）。 */
  function setActiveTriggerTask(taskId) {
    currentSelectedTriggerId = taskId || null;
    applyTriggerAreaVisibility();
  }

  /* ── 圖層開關（任務六）────────────────────
     points/highRisk 套用到既有標記，labels 強制顯示全部點位名稱，
     capArea 控制上面的示警範圍圈；全部立即套用一次，不需要重新 setLakes。 */
  function setLayers(partial) {
    layers = { ...layers, ...partial };
    markers.forEach((m, id) => {
      m.group.visible = layers.points;
      m.capRing.visible = layers.points && layers.highRisk && !!m._highRisk;
      const showLabel = m._active || id === hoveredId || layers.labels;
      m.label.classList.toggle('is-visible', layers.points && showLabel);
    });
    applyCapAreaVisibility();
    applyTriggerAreaVisibility();
  }


  /* capArea：{ lakeId, radiusKm, polygon } | null，代表目前選取事件是否要畫出
     CAP 示警範圍圈／淹沒多邊形；null 就是「沒有可用的 CAP 範圍」，不畫。 */
  function sync({ selectedId, visibleIds, highRiskIds, capArea }) {
    currentSelectedId = selectedId || null;
    markers.forEach((m, id) => {
      const visible = !visibleIds || visibleIds.has(id);
      const active = id === selectedId;
      m._active = active;
      m.group.visible = layers.points;
      const dim = !visible;
      m.dot.material.opacity = dim ? 0.12 : 1;
      m.dot.material.transparent = dim;
      m.dot.scale.setScalar(active ? 1.35 : 1);
      m.selectRing.material.opacity = active ? 0.9 : (id === hoveredId ? 0.9 : 0);
      const showLabel = layers.points && (active || id === hoveredId || layers.labels);
      m.label.classList.toggle('is-visible', showLabel);
      m.label.classList.toggle('is-dim', dim);
      const highRisk = !!(highRiskIds && highRiskIds.has(id));
      m._highRisk = highRisk;
      m.capRing.visible = layers.highRisk && highRisk;
      if (m.ghostDot) m._ghostEligible = visible;
    });
    setCapArea(capArea ? capArea.lakeId : null, capArea ? capArea.radiusKm : null,
               capArea ? capArea.polygon : null);
  }

  function hoverMarker(id) {
    if (!id || hoveredId === id) return;
    hoveredId = id;
    const m = markers.get(id);
    if (m) setMarkerHoverVisual(m, true);
  }
  function unhoverMarker(id) {
    if (!id) return;
    if (hoveredId === id) hoveredId = null;
    const m = markers.get(id);
    if (m) setMarkerHoverVisual(m, false);
  }

  /* ── 攝影機環繞控制 ────────────────────── */
  function updateCamera() {
    const t = view.target;
    camera.position.set(
      t.x + view.radius * Math.sin(view.phi) * Math.sin(view.theta),
      t.y + view.radius * Math.cos(view.phi),
      t.z + view.radius * Math.sin(view.phi) * Math.cos(view.theta)
    );
    camera.lookAt(t.x, t.y, t.z);
    // 平移(panCamera)需要用相機目前的世界方向算左右/前後向量，
    // lookAt 只更新 local matrix，這裡手動同步 matrixWorld 確保平移方向不會延遲一格。
    camera.updateMatrixWorld(true);
  }

  function resetView() {
    view.theta = -0.35;
    view.phi = 0.78;
    view.radius = WORLD.depth * 1.05;
    view.target.x = 0;
    view.target.z = 0;
    updateCamera();
  }

  /* ── 靜置自動旋轉 ────────────────────────
     30 秒內沒有點擊/拖曳/滾輪/按鍵等「有意義的互動」，就對準目前選取的
     湖泊（詳情面板顯示的那個）開始等速水平旋轉；一有互動立刻停止並
     重新倒數。單純滑鼠移動不算互動，避免 demo 展示時滑鼠稍微動一下
     旋轉就中斷。 */
  function enterIdleRotate() {
    if (currentSelectedId && markers.has(currentSelectedId)) {
      const m = markers.get(currentSelectedId);
      view.target.x = m.group.position.x;
      view.target.z = m.group.position.z;
    }
    isIdleRotating = true;
  }

  function resetIdleTimer() {
    isIdleRotating = false;
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(enterIdleRotate, IDLE_MS);
  }

  function bindIdleRotate() {
    ['pointerdown', 'wheel', 'keydown'].forEach(evt => {
      document.addEventListener(evt, resetIdleTimer, { passive: true });
    });
    resetIdleTimer();
  }

  /* 拖曳平移：把螢幕上的左右/上下位移，換算成沿著相機目前朝向的
     地面左右／前後向量，這樣不管旋轉到哪個角度，拖曳方向都跟畫面對齊。
     平移距離會跟著目前的縮放距離(radius)等比例縮放——拉近看細節時，
     拖一小段畫面不會整個跳掉；拉遠看全貌時，拖一小段畫面也能跨比較大的距離。
     移動範圍限制在地形範圍內，避免把整張地形拖到畫面外面回不來。 */
  const _panRight = new THREE.Vector3();
  const _panForward = new THREE.Vector3();

  function panCamera(dx, dy) {
    const panScale = view.radius * 0.0016;

    _panRight.setFromMatrixColumn(camera.matrixWorld, 0);
    _panRight.y = 0;
    if (_panRight.lengthSq() > 1e-6) _panRight.normalize();

    camera.getWorldDirection(_panForward);
    _panForward.y = 0;
    if (_panForward.lengthSq() > 1e-6) _panForward.normalize();

    view.target.x -= _panRight.x * dx * panScale;
    view.target.z -= _panRight.z * dx * panScale;
    view.target.x += _panForward.x * dy * panScale;
    view.target.z += _panForward.z * dy * panScale;

    const maxX = WORLD.width * 0.5, maxZ = WORLD.depth * 0.5;
    view.target.x = clamp(view.target.x, -maxX, maxX);
    view.target.z = clamp(view.target.z, -maxZ, maxZ);

    updateCamera();
  }

  function bindControls() {
    canvas.style.touchAction = 'none';
    // 右鍵拖曳用來平移，瀏覽器預設的右鍵選單要關掉，不然拖到一半會跳出選單。
    canvas.addEventListener('contextmenu', e => e.preventDefault());

    canvas.addEventListener('pointerdown', e => {
      dragging = true;
      // 右鍵，或按住 Shift 用左鍵拖曳 = 平移；一般左鍵拖曳維持原本的旋轉。
      dragMode = (e.button === 2 || e.shiftKey) ? 'pan' : 'rotate';
      canvas.setPointerCapture(e.pointerId);
      lastX = downX = e.clientX;
      lastY = downY = e.clientY;
      wrapEl.classList.add('is-dragging');
      canvas.style.cursor = dragMode === 'pan' ? 'move' : 'grabbing';
    });

    canvas.addEventListener('pointermove', e => {
      if (dragging) {
        const dx = e.clientX - lastX, dy = e.clientY - lastY;
        if (dragMode === 'pan') {
          panCamera(dx, dy);
        } else {
          view.theta -= dx * 0.0065;
          view.phi = clamp(view.phi - dy * 0.0065, PHI_MIN, PHI_MAX);
          updateCamera();
        }
        lastX = e.clientX; lastY = e.clientY;
      } else {
        raycastHover(e);
      }
    });

    canvas.addEventListener('pointerup', e => {
      dragging = false;
      wrapEl.classList.remove('is-dragging');
      const moved = Math.hypot(e.clientX - downX, e.clientY - downY);
      if (moved < 4 && dragMode === 'rotate') raycastClick(e);
      canvas.style.cursor = 'grab';
    });

    canvas.addEventListener('pointerleave', () => {
      if (!dragging && hoveredId) {
        const id = hoveredId;
        unhoverMarker(id);
        if (handlers.onHoverEnd) handlers.onHoverEnd(id);
      }
    });

    canvas.addEventListener('wheel', e => {
      e.preventDefault();
      view.radius = clamp(view.radius * Math.pow(1.0016, e.deltaY), RADIUS_MIN, RADIUS_MAX);
      updateCamera();
    }, { passive: false });
  }

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  function pickMarkerId(e) {
    const rect = canvas.getBoundingClientRect();
    pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const dots = [...markers.values()].map(m => m.dot);
    const hits = raycaster.intersectObjects(dots, false);
    return hits.length ? hits[0].object.userData.id : null;
  }

  function raycastHover(e) {
    const rect = canvas.getBoundingClientRect();
    const coords = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    const id = pickMarkerId(e);
    if (id !== hoveredId) {
      if (hoveredId && handlers.onHoverEnd) handlers.onHoverEnd(hoveredId);
      if (id) {
        hoverMarker(id);
        if (handlers.onHoverStart) handlers.onHoverStart(id, coords);
      } else {
        hoveredId = null;
      }
      canvas.style.cursor = id ? 'pointer' : (dragging ? 'grabbing' : 'grab');
    } else if (id && handlers.onHoverMove) {
      handlers.onHoverMove(id, coords);
    }
  }

  function raycastClick(e) {
    const id = pickMarkerId(e);
    if (id && handlers.onSelect) handlers.onSelect(id);
  }

  /* ── 遮蔽偵測（每幀，全部標記）────
     從攝影機往每個標記投一道射線，只和地形網格做相交測試；
     若地形擋在攝影機與標記之間，代表這顆標記目前被山體擋住，
     此時開啟穿透深度測試的 ghostDot + 垂直定位線讓標記依然可見。
     名稱要不要顯示不歸這裡管，交給 layers.labels／選取／hover 的
     既有規則決定，避免使用者關掉「點位名稱」後名稱又被硬顯示出來。
     「堰塞湖點位」圖層本身關掉時，這些輔助視覺一律不顯示。
     為節省效能，每 3 個影格才重算一次（旋轉/縮放時仍然順暢）。 */
  const _occRaycaster = new THREE.Raycaster();
  const _occDir = new THREE.Vector3();
  const _occOrigin = new THREE.Vector3();
  let _occFrame = 0;

  function updateOcclusion() {
    _occFrame = (_occFrame + 1) % 3;
    if (_occFrame !== 0 || !terrainMesh) return;
    _occOrigin.copy(camera.position);
    markers.forEach(m => {
      if (!m.ghostDot) return;
      const setGhostVisible = visible => {
        m.ghostDot.visible = visible;
        if (m.pinLine) m.pinLine.visible = visible;
        if (m.pinHead) m.pinHead.visible = visible;
      };
      // 「堰塞湖點位」圖層關掉時，這顆標記本體都不顯示了，
      // 穿透替身、定位線這些輔助也都不該出現。名稱要不要顯示是
      // 「點位名稱」開關（layers.labels）跟選取/hover 的事，這裡不管。
      if (!layers.points) { setGhostVisible(false); return; }
      if (!m._ghostEligible) { setGhostVisible(false); return; }
      const worldY = m.group.position.y + m.dotR * 0.7;
      _occDir.set(m.group.position.x, worldY, m.group.position.z).sub(_occOrigin);
      const dist = _occDir.length();
      if (dist < 1e-4) { setGhostVisible(false); return; }
      // 容差原本只有 dotR*1.2，對半徑很小的標記來說幾乎等於 0，
      // 常常把「貼在自己所在那塊地形上」誤判成「被前方山體擋住」。
      // 改成跟 dotR 無關的絕對下限，確保小標記也有合理的容差。
      const margin = Math.max(m.dotR * 1.2, 0.09);
      const far = dist - margin;
      if (far <= 0) { setGhostVisible(false); return; }
      _occDir.divideScalar(dist);
      _occRaycaster.set(_occOrigin, _occDir);
      _occRaycaster.far = far;
      _occRaycaster.near = 0;
      const hit = _occRaycaster.intersectObject(terrainMesh, false);
      setGhostVisible(hit.length > 0);
    });
  }

  /* ── 標籤位置更新（每幀）───────────────── */
  const _v = new THREE.Vector3();
  function updateLabels() {
    const rect = canvas.getBoundingClientRect();
    markers.forEach(m => {
      _v.set(m.group.position.x, m.group.position.y + m.dotR * 1.9, m.group.position.z);
      _v.project(camera);
      if (_v.z > 1) { m.label.style.display = 'none'; return; }
      const sx = (_v.x * 0.5 + 0.5) * rect.width;
      const sy = (-_v.y * 0.5 + 0.5) * rect.height;
      m.label.style.display = 'block';
      m.label.style.transform = `translate(${sx.toFixed(1)}px, ${sy.toFixed(1)}px)`;
    });
  }

  /* ── 動畫迴圈：CAP 環旋轉 + 脈動 ──────────── */
  function animate() {
    requestAnimationFrame(animate);
    clock += 0.016;
    if (isIdleRotating) {
      view.theta += IDLE_ROTATE_SPEED * 0.016;
      updateCamera();
    }
    markers.forEach(m => {
      if (m.capRing.visible) {
        m.capRing.rotation.z += 0.012;
        m.capRing.material.opacity = 0.55 + Math.sin(clock * 2.4) * 0.35;
      }
      if (m.ghostDot && m.ghostDot.visible) {
        m.ghostDot.material.opacity = 0.65 + Math.sin(clock * 3.2) * 0.25;
      }
    });
    updateOcclusion();
    updateLabels();
    renderer.render(scene, camera);
  }

  function resize() {
    const rect = wrapEl.getBoundingClientRect();
    const w = Math.max(1, rect.width), h = Math.max(1, rect.height);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function init(canvasEl, labelsElArg, hintResetEl, handlersArg) {
    TERRAIN = window.TAIWAN_TERRAIN;
    if (!TERRAIN) { console.error('Map3D: 找不到 window.TAIWAN_TERRAIN，請確認 data/terrain.js 已載入'); return; }
    if (typeof THREE === 'undefined') { console.error('Map3D: 找不到 THREE，請確認 three.min.js 已載入'); return; }

    canvas = canvasEl;
    labelsEl = labelsElArg;
    wrapEl = canvas.parentElement;
    handlers = handlersArg || {};

    WORLD.depth = WORLD.width * (TERRAIN.bounds.north - TERRAIN.bounds.south)
                              / (TERRAIN.bounds.east - TERRAIN.bounds.west);
    HEIGHT_SCALE = (WORLD.width * 0.16) / Math.max(1, TERRAIN.maxElevation);
    RADIUS_MIN = 6;
    RADIUS_MAX = 48;

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);

    scene.add(new THREE.AmbientLight(0xbcCdc2, 0.55));
    const sun = new THREE.DirectionalLight(0xE9F1EB, 1.05);
    sun.position.set(WORLD.width * 0.6, WORLD.width * 1.1, WORLD.depth * 0.35);
    scene.add(sun);
    const rim = new THREE.DirectionalLight(0x3FA9A0, 0.28);
    rim.position.set(-WORLD.width, WORLD.width * 0.4, -WORLD.depth * 0.5);
    scene.add(rim);

    groundPlate = buildGroundPlate();
    scene.add(groundPlate);

    terrainMesh = buildTerrain();
    scene.add(terrainMesh);

    markerGroup = new THREE.Group();
    scene.add(markerGroup);

    capAreaRing = buildCapAreaRing();

    resetView();
    bindControls();
    bindIdleRotate();

    if (hintResetEl) {
      hintResetEl.addEventListener('click', resetView);
    }

    window.addEventListener('resize', resize);
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(resize).observe(wrapEl);
    }
    resize();
    canvas.style.cursor = 'grab';
    animate();
  }

  return {
    init, setLakes, sync, hoverMarker, unhoverMarker, setLayers, resize, resetView,
    setTriggerAreas, setActiveTriggerTask,
  };
})();
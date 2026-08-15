/* ════════════════════════════════════════
   cap.js — CAP 1.2 (CAP-TWP) 示警映射與 XML 組裝

   依循 docs/ossint-barrier-lake-architecture.html 第 09 節的設計、
   docs/attribution.md 的稽核精神：
     · 這裡不做「新判斷」，只把既有的風險模型輸出（risk.js）
       翻譯成 CAP 欄位。所有門檻寫在本檔最上方，方便之後校準。
     · 每個 CAP 欄位的依據都附進 <parameter>，前端也把依據攤開顯示，
       不接受「只給結論不給依據」。
     · status 目前固定為 "Test"——這是 demo/開發階段的資料，
       尚未經過 NCDR 平台介接與正式門檻審定，絕不可標成 "Actual"。
       正式上線前這裡是必須手動確認並修改的地方。

   已知限制（必須誠實反映在 UI 上，不能只藏在註解裡）：
     · 風險模型正樣本數極少（見 RISK_MODEL_META.nPositives，目前是 12），
       且 risk_formula.txt 附的 0.927 AUC 是全資料擬合、非留出驗證，
       容易高估。因此 certainty 最高只給到 "Possible"，
       不給 "Likely" 或 "Observed"。
     · area 優先用 pipeline.assess.run 產生的淹沒多邊形（見
       dashboard/data/inundation.js／window.LAKE_INUNDATION）；沒有對應
       資料時（湖泊不是 statusKey==watch，或 build_all 沒加 --demo-dem
       執行 assess 步驟）才退回固定半徑的 circle 頂著。
       polygon 來源若是 method === "synthetic_demo_dem"，代表地形是
       合成示範資料、不是真實 DEM——這件事必須透過
       area.inundationMethod / area.inundationDisclaimer 傳到前端顯示
       出來（見 app.js renderEvidenceCard），不能被當成真的地形分析結果。
     · info.forecast（有 window.LAKE_FORECAST 資料時才會有）是額外附加
       的溢流時間預估（pipeline.attribution.forecast，水量平衡、區間
       輸出），跟 severity/urgency/certainty 是分開的兩件事、互不覆寫：
       severity/urgency/certainty 一律只看 risk.js（邏輯迴歸），
       forecast 只在查得到公開可信集水面積來源時才有值。兩者角色不同，
       別把 forecast 的「有沒有可能溢流」跟 severity 的「風險高不高」
       混為一談——這是本專案「CAP 的風險依據」這個決定的具體實作，
       詳見頂層 README。
   ════════════════════════════════════════ */

'use strict';

const CAP = (() => {

  const CAP_TWP_CODE = 'CAP-TWP:1.0';
  const DEFAULT_CIRCLE_RADIUS_KM = 3;

  // ── 門檻（唯一該調整風險分級的地方）───────────

  /* 風險模型完全不知道「這個湖現在還存不存在」——它只看雨量與登載
     蓄水量特徵。清冊的 statusKey 才是現況存續的唯一依據，CAP 在這裡
     必須以 statusKey 把關，否則會對「已消失」的湖發出嚴重示警，
     這正是最典型會造成「狼來了」的假警報。

     覆寫規則：
       gone   → 壩體已不存在，不管模型怎麼判，一律 urgency=Past、
                severity 不採信模型值，並在 basis 中說明原因。
       stable → 已有穩定溢流道、現地有實際觀測佐證，模型判高風險時
                下修一級（Severe → Moderate），因為模型沒有把
                「已穩定」這個事實納入判斷。
       watch  → 唯一該直接採信模型輸出的狀況。 */

  function severityFromRisk(risk, lake) {
    if (lake && lake.statusKey === 'gone') {
      return {
        value: 'Unknown',
        basis: '清冊登載為「已消失」（壩體已不存在），風險模型未區分現況存續，此欄位對已消失個案不具意義',
      };
    }
    if (!risk) return { value: 'Unknown', basis: '無風險模型評估資料' };

    if (risk.risk_level === '高') {
      if (lake && lake.statusKey === 'stable') {
        return {
          value: 'Moderate',
          basis: `risk_prob=${fmtProb(risk.risk_prob)}，模型判定為高風險，` +
                 '但清冊登載為「存在(已穩定)」（已形成穩定溢流道），模型未納入此事實，下修一級',
        };
      }
      return { value: 'Severe', basis: `risk_prob=${fmtProb(risk.risk_prob)}，模型判定為高風險` };
    }
    if (risk.risk_level === '低') {
      return { value: 'Minor', basis: `risk_prob=${fmtProb(risk.risk_prob)}，模型判定為低風險` };
    }
    return { value: 'Unknown', basis: 'risk_level 欄位為空或無法辨識' };
  }

  /* 風險快照是批次計算（目前每湖僅一筆最新值），不是即時觀測，
     因此不給 Immediate——那必須保留給真的有秒級/分鐘級觀測依據的情境。 */
  function urgencyFromRisk(risk, lake) {
    if (lake && lake.statusKey === 'gone') {
      return { value: 'Past', basis: '壩體已消失，已無應變必要（CAP「Past」原意即為此）' };
    }
    if (!risk) return { value: 'Unknown', basis: '無風險模型評估資料' };
    if (risk.risk_level === '高') {
      return { value: 'Expected', basis: '批次風險評估判定為高風險，應於短期內密切注意（非即時觀測，不判為 Immediate）' };
    }
    return { value: 'Future', basis: '目前風險判定為低，僅列入例行監控' };
  }

  /* 樣本數極少、且擬合 AUC 非留出驗證 -> 最高只給 Possible，
     誠實反映模型現階段的可信度，不要讓示警看起來比實際上更確定。 */
  function certaintyFromModel(meta) {
    const n = meta ? meta.nPositives : null;
    const heldOut = meta ? meta.rocAuc : null;
    if (n != null && n < 30 && heldOut == null) {
      return {
        value: 'Possible',
        basis: `正樣本數僅 ${n} 筆，且無留出驗證（roc_auc 未提供），模型可信度尚待確認`,
      };
    }
    return { value: 'Likely', basis: '模型樣本數與驗證方式已達可接受水準' };
  }

  function fmtProb(p) {
    return (p == null) ? '—' : (p * 100).toFixed(0) + '%';
  }

  // ── 敘述 ──────────────────────────────────

  function topDrivers(risk, meta, n = 2) {
    if (!risk || !meta || !meta.featureImportance) return [];
    const FEATURE_TEXT = {
      volume: '既有蓄水量', rain_7d: '近 7 日累積雨量', rain_3d: '近 3 日累積雨量',
      rain_30d: '近 30 日累積雨量', rain_1d: '近 1 日雨量',
      shaking_30d: '近 30 日地動', quake_max_mag_30d: '近 30 日最大地震規模',
      quake_count_30d: '近 30 日地震次數',
      formed_by_quake: '地震誘發', formed_by_rain: '降雨誘發',
    };
    return Object.entries(meta.featureImportance)
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, n)
      .map(([k]) => FEATURE_TEXT[k] || k);
  }

  function headline(lake, risk) {
    if (lake && lake.statusKey === 'gone') {
      return `${lake.name}：已消失，無需示警（風險模型未納入現況存續）`;
    }
    if (!risk) return `${lake.name}：無風險模型評估`;
    return `${lake.name}堰塞湖風險示警 — ${risk.risk_level}風險（risk_prob ${fmtProb(risk.risk_prob)}）`;
  }

  function description(lake, risk, meta) {
    if (lake && lake.statusKey === 'gone') {
      return `${lake.name}清冊登載為「已消失」，壩體已不存在。風險模型僅依雨量與登載蓄水量特徵計算，` +
        `並未判斷湖體現況是否仍存在，其原始輸出${risk ? `（risk_prob ${fmtProb(risk.risk_prob)}，${risk.risk_level}風險）` : ''}` +
        `對本案不具意義，故不發布示警。`;
    }
    if (!risk) return `${lake.name}目前無風險模型評估資料，無法產生示警內容。`;

    const drivers = topDrivers(risk, meta);
    const driverText = drivers.length ? `，主要驅動特徵為${drivers.join('、')}` : '';
    const stableNote = (lake && lake.statusKey === 'stable')
      ? '清冊登載為「存在(已穩定)」（已形成穩定溢流道），模型未納入此事實，故已將嚴重度下修一級。'
      : '';
    return `依 ERA5-Land 降雨再分析資料與邏輯迴歸風險模型（快照日期 ${risk.date}）評估，` +
      `${lake.name}目前風險機率為 ${fmtProb(risk.risk_prob)}，模型原始判定為「${risk.risk_level}風險」${driverText}。${stableNote}` +
      `本評估屬統計模型推論，非現地實測確認，正樣本數少（${meta ? meta.nPositives : '—'} 筆），` +
      `請配合現地觀測與官方公告研判，不應單獨作為疏散決策依據。`;
  }

  /* 是否構成「需要出現在示警橫幅/地圖示警環上的可行動警示」。
     只有 statusKey === 'watch'（現況仍監測中）且模型判高風險，
     才算數——已消失、已穩定的湖即使模型算出高風險也不算。 */
  function shouldAlert(lake) {
    return !!(lake && lake.statusKey === 'watch' && lake.risk && lake.risk.risk_level === '高');
  }

  /* 風險快照是「每湖一筆最新值」的批次結果，不是連續觀測。
     effective 用該筆快照的日期（沒有快照就退回 sent 當下）；
     expires 抓 +1 天——下一次批次重跑後這筆判斷就該被取代，
     不該讓一筆舊快照的示警無限期掛著。 */
  function effectiveWindow(risk) {
    const base = (risk && risk.date) ? new Date(`${risk.date}T00:00:00+08:00`) : new Date();
    const effective = base.toISOString();
    const expires = new Date(base.getTime() + 24 * 3600 * 1000).toISOString();
    return { effective, expires };
  }

  /* 建議行動：依「現況把關後」的 severity/urgency 決定文字，
     不是直接照模型原始輸出講話——已消失/已穩定的湖不能叫人家撤離。 */
  function instructionFor(lake, risk, severity, urgency) {
    if (lake && lake.statusKey === 'gone') {
      return '壩體已消失，無需採取行動。如發現清冊現況與實際不符，請通報更新清冊資料。';
    }
    if (!risk) {
      return '目前無風險模型評估資料，請以清冊現況與官方公告為準，持續留意當地雨量與河川水位。';
    }
    if (lake && lake.statusKey === 'stable') {
      return '已有穩定溢流道，惟風險模型未納入此事實：建議維持例行監測，暴雨期間留意上游雨量即可，非必要不需特殊應變。';
    }
    if (severity.value === 'Severe') {
      return '請留意最新降雨與官方公告，避免進入下游河道與低窪地區；若現地出現異常湧水、水色混濁、水位快速上升等徵兆，請立即遠離並通報，此為統計模型批次示警，非即時觀測確認，仍須配合現地觀察研判。';
    }
    return '目前風險判定為低，維持例行監測即可，暴雨期間仍建議留意當地雨量與官方公告。';
  }

  // ── 淹沒範圍 area（優先用 assess 模組算出的多邊形，沒有才退回 circle）──

  /* CAP 1.2 的 <polygon> 格式是「lat,lon 空白分隔的點序列，首尾點需相同
     以閉合」；inundation.polygon 存的是 [lon,lat] 對（跟 GeoJSON 慣例一致），
     這裡才轉成 CAP 要的 "lat,lon" 順序與字串格式。 */
  function polygonToCapString(points) {
    if (!points || points.length < 3) return null;
    const ring = points.slice();
    const [firstLon, firstLat] = ring[0];
    const [lastLon, lastLat] = ring[ring.length - 1];
    if (firstLon !== lastLon || firstLat !== lastLat) ring.push(ring[0]);
    return ring.map(([lon, lat]) => `${lat},${lon}`).join(' ');
  }

  function areaFor(lake, inundation) {
    const areaDesc = `${lake.county}${lake.town}${lake.village}`;
    if (inundation && inundation.polygon && inundation.polygon.length >= 3) {
      return {
        areaDesc,
        circle: null,
        polygon: polygonToCapString(inundation.polygon),
        polygonPoints: inundation.polygon,          // 供前端地圖直接畫圖用，不用重新解析 CAP 字串
        inundationMethod: inundation.method || null, // "real_dem" | "synthetic_demo_dem"
        inundationDisclaimer: inundation.disclaimer || null,
      };
    }
    return {
      areaDesc,
      circle: (lake.lat != null && lake.lon != null)
        ? `${lake.lat},${lake.lon} ${DEFAULT_CIRCLE_RADIUS_KM}`
        : null,
      polygon: null,
      polygonPoints: null,
      inundationMethod: null,
      inundationDisclaimer: null,
    };
  }

  // ── 組裝 CAP 物件（供 UI 與 XML 共用）──────────

  function build(lake, risk, meta, inundation, forecastData, opts = {}) {
    const severity = severityFromRisk(risk, lake);
    const urgency = urgencyFromRisk(risk, lake);
    const certainty = certaintyFromModel(meta);
    const { effective, expires } = effectiveWindow(risk);

    return {
      identifier: opts.identifier || `ossint-${lake.id}-${risk ? risk.date : 'na'}`,
      sender: opts.sender || 'ossint2026-demo@example.org',
      sent: new Date().toISOString(),
      status: 'Test',       // demo 階段固定值，正式上線前必須人工確認並改掉
      msgType: 'Alert',
      scope: 'Public',
      code: CAP_TWP_CODE,
      info: {
        language: 'zh-TW',
        category: 'Geo',
        event: '堰塞湖溢流風險示警',
        responseType: (lake && lake.statusKey === 'gone')
          ? 'AllClear'
          : (risk && risk.risk_level === '高' ? 'Monitor' : 'None'),
        urgency, severity, certainty,
        senderName: 'OSSInt 2026 堰塞湖快速評估系統（Demo）',
        headline: headline(lake, risk),
        description: description(lake, risk, meta),
        instruction: instructionFor(lake, risk, severity, urgency),
        effective, expires,
        area: areaFor(lake, inundation),
        // 附加資訊，不影響 severity/urgency/certainty 的判斷：見上方檔頭說明。
        forecast: forecastData || null,
        parameters: buildParameters(lake, risk, meta, severity, urgency, certainty, inundation, forecastData),
      },
    };
  }

  function buildParameters(lake, risk, meta, severity, urgency, certainty, inundation, forecastData) {
    const params = [
      { valueName: 'severity_basis', value: severity.basis },
      { valueName: 'urgency_basis', value: urgency.basis },
      { valueName: 'certainty_basis', value: certainty.basis },
    ];
    if (risk) {
      params.push({ valueName: 'risk_prob', value: String(risk.risk_prob) });
      params.push({ valueName: 'risk_snapshot_date', value: risk.date });
      params.push({ valueName: 'model', value: meta ? meta.model : '' });
    }
    if (inundation && inundation.polygon && inundation.polygon.length >= 3) {
      params.push({ valueName: 'inundation_method', value: inundation.method || '' });
      params.push({ valueName: 'inundation_area_ha', value: String(inundation.areaHa) });
      if (inundation.disclaimer) {
        params.push({ valueName: 'inundation_disclaimer', value: inundation.disclaimer });
      }
    }
    if (forecastData) {
      params.push({ valueName: 'forecast_catchment_km2', value: String(forecastData.catchmentKm2) });
      params.push({ valueName: 'forecast_catchment_source', value: forecastData.catchmentSource });
      if (forecastData.median) params.push({ valueName: 'forecast_median', value: forecastData.median });
      params.push({ valueName: 'forecast_disclaimer', value: forecastData.disclaimer });
    }
    return params;
  }

  // ── XML 輸出 ──────────────────────────────

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function toXML(alert) {
    const info = alert.info;
    const params = info.parameters.map(p =>
      `    <parameter><valueName>${esc(p.valueName)}</valueName><value>${esc(p.value)}</value></parameter>`
    ).join('\n');

    const areaLines = [
      `    <areaDesc>${esc(info.area.areaDesc)}</areaDesc>`,
      info.area.polygon ? `    <polygon>${esc(info.area.polygon)}</polygon>` : '',
      (!info.area.polygon && info.area.circle) ? `    <circle>${esc(info.area.circle)}</circle>` : '',
    ].filter(Boolean).join('\n');

    return `<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>${esc(alert.identifier)}</identifier>
  <sender>${esc(alert.sender)}</sender>
  <sent>${esc(alert.sent)}</sent>
  <status>${esc(alert.status)}</status>
  <msgType>${esc(alert.msgType)}</msgType>
  <scope>${esc(alert.scope)}</scope>
  <code>${esc(alert.code)}</code>
  <info>
    <language>${esc(info.language)}</language>
    <category>${esc(info.category)}</category>
    <event>${esc(info.event)}</event>
    <responseType>${esc(info.responseType)}</responseType>
    <urgency>${esc(info.urgency.value)}</urgency>
    <severity>${esc(info.severity.value)}</severity>
    <certainty>${esc(info.certainty.value)}</certainty>
    <senderName>${esc(info.senderName)}</senderName>
    <headline>${esc(info.headline)}</headline>
    <description>${esc(info.description)}</description>
    <instruction>${esc(info.instruction)}</instruction>
    <effective>${esc(info.effective)}</effective>
    <expires>${esc(info.expires)}</expires>
${params}
  <area>
${areaLines}
  </area>
  </info>
</alert>
`;
  }

  return { build, toXML, severityFromRisk, urgencyFromRisk, certaintyFromModel, topDrivers, shouldAlert };
})();

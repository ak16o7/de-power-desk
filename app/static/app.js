/* DE Power Desk v5 — front end. No build step, Plotly bundled locally. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const REFRESH_S = Math.max(60, parseInt(document.body.dataset.refresh, 10) || 300);
  const NF0 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 });
  const NF1 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const NF2 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  const BORDER_NAMES = { FR: 'FR', NL: 'NL', BE: 'BE', DK_1: 'DK1', DK_2: 'DK2', AT: 'AT', CH: 'CH', CZ: 'CZ', PL: 'PL', SE_4: 'SE4', NO_2: 'NO2' };
  const ZONE_NAMES = { DE_LU: 'DE-LU', FR: 'FR', NL: 'NL', BE: 'BE' };
  const state = { data: {}, errors: {}, resTech: 'RES', outZone: 'DE_LU', timer: null, countdown: REFRESH_S, loading: false };

  // ---------- formatting ----------
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const fmt = (v, nf = NF0) => (isNum(v) ? nf.format(v) : '—');
  const sgn = (v, nf = NF0) => (isNum(v) ? (v > 0 ? '+' : v < 0 ? '−' : '±') + nf.format(Math.abs(v)) : '—');
  const mw = (v, signed = true) => (isNum(v) ? `${signed ? sgn(v) : fmt(v)}<small>MW</small>` : '—');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hhmm = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' });
  };
  const dayTime = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' });
  };
  const todayBerlin = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Plotly ignores UTC offsets; strip them so the axis shows Berlin wall time.
  const xs = (pts) => (pts || []).map((p) => p.t.slice(0, 19));
  const ys = (pts) => (pts || []).map((p) => p.v);
  const toMap = (pts) => new Map((pts || []).map((p) => [p.t, p.v]));
  const diff = (a, b) => {
    const mb = toMap(b);
    return (a || []).filter((p) => mb.has(p.t)).map((p) => ({ t: p.t, v: p.v - mb.get(p.t) }));
  };

  // ---------- signal tiles ----------
  function setSig(id, { value, sub = '', foot = '', tag = null }) {
    const el = $(id);
    el.querySelector('[data-value]').innerHTML = value;
    el.querySelector('[data-sub]').innerHTML = sub;
    el.querySelector('[data-foot]').innerHTML = foot;
    const t = el.querySelector('[data-tag]');
    t.className = 'tag' + (tag ? ' ' + tag.cls : '');
    t.textContent = tag ? tag.text : '';
    t.classList.toggle('hidden', !tag);
  }
  // Price direction implied by a supply/demand surprise; thresholds are desk heuristics.
  const dir = (v, thr, positiveIs) => {
    if (!isNum(v) || Math.abs(v) < thr) return null;
    const up = positiveIs === 'bull' ? v > 0 : v < 0;
    return up ? { cls: 'bull', text: 'bullish' } : { cls: 'bear', text: 'bearish' };
  };
  const deltas = (c, unit = 'MW') => (c ? `<span class="deltas"><span>Δ15m <b>${sgn(c.d15_mw)}</b></span><span>Δ60m <b>${sgn(c.d60_mw)}</b></span></span>` : '');

  function chip(text, cls = '', title = '') {
    const ico = cls === 'ok' ? '✓' : cls === 'warn' ? '!' : cls === 'bad' ? '×' : '·';
    return `<span class="chip ${cls}" title="${esc(title)}"><span class="ico">${ico}</span>${esc(text)}</span>`;
  }
  const stateCls = (s) => (s === 'ok' || s === 'complete' ? 'ok' : s === 'partial' || s === 'not_configured' || s === 'no_data' || s === 'empty' ? 'warn' : 'bad');

  // ---------- chart helpers ----------
  const PLOT_CFG = { displayModeBar: false, responsive: true };
  function baseLayout(extra = {}) {
    const text2 = cssVar('--text-2'), grid = cssVar('--line-soft'), muted = cssVar('--muted');
    return Object.assign({
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: text2, size: 11, family: 'Inter, system-ui, sans-serif' },
      margin: { l: 56, r: 12, t: 28, b: 30 },
      hovermode: 'x unified',
      hoverlabel: { bgcolor: cssVar('--surface-2'), bordercolor: cssVar('--line'), font: { color: cssVar('--text') } },
      legend: { orientation: 'h', x: 0, y: 1.1, font: { size: 11 } },
      xaxis: { type: 'date', tickformat: '%H:%M', hoverformat: '%d.%m. %H:%M', gridcolor: grid, linecolor: grid, zeroline: false, color: muted },
      yaxis: { gridcolor: grid, zerolinecolor: cssVar('--line'), ticksuffix: '', color: muted, separatethousands: true, tickformat: ',.0f' },
      separators: ',.',
    }, extra);
  }
  const line = (pts, name, color, dash = 'solid', yaxis = 'y', extra = {}) => Object.assign({
    type: 'scatter', mode: 'lines', x: xs(pts), y: ys(pts), name, yaxis,
    line: { color, width: 2, dash, shape: 'hv' }, hovertemplate: `%{y:,.0f}`,
  }, extra);
  const bars = (pts, name, yaxis = 'y2', unit = 'MW', names = ['über Prognose', 'unter Prognose']) => {
    const pos = cssVar('--pos'), neg = cssVar('--neg');
    const p = (pts || []).map((q) => ({ t: q.t, v: q.v >= 0 ? q.v : null }));
    const n = (pts || []).map((q) => ({ t: q.t, v: q.v < 0 ? q.v : null }));
    return [
      { type: 'bar', x: xs(p), y: ys(p), name: `${name}: ${names[0]}`, yaxis, marker: { color: pos }, hovertemplate: `%{y:+,.0f} ${unit}`, showlegend: false },
      { type: 'bar', x: xs(n), y: ys(n), name: `${name}: ${names[1]}`, yaxis, marker: { color: neg }, hovertemplate: `%{y:+,.0f} ${unit}`, showlegend: false },
    ];
  };
  function plot(id, traces, layout) {
    const el = $(id);
    if (!traces.some((t) => t.x && t.x.length)) {
      if (window.Plotly) Plotly.purge(el);
      el.innerHTML = '<div class="empty">Keine Daten für diese Auswahl veröffentlicht.</div>';
      return;
    }
    if (el.querySelector('.empty')) el.innerHTML = '';
    Plotly.react(el, traces, layout, PLOT_CFG);
  }
  const metric = (label, value, small = '') => `<div class="metric"><span>${esc(label)}</span><strong>${value}</strong><small>${small}</small></div>`;

  // ---------- panels ----------
  function renderRes() {
    const d = state.data.renewables;
    if (!d) return;
    const k = d.kpi || {};
    const tech = k.tech_error_mw || {};
    setSig('sigRes', {
      value: mw(k.res_error_mw),
      sub: `Solar <b>${sgn(tech.Solar)}</b> · On <b>${sgn(tech['Wind Onshore'])}</b> · Off <b>${sgn(tech['Wind Offshore'])}</b><br>vs. ID-Stand 08:00: <b>${sgn(k.res_error_id_mw)}</b> MW`,
      foot: `<span>MTU ${hhmm(k.as_of)}</span><span>Ø Tag ${sgn(k.day_avg_error_mw)} MW</span>${deltas(k.changes)}`,
      tag: dir(k.res_error_mw, 300, 'bear'),
    });
    const hasFwd = isNum(k.next4h_revision_avg_mw);
    setSig('sigRev', {
      value: hasFwd ? mw(k.next4h_revision_avg_mw) : '—',
      sub: hasFwd ? `Spanne <b>${sgn(k.next4h_revision_min_mw)}</b> bis <b>${sgn(k.next4h_revision_max_mw)}</b> MW<br>Rest des Tages Ø <b>${sgn(k.rest_of_day_revision_avg_mw)}</b> MW`
        : 'Nur für den laufenden Tag, sobald eine laufende Prognose (A18) vollständig ist.',
      foot: hasFwd ? `<span>${hhmm(k.next4h_window_start)}–${hhmm(k.next4h_window_end)}</span><span>laufende Prognose − DA</span>` : '<span>laufende Prognose − DA</span>',
      tag: dir(k.next4h_revision_avg_mw, 300, 'bear'),
    });

    const s = d.series || {};
    const T = state.resTech;
    const pts = (suffix) => s[`${T} ${suffix}`] || [];
    const err = T === 'RES' ? s['RES Forecast Error'] : diff(pts('Actual'), pts('Day-ahead'));
    const traces = [
      line(pts('Actual'), 'Ist', cssVar('--s1')),
      line(pts('Day-ahead'), 'DA (D-1 18:00)', cssVar('--s2'), 'dash'),
      line(pts('Intraday'), 'ID (D 08:00)', cssVar('--s3'), 'dot'),
      line(pts('Current'), 'Laufend (A18)', cssVar('--s4'), 'dashdot'),
      ...bars(err, 'Ist − DA'),
    ];
    plot('chRes', traces, baseLayout({
      xaxis: Object.assign(baseLayout().xaxis, { anchor: 'y2' }),
      yaxis: Object.assign(baseLayout().yaxis, { domain: [0.36, 1], title: { text: 'MW', font: { size: 10 } } }),
      yaxis2: Object.assign(baseLayout().yaxis, { domain: [0, 0.28], title: { text: 'Ist − DA', font: { size: 10 } } }),
      bargap: 0.15,
    }));
    const errVals = (err || []).map((p) => p.v);
    const mae = errVals.length ? errVals.reduce((a, v) => a + Math.abs(v), 0) / errVals.length : null;
    const maxAbs = errVals.length ? errVals.reduce((a, v) => (Math.abs(v) > Math.abs(a) ? v : a), 0) : null;
    $('mRes').innerHTML = [
      metric('Abweichung jetzt (Ist − DA)', `${sgn(T === 'RES' ? k.res_error_mw : tech[T])} MW`, `MTU ${hhmm(k.as_of)}`),
      metric('MAE bisher', `${fmt(mae)} MW`, T === 'RES' ? 'mittlere absolute Abweichung' : T),
      metric('Größte Abweichung', `${sgn(maxAbs)} MW`, 'seit 00:00'),
      metric('Ist-Daten bis', hhmm(d.freshness?.actual_through), 'letzte vollständige MTU'),
    ].join('');
  }

  function renderLoad() {
    const d = state.data.load;
    if (!d) return;
    const k = d.kpi || {}, s = d.series || {};
    setSig('sigResid', {
      value: mw(k.residual_surprise_mw),
      sub: `Residuallast <b>${fmt(k.residual_load_mw)}</b> MW<br>Last Ist − DA <b>${sgn(k.load_error_mw)}</b> MW`,
      foot: `<span>MTU ${hhmm(k.surprise_as_of)}</span><span>Ø Tag ${sgn(k.day_avg_surprise_mw)} MW</span>`,
      tag: dir(k.residual_surprise_mw, 500, 'bull'),
    });
    plot('chLoad', [
      line(s['Load Actual'], 'Last Ist', cssVar('--s1')),
      line(s['Load Forecast'], 'Last DA', cssVar('--s1'), 'dash'),
      line(s['Residual Load Actual'], 'Residual Ist', cssVar('--s3')),
      line(s['Residual Load Forecast'], 'Residual DA', cssVar('--s3'), 'dash'),
      ...bars(s['Residual Load Surprise'], 'Residual Ist − DA', 'y2', 'MW', ['höher', 'niedriger']),
    ], baseLayout({
      xaxis: Object.assign(baseLayout().xaxis, { anchor: 'y2' }),
      yaxis: Object.assign(baseLayout().yaxis, { domain: [0.36, 1], title: { text: 'MW', font: { size: 10 } } }),
      yaxis2: Object.assign(baseLayout().yaxis, { domain: [0, 0.28], title: { text: 'Resid. Ist − DA', font: { size: 10 } } }),
      bargap: 0.15,
    }));
    const la = ys(s['Load Actual']);
    $('mLoad').innerHTML = [
      metric('Residuallast jetzt', `${fmt(k.residual_load_mw)} MW`, `MTU ${hhmm(k.as_of)}`),
      metric('Last Ist − DA', `${sgn(k.load_error_mw)} MW`, 'Lastprognosefehler'),
      metric('Residual Ist − DA Ø', `${sgn(k.day_avg_surprise_mw)} MW`, 'seit 00:00'),
      metric('Lastspitze bisher', `${fmt(la.length ? Math.max(...la) : null)} MW`, 'ENTSO-E A65'),
    ].join('');
  }

  function renderFlow() {
    const d = state.data.borders;
    if (!d) return;
    const k = d.kpi || {}, s = d.series || {}, cov = d.coverage || {};
    const flags = d.flags || [];
    setSig('sigFlow', {
      value: mw(k.net_import_mw),
      sub: `Intraday-XB <b>${sgn(k.intraday_xb_mw)}</b> · ungeplant <b>${sgn(k.unscheduled_mw)}</b><br>DA-Fahrplan <b>${sgn(k.da_schedule_mw)}</b> MW`,
      foot: `<span>MTU ${hhmm(k.as_of)}</span><span>${cov.physical_series ?? 0}/${cov.expected ?? 0} Grenzen</span>${flags.length ? `<span class="flag">⚠ ${flags.map((f) => BORDER_NAMES[f.border] || f.border).join(', ')} 0 MW</span>` : ''}`,
      tag: null,
    });
    const sel = $('flowBorder');
    if (sel.options.length <= 1) {
      (k.selected_borders || []).forEach((b) => sel.add(new Option(BORDER_NAMES[b] || b, b)));
    }
    const b = sel.value;
    const src = b === 'TOTAL'
      ? { p: s['Net Physical Import'], da: s['Net DA Schedule'], tot: s['Net Total Schedule'] }
      : { p: d.borders?.[b]?.physical, da: d.borders?.[b]?.scheduled, tot: d.borders?.[b]?.total };
    plot('chFlow', [
      line(src.p, 'Physisch', cssVar('--s1')),
      line(src.da, 'DA-Fahrplan', cssVar('--s2'), 'dash'),
      line(src.tot, 'Gesamtfahrplan (inkl. ID)', cssVar('--s3'), 'dot'),
    ], baseLayout({ yaxis: Object.assign(baseLayout().yaxis, { title: { text: 'MW Import (+) / Export (−)', font: { size: 10 } } }) }));

    const rows = (d.table || []).slice().sort((a, c) => Math.abs(c.physical ?? 0) - Math.abs(a.physical ?? 0));
    const flagText = { zero_flow_all_day: '0 MW ganztägig – Ausfall/Wartung?', zero_physical_with_schedule: '0 MW physisch trotz Fahrplan' };
    $('tFlow').innerHTML = `<thead><tr><th>Grenze</th><th class="num">Physisch</th><th class="num">DA</th><th class="num">Gesamt</th><th class="num">Intraday-XB</th><th class="num">Ungeplant</th><th>Hinweis</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${esc(BORDER_NAMES[r.border] || r.border)}</td><td class="num">${sgn(r.physical)}</td><td class="num">${sgn(r.scheduled)}</td><td class="num">${sgn(r.total)}</td><td class="num">${sgn(r.intraday)}</td><td class="num">${sgn(r.unscheduled)}</td><td>${r.flag ? `<span class="flag">⚠ ${esc(flagText[r.flag] || r.flag)}</span>` : ''}</td></tr>`).join('') +
      `<tr><td><b>Summe</b></td><td class="num"><b>${sgn(k.net_import_mw)}</b></td><td class="num"><b>${sgn(k.da_schedule_mw)}</b></td><td class="num"><b>${sgn(k.total_schedule_mw)}</b></td><td class="num"><b>${sgn(k.intraday_xb_mw)}</b></td><td class="num"><b>${sgn(k.unscheduled_mw)}</b></td><td class="muted">MTU ${hhmm(k.as_of)}</td></tr></tbody>`;
    $('srcFlow').innerHTML = [
      chip(`Physisch ${cov.physical_series}/${cov.expected}`, cov.physical_total_complete ? 'ok' : 'warn'),
      chip(`DA ${cov.scheduled_series}/${cov.expected}`, cov.scheduled_total_complete ? 'ok' : 'warn'),
      chip(`Gesamtfahrplan ${cov.total_series}/${cov.expected}`, cov.total_schedule_complete ? 'ok' : 'warn', 'A09 contract A05'),
    ].join('');
  }

  function renderOut() {
    const d = state.data.outages;
    if (!d) return;
    const k = d.kpi || {}, zones = d.zones || {};
    const de = zones.DE_LU || {};
    setSig('sigOut', {
      value: mw(de.unavailable_mw, false),
      sub: `davon ungeplant <b>${fmt(de.forced_mw)}</b> MW<br>Δ 24 h <b>${sgn(de.delta_24h_mw)}</b> · ungeplant Δ <b>${sgn(de.forced_delta_24h_mw)}</b>`,
      foot: `<span>Stand ${hhmm(k.as_of)}</span><span>neu/geändert 24 h: ${k.recent_notices ?? 0} (${k.recent_forced_notices ?? 0} ungeplant)</span>${de.complete === false ? '<span class="flag">⚠ unvollständig</span>' : ''}`,
      tag: dir(de.delta_24h_mw, 300, 'bull'),
    });
    const zoneOrder = ['DE_LU', 'FR', 'NL', 'BE'];
    $('tZones').innerHTML = `<thead><tr><th>Zone</th><th class="num">Nicht verfügbar</th><th class="num">Ungeplant</th><th class="num">Geplant</th><th class="num">Δ 24 h</th><th class="num">Ungeplant Δ 24 h</th><th class="num">Aktive Meldungen</th><th>Quelle</th></tr></thead><tbody>` +
      zoneOrder.map((z) => {
        const r = zones[z] || {};
        const src = r.source ? esc(r.source) + (r.a77_plant_only_units ? ` <span class="muted">(+${r.a77_plant_only_units} Anlagen nur A77)</span>` : '') : '<span class="flag">keine Daten</span>';
        return `<tr><td><b>${ZONE_NAMES[z]}</b></td><td class="num">${fmt(r.unavailable_mw)}</td><td class="num">${fmt(r.forced_mw)}</td><td class="num">${fmt(r.planned_mw)}</td><td class="num">${sgn(r.delta_24h_mw)}</td><td class="num">${sgn(r.forced_delta_24h_mw)}</td><td class="num">${fmt(r.active_events)}</td><td>${src}${r.complete === false ? ' <span class="flag">⚠ unvollständig</span>' : ''}</td></tr>`;
      }).join('') + '</tbody>';

    const z = state.outZone;
    plot('chOut', [line(d.series?.[z], `${ZONE_NAMES[z]} nicht verfügbar`, cssVar('--s1'), 'solid', 'y', { showlegend: false })],
      baseLayout({ margin: { l: 56, r: 12, t: 10, b: 30 }, yaxis: Object.assign(baseLayout().yaxis, { title: { text: `MW · ${ZONE_NAMES[z]}`, font: { size: 10 } }, rangemode: 'tozero' }) }));

    const typeLabel = (n) => (n.notice_type === 'forced' ? '<span class="flag">ungeplant</span>' : n.notice_type === 'planned' ? 'geplant' : 'unbekannt');
    const window_ = (n) => `${dayTime(n.event_start)} – ${n.duration_class === 'open_ended' ? 'offen' : dayTime(n.event_end)}`;
    const recent = d.recent || [];
    $('tRecent').innerHTML = recent.length
      ? `<thead><tr><th>Veröffentlicht</th><th>Zone</th><th>Anlage</th><th>Brennstoff</th><th class="num">MW</th><th>Art</th><th>Zeitraum</th><th>Status</th></tr></thead><tbody>` +
        recent.map((n) => `<tr><td>${dayTime(n.published)}${n.revision > 1 ? ` <span class="muted">Rev. ${n.revision}</span>` : ''}</td><td>${ZONE_NAMES[n.zone] || esc(n.zone)}</td><td>${esc(n.plant)}</td><td>${esc(n.fuel || '')}</td><td class="num">${fmt(n.unavailable_mw)}</td><td>${typeLabel(n)}</td><td>${window_(n)}</td><td>${n.state === 'active' ? 'aktiv' : n.state === 'upcoming' ? 'kommt' : 'beendet'}</td></tr>`).join('') + '</tbody>'
      : '<tbody><tr><td class="muted">Keine neuen oder geänderten Meldungen in den letzten 24 h.</td></tr></tbody>';
    renderNotices();
    const st = d.source_status || {};
    $('srcOut').innerHTML = Object.entries(st).map(([zone, docs]) => Object.entries(docs).map(([doc, s]) => chip(`${ZONE_NAMES[zone] || zone} ${doc}: ${s}`, stateCls(s))).join('')).join('');
  }

  function renderNotices() {
    const d = state.data.outages;
    if (!d) return;
    const fz = $('fZone').value, ft = $('fType').value, fs = $('fState').value, q = $('fText').value.trim().toLowerCase();
    const rows = (d.notices || []).filter((n) => (!fz || n.zone === fz) && (!ft || n.notice_type === ft) && (!fs || n.state === fs)
      && (!q || `${n.plant} ${n.fuel} ${n.reason_text || ''}`.toLowerCase().includes(q)));
    $('fCount').textContent = `${rows.length} von ${(d.notices || []).length} Meldungen · MW je Meldung, bei Überlappung nicht addierbar`;
    const durLabel = { bounded: '< 30 Tage', long_term: '≥ 30 Tage', open_ended: 'offen', unknown: '?' };
    $('tNotices').innerHTML = `<thead><tr><th>Status</th><th>Zone</th><th>Anlage</th><th>Brennstoff</th><th class="num">MW</th><th class="num">Nenn-MW</th><th>Art</th><th>Dauer</th><th>Zeitraum</th><th>Veröffentlicht</th><th>Grund</th></tr></thead><tbody>` +
      rows.slice(0, 500).map((n) => `<tr><td>${n.state === 'active' ? 'aktiv' : n.state === 'upcoming' ? 'kommt' : 'beendet'}</td><td>${ZONE_NAMES[n.zone] || esc(n.zone)}</td><td>${esc(n.plant)}</td><td>${esc(n.fuel || '')}</td><td class="num">${fmt(n.unavailable_mw)}</td><td class="num">${fmt(n.nominal_mw)}</td><td>${n.notice_type === 'forced' ? '<span class="flag">ungeplant</span>' : n.notice_type === 'planned' ? 'geplant' : '?'}</td><td>${durLabel[n.duration_class] || ''}</td><td>${dayTime(n.event_start)} – ${n.duration_class === 'open_ended' ? 'offen' : dayTime(n.event_end)}</td><td>${dayTime(n.published)}</td><td class="wrapcell">${esc(n.reason_text || '')}</td></tr>`).join('') + '</tbody>';
  }

  function renderSys() {
    const d = state.data.balancing;
    if (!d) return;
    const k = d.kpi || {}, s = d.series || {}, src = d.sources || {};
    const short = k.imbalance_state === 'deficit', long = k.imbalance_state === 'surplus';
    const price = k.price_mode === 'single' ? k.imbalance_price_eur_mwh : k.imbalance_price_short_eur_mwh;
    const act = isNum(k.afrr_de_mw) ? `aFRR DE <b>${sgn(k.afrr_de_mw)}</b> MW`
      : isNum(k.afrr_partial_mw) ? `aFRR <b>${sgn(k.afrr_partial_mw)}</b> MW <span class="muted">(ohne ${esc((k.afrr_missing_areas || []).join(', '))})</span>` : 'aFRR —';
    setSig('sigSys', {
      value: isNum(k.imbalance_volume_mwh) ? `${sgn(k.imbalance_volume_mwh)}<small>MWh</small>` : '—',
      sub: `reBAP <b>${fmt(price, NF2)}</b> €/MWh · ${short ? 'System kurz' : long ? 'System lang' : '—'}<br>${act}${isNum(k.nrv_saldo_mw) ? ` · NRV <b>${sgn(k.nrv_saldo_mw)}</b>` : ''}`,
      foot: `<span>MTU ${hhmm(k.as_of)}</span><span>≈ ${sgn(k.imbalance_avg_mw)} MW</span>${deltas(k.changes)}`,
      tag: short ? { cls: 'bull', text: 'kurz' } : long ? { cls: 'bear', text: 'lang' } : null,
    });

    const traces = [
      ...bars(s['Net imbalance volume'], 'Bilanz', 'y', 'MWh', ['lang', 'kurz']).map((t, i) => Object.assign(t, { showlegend: true, name: i === 0 ? 'System lang (MWh)' : 'System kurz (MWh)' })),
    ];
    if (k.price_mode === 'single' || !(s['Imbalance price long'] || []).length) {
      traces.push(line(s['Imbalance price'], 'reBAP €/MWh', cssVar('--s2'), 'solid', 'y2', { hovertemplate: '%{y:,.2f} €/MWh' }));
    } else {
      traces.push(line(s['Imbalance price long'], 'Preis lang', cssVar('--s2'), 'solid', 'y2', { hovertemplate: '%{y:,.2f} €/MWh' }));
      traces.push(line(s['Imbalance price short'], 'Preis kurz', cssVar('--s4'), 'dash', 'y2', { hovertemplate: '%{y:,.2f} €/MWh' }));
    }
    const actPts = (s['aFRR net DE'] || []).length ? s['aFRR net DE'] : s['aFRR net partial'];
    const actName = (s['aFRR net DE'] || []).length ? 'aFRR netto DE' : `aFRR netto ohne ${(k.afrr_missing_areas || []).join(', ') || '—'}`;
    traces.push(line(actPts, actName, cssVar('--s3'), 'solid', 'y3'));
    if ((s['NRV-Saldo'] || []).length) traces.push(line(s['NRV-Saldo'], 'NRV-Saldo (+ = kurz)', cssVar('--s1'), 'dot', 'y3'));
    const L = baseLayout();
    plot('chSys', traces, baseLayout({
      xaxis: Object.assign(L.xaxis, { anchor: 'y3' }),
      yaxis: Object.assign({}, L.yaxis, { domain: [0.70, 1], title: { text: 'MWh', font: { size: 10 } } }),
      yaxis2: Object.assign({}, L.yaxis, { domain: [0.37, 0.63], title: { text: '€/MWh', font: { size: 10 } } }),
      yaxis3: Object.assign({}, L.yaxis, { domain: [0, 0.30], title: { text: 'MW', font: { size: 10 } } }),
      bargap: 0.15,
    }));
    $('mSys').innerHTML = [
      metric('reBAP jetzt', `${fmt(price, NF2)} €/MWh`, `MTU ${hhmm(k.price_as_of)}`),
      metric('reBAP Ø / Max / Min', `${fmt(k.day_avg_price_eur_mwh)} / ${fmt(k.day_max_price_eur_mwh)} / ${fmt(k.day_min_price_eur_mwh)}`, '€/MWh seit 00:00'),
      metric('aFRR netto', isNum(k.afrr_de_mw) ? `${sgn(k.afrr_de_mw)} MW` : isNum(k.afrr_partial_mw) ? `${sgn(k.afrr_partial_mw)} MW*` : '—', esc(k.activation_source || 'keine Quelle')),
      metric('NRV-Saldo', isNum(k.nrv_saldo_mw) ? `${sgn(k.nrv_saldo_mw)} MW` : '—', isNum(k.nrv_saldo_mw) ? (k.nrv_state === 'deficit' ? 'Unterdeckung' : 'Überdeckung') : 'netztransparenz.de nicht konfiguriert'),
    ].join('');
    const ntpOk = src.NTP?.state === 'ok';
    $('srcSys').innerHTML = Object.entries(src).map(([key, v]) => {
      const secondary = ntpOk && key === '12.3.E';
      const label = `${v.label}${secondary ? ' (nur Reserve)' : ''}: ${v.state === 'not_configured' ? 'nicht konfiguriert' : v.state}`;
      const diag = key === 'NTP' && v.diag ? Object.entries(v.diag).map(([k, d]) => `${k}: ${d.variant || '?'} ${d.flavour || ''} ${d.error || ''}`).join(' | ') : (v.scope || '');
      return chip(label, secondary ? '' : stateCls(v.state), diag);
    }).join('');
  }

  const RENDER = { renewables: renderRes, load: renderLoad, borders: renderFlow, outages: renderOut, balancing: renderSys };

  // ---------- data loading ----------
  async function getJSON(url, ms = 150000) {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), ms);
    try {
      const r = await fetch(url, { signal: ctl.signal, headers: { Accept: 'application/json' } });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      return body;
    } finally { clearTimeout(to); }
  }

  async function loadAll() {
    if (state.loading) return;
    state.loading = true;
    const day = $('day').value || todayBerlin();
    setStatus('warn', 'lädt…');
    const names = Object.keys(RENDER);
    await Promise.all(names.map(async (name) => {
      try {
        state.data[name] = await getJSON(`/api/${name}?day=${encodeURIComponent(day)}`);
        delete state.errors[name];
        RENDER[name]();
      } catch (e) {
        state.errors[name] = e.message || String(e);
      }
    }));
    state.loading = false;
    const failed = Object.keys(state.errors);
    const partial = names.filter((n) => state.data[n]?.quality?.state === 'partial');
    const now = new Date().toLocaleTimeString('de-DE', { timeZone: 'Europe/Berlin' });
    if (failed.length) setStatus('bad', `Stand ${now} · Fehler: ${failed.join(', ')} (${state.errors[failed[0]]})`);
    else if (partial.length) setStatus('warn', `Stand ${now} · teilweise: ${partial.join(', ')}`);
    else setStatus('ok', `Stand ${now} · alle Quellen vollständig`);
    state.countdown = REFRESH_S;
  }

  function setStatus(cls, text) {
    $('statusDot').className = 'dot ' + cls;
    $('statusText').textContent = text + (isLive() && $('auto').checked && !state.loading ? ` · nächstes Update in ${Math.max(0, state.countdown)} s` : '');
    state.lastStatus = [cls, text];
  }
  const isLive = () => ($('day').value || todayBerlin()) === todayBerlin();

  function tick() {
    if (!$('auto').checked || !isLive() || state.loading) return;
    state.countdown -= 5;
    if (state.countdown <= 0) loadAll();
    else if (state.lastStatus) setStatus(...state.lastStatus);
  }

  function segment(id, key, after) {
    $(id).addEventListener('click', (ev) => {
      const b = ev.target.closest('button[data-v]');
      if (!b) return;
      $(id).querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      state[key] = b.dataset.v;
      after();
    });
  }

  async function init() {
    $('day').value = todayBerlin();
    $('day').max = new Date(Date.now() + 2 * 864e5).toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
    $('day').addEventListener('change', () => { $('flowBorder').length = 1; loadAll(); });
    $('today').addEventListener('click', () => { $('day').value = todayBerlin(); loadAll(); });
    $('refresh').addEventListener('click', loadAll);
    $('flowBorder').addEventListener('change', renderFlow);
    ['fZone', 'fType', 'fState'].forEach((id) => $(id).addEventListener('change', renderNotices));
    $('fText').addEventListener('input', renderNotices);
    segment('resTech', 'resTech', renderRes);
    segment('outZone', 'outZone', renderOut);
    window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', () => Object.values(RENDER).forEach((f) => f()));
    try {
      const h = await getJSON('/health', 30000);
      $('authChip').classList.toggle('hidden', !!h.auth_enabled);
      $('version').textContent = `· v${h.version}${h.netztransparenz ? ' · netztransparenz.de aktiv' : ''}`;
    } catch (_) { /* panels report their own errors */ }
    setInterval(tick, 5000);
    loadAll();
  }
  document.addEventListener('DOMContentLoaded', init);
})();

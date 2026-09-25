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
  // "MTU 17:30 · 40 min alt": age = now − end of that quarter-hour. The status
  // bar only says when the SERVER built the panel; this says how old the data is.
  const ageText = (end) => {
    const mins = Math.round((Date.now() - end) / 60e3);
    return Number.isFinite(mins) && mins >= 0 ? `· ${mins} min alt` : '';
  };
  const mtu = (iso) => {
    if (!iso) return 'MTU —';
    const live = ($('day').value || todayBerlin()) === todayBerlin();
    const end = Date.parse(iso) + 900e3;
    // data-end lets tick() keep the age current between panel reloads.
    return live && Number.isFinite(end)
      ? `MTU ${hhmm(iso)} <span class="muted age" data-end="${end}" title="Minuten seit Ende dieser Viertelstunde">${ageText(end)}</span>`
      : `MTU ${hhmm(iso)}`;
  };
  const refreshAges = () => document.querySelectorAll('.age[data-end]').forEach((el) => { el.textContent = ageText(Number(el.dataset.end)); });
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Plotly ignores UTC offsets in date strings. Berlin wall-clock strings would
  // collide on the DST fall-back day (02:00–02:59 happens twice) and draw two
  // MTUs on top of each other. So x is plotted in UTC; tick labels and the
  // hover time are rendered in Europe/Berlin (see timeTicks / withBerlinTime).
  const utcX = (iso) => new Date(iso).toISOString().slice(0, 19);
  const xs = (pts) => (pts || []).map((p) => utcX(p.t));
  const ys = (pts) => (pts || []).map((p) => p.v);
  const BERLIN_PARTS = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  const berlinParts = (d) => Object.fromEntries(BERLIN_PARTS.formatToParts(d).filter((p) => p.type !== 'literal').map((p) => [p.type, p.value]));
  const berlinLabel = (x) => {
    const d = new Date(`${x}Z`);
    // MEZ/MESZ suffix keeps the repeated hour on the DST fall-back day unambiguous.
    return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin', timeZoneName: 'short' });
  };
  // Every full `stepH` hours of the selected Berlin delivery day, as UTC x values.
  function timeTicks(day, stepH) {
    const [y, m, dd] = day.split('-').map(Number);
    const vals = [], text = [];
    for (let t = Date.UTC(y, m - 1, dd) - 3 * 3600e3; t <= Date.UTC(y, m - 1, dd) + 27 * 3600e3; t += 900e3) {
      const p = berlinParts(new Date(t));
      if (`${p.year}-${p.month}-${p.day}` !== day || p.minute !== '00' || Number(p.hour) % stepH) continue;
      vals.push(new Date(t).toISOString().slice(0, 19));
      text.push(`${p.hour}:00`);
    }
    return { tickmode: 'array', tickvals: vals, ticktext: text };
  }
  // Invisible companion traces (one per subplot, because unified hover only
  // lists traces of the hovered subplot): first hover row = Berlin time.
  // They reuse existing (x, y) pairs so they never change the autorange.
  function withBerlinTime(traces) {
    const byAxis = {};
    traces.forEach((t) => {
      const pts = (byAxis[t.yaxis || 'y'] ||= new Map());
      (t.x || []).forEach((xv, i) => {
        const yv = t.y?.[i];
        if (yv !== null && yv !== undefined && !pts.has(xv)) pts.set(xv, yv);
      });
    });
    const helpers = Object.entries(byAxis).filter(([, pts]) => pts.size).map(([axis, pts]) => {
      const x = [...pts.keys()].sort();
      return { type: 'scatter', mode: 'markers', x, y: x.map((xv) => pts.get(xv)), yaxis: axis, name: '', showlegend: false,
        customdata: x.map(berlinLabel), marker: { opacity: 0, size: 1 }, hovertemplate: '<b>%{customdata}</b><extra></extra>' };
    });
    return [...helpers, ...traces];
  }
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
    // Exact zeros belong to neither side (e.g. A86 "balanced"); they would be invisible anyway.
    const p = (pts || []).map((q) => ({ t: q.t, v: q.v > 0 ? q.v : null }));
    const n = (pts || []).map((q) => ({ t: q.t, v: q.v < 0 ? q.v : null }));
    return [
      // Pre-formatted (de-DE, explicit sign): Plotly's '%{y:+,.0f}' is not applied in unified hover and printed raw values like '-58.431'.
      { type: 'bar', x: xs(p), y: ys(p), customdata: p.map((q) => sgn(q.v)), name: `${name}: ${names[0]}`, yaxis, marker: { color: pos }, hovertemplate: `%{customdata} ${unit}`, showlegend: false },
      { type: 'bar', x: xs(n), y: ys(n), customdata: n.map((q) => sgn(q.v)), name: `${name}: ${names[1]}`, yaxis, marker: { color: neg }, hovertemplate: `%{customdata} ${unit}`, showlegend: false },
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
    const day = $('day').value || todayBerlin();
    const ticks = timeTicks(day, (el.clientWidth || 800) < 560 ? 4 : 2);
    // The unified-hover title would show the UTC x value; the Berlin time row
    // from withBerlinTime replaces it.
    layout.xaxis = Object.assign({}, layout.xaxis, ticks, { unifiedhovertitle: { text: ' ' } });
    Plotly.react(el, withBerlinTime(traces), layout, PLOT_CFG);
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
      foot: `<span>${mtu(k.as_of)}</span><span title="Mittel der letzten 4 MTUs; die jüngste MTU ist noch eine Schätzung">Ø 1 h ${sgn(k.res_error_1h_mw)} MW</span><span>Ø Tag ${sgn(k.day_avg_error_mw)} MW</span>${deltas(k.changes)}`,
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
      foot: `<span>${mtu(k.surprise_as_of)}</span><span>Ø 1 h ${sgn(k.residual_surprise_1h_mw)} MW</span><span>Ø Tag ${sgn(k.day_avg_surprise_mw)} MW</span>`,
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
      sub: `Intraday-XB <b>${sgn(k.intraday_xb_mw)}</b> · Phys. − Fahrplan <b>${sgn(k.unscheduled_mw)}</b><br>DA-Fahrplan <b>${sgn(k.da_schedule_mw)}</b> MW`,
      foot: `<span>${mtu(k.as_of)}</span><span>${cov.physical_series ?? 0}/${cov.expected ?? 0} Grenzen</span>${flags.length ? `<span class="flag">⚠ ${flags.map((f) => BORDER_NAMES[f.border] || f.border).join(', ')} 0 MW</span>` : ''}`,
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
    $('tFlow').innerHTML = `<thead><tr><th>Grenze</th><th class="num">Physisch</th><th class="num">DA</th><th class="num">Gesamt</th><th class="num">Intraday-XB</th><th class="num" title="Pro Grenze: v. a. Ring-/Transitflüsse (Core: Fahrplan ist rechnerische Zerlegung). Summe: Ringflüsse heben sich auf; Rest = Redispatch/Countertrading, Regelenergie, Datenabweichungen – kein Handelssignal.">Phys. − Fahrplan</th><th>Hinweis</th></tr></thead><tbody>` +
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
    // Headline = freshest of A86 (x4) and −NRV-Saldo (same quantity, r ≈ −0.999);
    // netztransparenz is usually 15–30 min ahead of the A86 German sum.
    const nowState = k.system_now_state || k.imbalance_state;
    const short = nowState === 'deficit', long = nowState === 'surplus';
    const fromNrv = (k.system_now_source || '').startsWith('NRV');
    const price = k.price_mode === 'single' ? k.imbalance_price_eur_mwh : k.imbalance_price_short_eur_mwh;
    // Same-day reBAP is an operational estimate; the settled price comes later.
    const prelim = k.price_status !== 'final';
    const act = isNum(k.afrr_de_mw) ? `aFRR DE <b>${sgn(k.afrr_de_mw)}</b> MW`
      : isNum(k.afrr_partial_mw) ? `aFRR <b>${sgn(k.afrr_partial_mw)}</b> MW <span class="muted">(ohne ${esc((k.afrr_missing_areas || []).join(', '))})</span>` : 'aFRR —';
    setSig('sigSys', {
      value: isNum(k.system_now_mw) ? `${sgn(k.system_now_mw)}<small>MW</small>` : '—',
      sub: `${short ? 'System kurz' : long ? 'System lang' : nowState === 'balanced' ? 'ausgeglichen' : '—'} · reBAP${prelim ? ' (vorl.)' : ''} <b>${fmt(price, NF2)}</b> €/MWh<br>A86 ${hhmm(k.as_of)}: <b>${sgn(k.imbalance_volume_mwh)}</b> MWh · ${act}`,
      foot: `<span>${mtu(k.system_now_as_of || k.as_of)}</span><span title="${fromNrv ? 'Bilanz = −NRV-Saldo (netztransparenz.de); A86 folgt später' : 'Bilanz = A86 × 4'}">${fromNrv ? 'aus NRV-Saldo' : 'aus A86'}</span><span title="${k.imbalance_nowcast_points ? 'letzte Stunde inkl. vorläufiger Viertelstunden aus netztransparenz' : 'letzte Stunde aus A86'}">Ø 1 h ${sgn(k.imbalance_1h_avg_mw)} MW${k.imbalance_nowcast_points ? '*' : ''}</span>${deltas(k.system_now_changes || k.changes)}`,
      tag: short ? { cls: 'bull', text: 'kurz' } : long ? { cls: 'bear', text: 'lang' } : null,
    });

    const traces = [
      ...bars(s['Net imbalance volume'], 'Bilanz', 'y', 'MWh', ['lang', 'kurz']).map((t, i) => Object.assign(t, { showlegend: true, name: i === 0 ? 'System lang (MWh)' : 'System kurz (MWh)' })),
    ];
    // Latest quarter-hours before the A86 sum is complete: same TSO figure
    // from netztransparenz (−RZ-Saldo ÷ 4), drawn hatched and pale.
    const nowcast = s['Net imbalance volume nowcast'] || [];
    if (nowcast.length) {
      bars(nowcast, 'Bilanz vorläufig', 'y', 'MWh (vorläufig, netztransparenz)', ['lang', 'kurz']).forEach((t, i) => traces.push(Object.assign(t, {
        name: 'vorläufig (netztransparenz)', showlegend: i === 0, legendgroup: 'nowcast', opacity: 0.55,
        marker: Object.assign({}, t.marker, { pattern: { shape: '/', solidity: 0.35 } }),
      })));
    }
    if (k.price_mode === 'single' || !(s['Imbalance price long'] || []).length) {
      traces.push(line(s['Imbalance price'], prelim ? 'reBAP €/MWh (vorläufig)' : 'reBAP €/MWh', cssVar('--s2'), 'solid', 'y2', { hovertemplate: '%{y:,.2f} €/MWh' }));
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
      metric(prelim ? 'reBAP jetzt (vorläufig)' : 'reBAP jetzt', `${fmt(price, NF2)} €/MWh`, `MTU ${hhmm(k.price_as_of)}${prelim ? ' · Schätzung, Abrechnungspreis folgt' : ' · final'}`),
      metric('reBAP Ø / Max / Min', `${fmt(k.day_avg_price_eur_mwh)} / ${fmt(k.day_max_price_eur_mwh)} / ${fmt(k.day_min_price_eur_mwh)}`, '€/MWh seit 00:00'),
      metric('aFRR netto', isNum(k.afrr_de_mw) ? `${sgn(k.afrr_de_mw)} MW` : isNum(k.afrr_partial_mw) ? `${sgn(k.afrr_partial_mw)} MW*` : '—', esc(k.activation_source || 'keine Quelle')),
      metric('NRV-Saldo', isNum(k.nrv_saldo_mw) ? `${sgn(k.nrv_saldo_mw)} MW` : '—', isNum(k.nrv_saldo_mw) ? `${k.nrv_state === 'deficit' ? 'Unterdeckung' : k.nrv_state === 'surplus' ? 'Überdeckung' : 'ausgeglichen'} · MTU ${hhmm(k.nrv_as_of)}` : 'netztransparenz.de nicht konfiguriert'),
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

  const PANELS = Object.keys(RENDER);
  const FRESHNESS_POLL_S = 30;

  async function loadPanels(names = PANELS) {
    const day = $('day').value || todayBerlin();
    await Promise.all(names.map(async (name) => {
      try {
        state.data[name] = await getJSON(`/api/${name}?day=${encodeURIComponent(day)}`);
        delete state.errors[name];
        RENDER[name]();
      } catch (e) {
        state.errors[name] = e.message || String(e);
      }
    }));
  }

  function updateStatus() {
    const failed = Object.keys(state.errors);
    const partial = PANELS.filter((n) => state.data[n]?.quality?.state === 'partial');
    // Last time the server rebuilt any panel; per-tile "min alt" shows data age.
    const built = PANELS.map((n) => Date.parse(state.data[n]?.updated)).filter(Number.isFinite);
    const stamp = built.length ? `Server-Stand ${new Date(Math.max(...built)).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' })}` : 'Server-Stand —';
    if (failed.length) setStatus('bad', `${stamp} · Fehler: ${failed.join(', ')} (${state.errors[failed[0]]})`);
    else if (partial.length) setStatus('warn', `${stamp} · teilweise: ${partial.join(', ')}`);
    else setStatus('ok', `${stamp} · alle Quellen vollständig`);
  }

  async function loadAll() {
    if (state.loading) return;
    state.loading = true;
    setStatus('warn', 'lädt…');
    await loadPanels();
    state.loading = false;
    state.countdown = state.background ? FRESHNESS_POLL_S : REFRESH_S;
    updateStatus();
  }

  // With the server-side refresher, poll a tiny endpoint and re-download only
  // the panels the server has rebuilt. Without it, fall back to full reloads.
  async function pollFreshness() {
    try {
      const f = await getJSON('/api/freshness', 20000);
      state.background = !!f.background;
      if (f.date !== ($('day').value || todayBerlin())) return loadAll();
      const changed = PANELS.filter((n) => f.panels?.[n] && f.panels[n] !== state.data[n]?.updated);
      if (changed.length) {
        state.loading = true;
        await loadPanels(changed);
        state.loading = false;
      }
      updateStatus();
    } catch (_) { /* keep the current view; next poll retries */ }
  }

  function setStatus(cls, text) {
    $('statusDot').className = 'dot ' + cls;
    const next = isLive() && $('auto').checked && !state.loading
      ? (state.background ? ` · live, Prüfung in ${Math.max(0, state.countdown)} s` : ` · nächstes Update in ${Math.max(0, state.countdown)} s`)
      : '';
    $('statusText').textContent = text + next;
    state.lastStatus = [cls, text];
  }
  const isLive = () => ($('day').value || todayBerlin()) === todayBerlin();

  function tick() {
    refreshAges();
    if (!$('auto').checked || !isLive() || state.loading) return;
    state.countdown -= 5;
    if (state.countdown <= 0) {
      state.countdown = state.background ? FRESHNESS_POLL_S : REFRESH_S;
      if (state.background) pollFreshness(); else loadAll();
    } else if (state.lastStatus) setStatus(...state.lastStatus);
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

  // ---------- theme ----------
  // Default follows the OS; a click stores an explicit choice per browser.
  // The inline script in <head> applies the stored choice before first paint.
  const THEME_KEY = 'dpd-theme';
  const systemTheme = () => (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  const storedTheme = () => { try { const t = localStorage.getItem(THEME_KEY); return t === 'light' || t === 'dark' ? t : null; } catch (_) { return null; } };
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const btn = $('theme');
    const label = theme === 'dark' ? 'Helles Design' : 'Dunkles Design';
    btn.title = label; btn.setAttribute('aria-label', label);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#111110' : '#f3f2ee');
    Object.values(RENDER).forEach((f) => f());  // Plotly reads colors at render time
  }

  async function init() {
    applyTheme(storedTheme() || systemTheme());
    $('theme').addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* private mode: still switch for this view */ }
      applyTheme(next);
    });
    $('year').textContent = new Date().toLocaleDateString('de-DE', { year: 'numeric', timeZone: 'Europe/Berlin' });
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
    window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', () => { if (!storedTheme()) applyTheme(systemTheme()); });
    try {
      const h = await getJSON('/health', 30000);
      $('authChip').classList.toggle('hidden', !!h.auth_enabled || !!h.public_ok);
      $('version').textContent = `· v${h.version}${h.netztransparenz ? ' · netztransparenz.de aktiv' : ''}`;
      state.background = !!h.background_refresh;
    } catch (_) { /* panels report their own errors */ }
    setInterval(tick, 5000);
    loadAll();
  }
  document.addEventListener('DOMContentLoaded', init);
})();

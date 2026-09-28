/* DE Power Desk v5 — front end. No build step, Plotly bundled locally. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const REFRESH_S = Math.max(60, parseInt(document.body.dataset.refresh, 10) || 300);

  // ---------- language ----------
  // <html lang> is set in <head> before first paint (stored choice, else the
  // browser language). Static text exists in both languages in the page
  // (data-lang, CSS shows one); generated text comes from TXT via tr().
  const LANG_KEY = 'dpd-lang';
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'de');
  const LOCALE = () => (lang() === 'en' ? 'en-GB' : 'de-DE');
  let NF0, NF2;
  function setNumberFormats() {
    NF0 = new Intl.NumberFormat(LOCALE(), { maximumFractionDigits: 0 });
    NF2 = new Intl.NumberFormat(LOCALE(), { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  }
  setNumberFormats();
  const TXT = {
    // status bar, header
    loading: ['lädt…', 'loading…'], asOf: ['Stand {t}', 'As of {t}'], errorIn: ['Fehler: {p}', 'Error: {p}'],
    incompleteIn: ['{p} unvollständig', '{p} incomplete'],
    incompleteInfo: ['Mindestens eine Quelle ist unvollständig; Details stehen im jeweiligen Panel.', 'At least one source is incomplete; see the panel for details.'],
    allComplete: ['Alle Quellen vollständig', 'All sources complete'], liveNext: ['Live: nächste Prüfung in {s} s', 'Live: next check in {s} s'],
    nextUpdate: ['Nächstes Update in {s} s', 'Next update in {s} s'], liveOff: ['Live aus', 'Live off'],
    themeLight: ['Helles Design', 'Light theme'], themeDark: ['Dunkles Design', 'Dark theme'], ntpActive: ['netztransparenz.de aktiv', 'netztransparenz.de active'],
    noData: ['Keine Daten für diese Auswahl veröffentlicht.', 'No data published for this selection.'], toggleKey: ['ein-/ausblenden', 'show / hide'],
    minAgo: ['· {n} min alt', '· {n} min ago'], minAgoTitle: ['Minuten seit Ende dieser Viertelstunde', 'Minutes since the end of this quarter-hour'],
    // signal tiles
    avg1h: ['Ø 1 h', '1 h avg'], avgDay: ['Ø Tag', 'day avg'],
    avg1hTitle: ['Mittel der letzten 4 MTUs; die jüngste MTU ist noch eine Schätzung', 'Mean of the last 4 MTUs; the latest MTU is still an estimate'],
    vsId: ['vs. Intraday 08:00', 'vs. intraday 08:00'], range: ['Spanne', 'range'], to: ['bis', 'to'], restOfDay: ['Rest des Tages Ø', 'rest of day avg'],
    revNone: ['Nur für den laufenden Tag, sobald eine laufende Prognose (A18) vollständig ist.', 'Only for the current day, once a complete latest forecast (A18) is available.'],
    latestMinusDa: ['laufende Prognose − DA', 'latest forecast − DA'], residualLoad: ['Residuallast', 'Residual load'], loadActMinusDa: ['Last Ist − DA', 'Load actual − DA'],
    physMinusSched: ['Phys. − Fahrplan', 'Phys. − schedule'], daSched: ['DA-Fahrplan', 'DA schedule'], bordersN: ['{n}/{m} Grenzen', '{n}/{m} borders'],
    ofWhichUnplanned: ['davon ungeplant', 'of which unplanned'], unplannedDelta: ['ungeplant Δ', 'unplanned Δ'],
    newChanged: ['neu/geändert 24 h: {n} ({m} ungeplant)', 'new/updated 24 h: {n} ({m} unplanned)'], incompleteFlag: ['⚠ unvollständig', '⚠ incomplete'],
    sysShort: ['System kurz', 'System short'], sysLong: ['System lang', 'System long'], balanced: ['ausgeglichen', 'balanced'],
    rebapPrel: ['reBAP (vorl.)', 'reBAP (prel.)'], officialAt: ['offiziell {t}', 'official {t}'], official: ['offiziell', 'official'], prelim: ['vorläufig', 'preliminary'],
    excl: ['ohne', 'excl.'], tagShort: ['kurz', 'short'], tagLong: ['lang', 'long'],
    fromNrvTitle: ['Wert der Netzbetreiber (netztransparenz.de); der offizielle ENTSO-E-Wert folgt', 'TSO figure (netztransparenz.de); the official ENTSO-E value follows'],
    fromA86Title: ['offizieller ENTSO-E-Wert (A86)', 'official ENTSO-E value (A86)'],
    h1Nowcast: ['letzte Stunde inkl. vorläufiger Viertelstunden', 'last hour incl. preliminary quarter-hours'], h1A86: ['letzte Stunde aus A86', 'last hour from A86'],
    // chart heads and keys
    actual: ['Ist', 'Actual'], dayAhead: ['Day-Ahead', 'Day-ahead'], id0800: ['Intraday 08:00', 'Intraday 08:00'], latest: ['laufend', 'latest'],
    techRES: ['Wind und Solar', 'Wind and solar'], resDevTitle: ['Abweichung Ist − Day-Ahead', 'Error: actual − day-ahead'],
    aboveFc: ['über Prognose', 'above forecast'], belowFc: ['unter Prognose', 'below forecast'],
    loadAct: ['Last Ist', 'Load actual'], loadDa: ['Last Day-Ahead', 'Load day-ahead'], residAct: ['Residuallast Ist', 'Residual load actual'],
    residDa: ['Residuallast Day-Ahead', 'Residual load day-ahead'], loadTitle: ['Last und Residuallast', 'Load and residual load'],
    residDevTitle: ['Residuallast Ist − Day-Ahead', 'Residual load: actual − day-ahead'], higherFc: ['höher als Prognose', 'above forecast'], lowerFc: ['niedriger als Prognose', 'below forecast'],
    physical: ['Physisch', 'Physical'], daSchedule: ['Day-Ahead-Fahrplan', 'Day-ahead schedule'], totalSchedule: ['Gesamtfahrplan inkl. Intraday', 'Total schedule incl. intraday'],
    netImportAll: ['Netto-Import, alle Grenzen', 'Net import, all borders'], border: ['Grenze {b}', 'Border {b}'], flowNote: ['MW · + Import, − Export', 'MW · + import, − export'],
    outTitle: ['Nicht verfügbare Leistung', 'Unavailable capacity'],
    sysTitle: ['Systembilanz', 'System balance'], sysNote: ['MW · + lang, − kurz', 'MW · + long, − short'],
    priceDual: ['Ausgleichsenergiepreis', 'Imbalance price'], priceEst: [' · Schätzung der Netzbetreiber, Abrechnung folgt', ' · TSO estimate, settled price follows'],
    priceLong: ['Preis lang', 'Price long'], priceShort: ['Preis kurz', 'Price short'],
    regTitle: ['Regelenergie', 'Balancing energy'], regNote: ['MW · + = hochregeln (System kurz)', 'MW · + = upward regulation (system short)'], nrv: ['NRV-Saldo', 'NRV balance'],
    // stat tiles
    errNow: ['Abweichung jetzt', 'Error now'], actMinusDaAt: ['Ist − Day-Ahead · {t}', 'vs. day-ahead · {t}'], mae: ['Mittlere Abweichung', 'Mean abs. error'],
    maeSub: ['Betrag, seit 00:00', 'absolute, since 00:00'], maxErr: ['Größte Abweichung', 'Largest error'], since0: ['seit 00:00', 'since 00:00'],
    actThrough: ['Ist-Daten bis', 'Actuals through'], lastComplete: ['zuletzt vollständig', 'last complete'],
    residNow: ['Residuallast jetzt', 'Residual load now'], loadFcErr: ['Lastprognosefehler', 'Load forecast error'], loadActMinusDaLong: ['Last Ist − Day-Ahead', 'load vs. day-ahead'],
    meanErr: ['Abweichung Ø', 'Mean error'], residSince0: ['Residuallast seit 00:00', 'residual, since 00:00'], peakLoad: ['Lastspitze', 'Peak load'],
    sysNow: ['Systembilanz jetzt', 'System balance now'], rebapNow: ['reBAP jetzt', 'reBAP now'], estimate: ['Schätzung', 'estimate'], final: ['final', 'final'],
    rebapToday: ['reBAP heute', 'reBAP today'], avgMaxMin: ['Ø / Max / Min €/MWh', 'avg / max / min €/MWh'], regNow: ['Regelenergie jetzt', 'Balancing energy now'],
    // tables
    thBorder: ['Grenze', 'Border'], thTotal: ['Gesamt', 'Total'], thNote: ['Hinweis', 'Note'], total: ['Summe', 'Total'],
    physSchedTitle: ['Pro Grenze: v. a. Ring-/Transitflüsse (Core: Fahrplan ist rechnerische Zerlegung). Summe: Ringflüsse heben sich auf; Rest = Redispatch/Countertrading, Regelenergie, Datenabweichungen – kein Handelssignal.',
      'Per border: mostly loop/transit flows (Core: the schedule is an arithmetic split). Total: loop flows cancel out; the rest is redispatch/countertrading, balancing, data differences – not a trading signal.'],
    flag0All: ['0 MW ganztägig – Ausfall/Wartung?', '0 MW all day – outage/maintenance?'], flag0Phys: ['0 MW physisch trotz Fahrplan', '0 MW physical despite schedule'],
    covOf: ['{name}: {n} von {m} Grenzen', '{name}: {n} of {m} borders'], totalSched: ['Gesamtfahrplan', 'Total schedule'],
    thUnavail: ['Nicht verfügbar', 'Unavailable'], thUnplanned: ['Ungeplant', 'Unplanned'], thPlanned: ['Geplant', 'Planned'], thUnplDelta: ['Ungeplant Δ 24 h', 'Unplanned Δ 24 h'],
    thActive: ['Aktive Meldungen', 'Active notices'], thData: ['Daten', 'Data'], unitPlant: ['Block- und Anlagenmeldungen', 'unit and plant notices'], unitOnly: ['Blockmeldungen', 'unit notices'],
    plantsWithout: [', davon {n} Anlagen ohne Blockmeldung', ', incl. {n} plants without a unit notice'], noDataFlag: ['keine Daten', 'no data'], complete: ['✓ vollständig', '✓ complete'],
    thPublished: ['Veröffentlicht', 'Published'], thPlant: ['Anlage', 'Plant'], thFuel: ['Brennstoff', 'Fuel'], thType: ['Art', 'Type'], thPeriod: ['Zeitraum', 'Period'],
    thNominal: ['Nenn-MW', 'Nominal MW'], thDuration: ['Dauer', 'Duration'], thReason: ['Grund', 'Reason'],
    unplanned: ['ungeplant', 'unplanned'], planned: ['geplant', 'planned'], unknown: ['unbekannt', 'unknown'], open: ['offen', 'open'],
    active: ['aktiv', 'active'], upcoming: ['kommt', 'upcoming'], ended: ['beendet', 'ended'],
    noRecent: ['Keine neuen oder geänderten Meldungen in den letzten 24 h.', 'No new or updated notices in the last 24 h.'],
    countNotices: ['{n} von {m} Meldungen · MW je Meldung, bei Überlappung nicht addierbar', '{n} of {m} notices · MW per notice, not additive where they overlap'],
    lt30: ['< 30 Tage', '< 30 days'], ge30: ['≥ 30 Tage', '≥ 30 days'],
  };
  const tr = (key, vars) => {
    const s = TXT[key] ? TXT[key][lang() === 'en' ? 1 : 0] : key;
    return vars ? s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '') : s;
  };
  const BORDER_NAMES = { FR: 'FR', NL: 'NL', BE: 'BE', DK_1: 'DK1', DK_2: 'DK2', AT: 'AT', CH: 'CH', CZ: 'CZ', PL: 'PL', SE_4: 'SE4', NO_2: 'NO2' };
  const ZONE_NAMES = { DE_LU: 'DE-LU', FR: 'FR', NL: 'NL', BE: 'BE' };
  const OUT_ZONES = ['DE_LU', 'FR', 'NL', 'BE'];
  // ENTSO-E production types (API names are English; German shown in de).
  const FUEL_DE = { Biomass: 'Biomasse', Lignite: 'Braunkohle', 'Coal gas': 'Kokereigas', Gas: 'Erdgas', 'Hard coal': 'Steinkohle', Oil: 'Öl',
    'Oil shale': 'Ölschiefer', Peat: 'Torf', Geothermal: 'Geothermie', 'Pumped storage': 'Pumpspeicher', 'Run-of-river': 'Laufwasser',
    'Hydro reservoir': 'Speicherwasser', Marine: 'Meeresenergie', Nuclear: 'Kernenergie', 'Other RES': 'Sonstige erneuerbare', Solar: 'Solar',
    Waste: 'Abfall', 'Wind offshore': 'Wind offshore', 'Wind onshore': 'Wind onshore', Other: 'Sonstige', Battery: 'Batterie' };
  const fuel = (f) => esc((lang() === 'de' && FUEL_DE[f]) || f || '');
  const DOCS = { de: { A80: 'Blockmeldungen', A77: 'Anlagenmeldungen' }, en: { A80: 'Unit notices', A77: 'Plant notices' } };
  const STATES = {
    de: { ok: 'ok', partial: 'unvollständig', no_data: 'keine Daten', empty: 'keine Daten', error: 'Fehler', not_configured: 'nicht konfiguriert' },
    en: { ok: 'ok', partial: 'incomplete', no_data: 'no data', empty: 'no data', error: 'error', not_configured: 'not configured' },
  };
  const stateName = (v) => STATES[lang()][v] || v;
  const PANEL_NAMES = {
    de: { renewables: 'EE', load: 'Last', borders: 'Grenzen', outages: 'Kraftwerke', balancing: 'Systembilanz' },
    en: { renewables: 'RES', load: 'Load', borders: 'Borders', outages: 'Plants', balancing: 'System balance' },
  };
  const state = { data: {}, errors: {}, hidden: {}, zoom: {}, resTech: 'RES', timer: null, countdown: REFRESH_S, loading: false };

  // ---------- formatting ----------
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const fmt = (v, nf = NF0) => (isNum(v) ? nf.format(v) : '—');
  const sgn = (v, nf = NF0) => (isNum(v) ? (v > 0 ? '+' : v < 0 ? '−' : '±') + nf.format(Math.abs(v)) : '—');
  const mw = (v, signed = true) => (isNum(v) ? `${signed ? sgn(v) : fmt(v)}<small>MW</small>` : '—');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hhmm = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString(LOCALE(), { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Europe/Berlin' });
  };
  const dayTime = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString(LOCALE(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Europe/Berlin' });
  };
  const todayBerlin = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
  // "MTU 17:30 · 40 min alt": age = now − end of that quarter-hour. The status
  // bar only says when the SERVER built the panel; this says how old the data is.
  const ageText = (end) => {
    const mins = Math.round((Date.now() - end) / 60e3);
    return Number.isFinite(mins) && mins >= 0 ? tr('minAgo', { n: mins }) : '';
  };
  const mtu = (iso) => {
    if (!iso) return 'MTU —';
    const live = ($('day').value || todayBerlin()) === todayBerlin();
    const end = Date.parse(iso) + 900e3;
    // data-end lets tick() keep the age current between panel reloads.
    return live && Number.isFinite(end)
      ? `MTU ${hhmm(iso)} <span class="muted age" data-end="${end}" title="${tr('minAgoTitle')}">${ageText(end)}</span>`
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
    // MEZ/MESZ (CET/CEST) suffix keeps the repeated hour on the DST fall-back day unambiguous.
    return d.toLocaleString(LOCALE(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Europe/Berlin', timeZoneName: 'short' });
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
      margin: { l: 56, r: 12, t: 8, b: 30 },
      showlegend: false,  // keys live in the HTML chart head
      hovermode: 'x unified',
      hoverlabel: { bgcolor: cssVar('--surface-2'), bordercolor: cssVar('--line'), font: { color: cssVar('--text') } },
      barcornerradius: 2,
      xaxis: { type: 'date', tickformat: '%H:%M', hoverformat: '%d.%m. %H:%M', gridcolor: grid, linecolor: grid, zeroline: false, color: muted },
      yaxis: { gridcolor: grid, zerolinecolor: cssVar('--line'), ticksuffix: '', color: muted, separatethousands: true, tickformat: ',.0f', fixedrange: true },
      separators: lang() === 'en' ? '.,' : ',.',
    }, extra);
  }
  const line = (pts, name, color, dash = 'solid', yaxis = 'y', extra = {}) => Object.assign({
    type: 'scatter', mode: 'lines', x: xs(pts), y: ys(pts), name, yaxis,
    line: { color, width: 2, dash, shape: 'hv' }, hovertemplate: `%{y:,.0f} MW`,
  }, extra);
  // prelim: Set of timestamps whose bars are preliminary. Same traces with
  // per-point opacity (no extra traces, so every bar keeps its width and
  // position); the chart adds a quiet "vorläufig" band behind them.
  const PRELIM_OPACITY = 0.55;
  const bars = (pts, yaxis = 'y', unit = 'MW', names = [tr('aboveFc'), tr('belowFc')], prelim = null) => {
    const pos = cssVar('--pos'), neg = cssVar('--neg');
    // Exact zeros belong to neither side (e.g. A86 "balanced"); they would be invisible anyway.
    const p = (pts || []).map((q) => ({ t: q.t, v: q.v > 0 ? q.v : null }));
    const n = (pts || []).map((q) => ({ t: q.t, v: q.v < 0 ? q.v : null }));
    const isPre = (q) => !!prelim && prelim.has(q.t);
    const trace = (arr, color, label) => ({
      type: 'bar', x: xs(arr), y: ys(arr), name: label, yaxis,
      // Pre-formatted (de-DE, explicit sign): Plotly's '%{y:+,.0f}' is not applied in unified hover and printed raw values like '-58.431'.
      customdata: arr.map((q) => `${sgn(q.v)} ${unit}${isPre(q) ? ` · ${tr('prelim')}` : ''}`),
      hovertemplate: '%{customdata}',
      marker: prelim ? { color, opacity: arr.map((q) => (isPre(q) ? PRELIM_OPACITY : 1)) } : { color },
    });
    return [trace(p, pos, names[0]), trace(n, neg, names[1])];
  };
  // Each panel stacks single charts, each with an HTML head (title, unit,
  // keys). Charts of one panel share the x range and zoom together.
  function plot(id, traces, layout, { xLabels = true, range = null } = {}) {
    const el = $(id);
    if (!traces.some((t) => t.x && t.x.length)) {
      if (window.Plotly) Plotly.purge(el);
      delete el.dataset.sync;
      el.innerHTML = `<div class="empty">${tr('noData')}</div>`;
      return;
    }
    if (el.querySelector('.empty')) el.innerHTML = '';
    const day = $('day').value || todayBerlin();
    el.dataset.narrow = String(isNarrow(el));
    const hidden = state.hidden[id];
    if (hidden) traces.forEach((t) => { if (hidden.has(t.name)) t.visible = false; });
    // Tick spacing follows the plot area, not the card: 6 labels need ~40 px each.
    const m = layout.margin || {};
    const plotW = (el.clientWidth || 800) - (m.l ?? 56) - (m.r ?? 12);
    const ticks = timeTicks(day, plotW < 240 ? 6 : plotW < 490 ? 4 : 2);
    // A zoom (kept per chart group) survives the refreshes of the same day.
    const zoom = state.zoom[groupOf(id)[0]];
    if (zoom) range = zoom;
    // The unified-hover title would show the UTC x value; the Berlin time row
    // from withBerlinTime replaces it.
    layout.xaxis = Object.assign({}, layout.xaxis, ticks, { unifiedhovertitle: { text: ' ' }, showticklabels: xLabels },
      range ? { range, autorange: false } : {});
    if (!xLabels) layout.margin = Object.assign({}, m, { b: 6 });
    Plotly.react(el, withBerlinTime(traces), layout, PLOT_CFG).then(() => syncZoom(el));
  }
  const NARROW_PX = 560;
  const isNarrow = (el) => (el.clientWidth || 800) < NARROW_PX;
  // Common x range of one panel's charts (bars are centred on the MTU start).
  function xRange(...lists) {
    let lo = Infinity, hi = -Infinity;
    lists.flat().forEach((t) => (t.x || []).forEach((x, i) => {
      if (t.y?.[i] === null || t.y?.[i] === undefined) return;
      const v = Date.parse(`${x}Z`);
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }));
    return Number.isFinite(lo) ? [utcX(lo - 450e3), utcX(hi + 450e3)] : null;
  }
  // Drag to zoom the time axis; the charts of one panel follow each other,
  // double-click resets. The zoom is kept in state, so refreshes keep it.
  const SYNC = [['chRes', 'chResDiff'], ['chLoad', 'chLoadDiff'], ['chSys', 'chRebap', 'chReg']];
  const groupOf = (id) => SYNC.find((g) => g.includes(id)) || [id];
  let syncing = false;
  function syncZoom(el) {
    if (el.dataset.sync || !el.on) return;
    el.dataset.sync = '1';
    const group = groupOf(el.id);
    el.on('plotly_relayout', (ev) => {
      if (syncing) return;
      const r = 'xaxis.range[0]' in ev ? [ev['xaxis.range[0]'], ev['xaxis.range[1]']] : ev['xaxis.range'] || null;
      if (!r) return;
      state.zoom[group[0]] = r;
      syncing = true;
      Promise.all(group.filter((o) => o !== el.id && $(o)?.data).map((o) => Plotly.relayout($(o), { 'xaxis.range': r })))
        .finally(() => { syncing = false; });
    });
    el.on('plotly_doubleclick', () => {
      delete state.zoom[group[0]];
      RENDER[CHART_OWNER[el.id]]?.();
    });
  }

  // ---------- chart heads ----------
  // Title + unit on the left, keys on the right; line keys show the real
  // dash pattern and toggle their series. Wraps on phones like any text.
  const DASH = { solid: '', dash: '5 3', dot: '0.5 3.5', dashdot: '6 3 0.5 3' };
  function swatch(k) {
    if (k.kind === 'bar') {
      return `<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><rect width="10" height="10" rx="2" fill="${k.color}" fill-opacity="${k.opacity ?? 1}"/></svg>`;
    }
    const dash = DASH[k.dash || 'solid'];
    return `<svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true"><line x1="2" y1="5" x2="20" y2="5" stroke="${k.color}" stroke-width="2" stroke-linecap="round"${dash ? ` stroke-dasharray="${dash}"` : ''}/></svg>`;
  }
  const lk = (name, color, dash = 'solid') => ({ name, color, dash });
  const bk = (name, color, opacity = 1) => ({ name, color, opacity, kind: 'bar', toggle: false });
  function head(id, title, note = '', keys = []) {
    const el = $(id);
    let h = el.previousElementSibling;
    if (!h || !h.classList.contains('chead')) {
      h = document.createElement('div');
      h.className = 'chead';
      el.before(h);
    }
    const hidden = state.hidden[id] || new Set();
    const key = (k) => (k.toggle === false
      ? `<span class="key">${swatch(k)}${esc(k.name)}</span>`
      : `<button type="button" class="key" data-chart="${id}" data-name="${esc(k.name)}" aria-pressed="${!hidden.has(k.name)}" title="${tr('toggleKey')}">${swatch(k)}${esc(k.name)}</button>`);
    h.innerHTML = `<div class="ct"><b>${esc(title)}</b>${note ? `<span>${note}</span>` : ''}</div>`
      + (keys.length ? `<div class="ck">${keys.map(key).join('')}</div>` : '');
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
      sub: `Solar <b>${sgn(tech.Solar)}</b> · On <b>${sgn(tech['Wind Onshore'])}</b> · Off <b>${sgn(tech['Wind Offshore'])}</b><br>${tr('vsId')}: <b>${sgn(k.res_error_id_mw)}</b> MW`,
      foot: `<span>${mtu(k.as_of)}</span><span title="${tr('avg1hTitle')}">${tr('avg1h')} ${sgn(k.res_error_1h_mw)} MW</span><span>${tr('avgDay')} ${sgn(k.day_avg_error_mw)} MW</span>${deltas(k.changes)}`,
      tag: dir(k.res_error_mw, 300, 'bear'),
    });
    const hasFwd = isNum(k.next4h_revision_avg_mw);
    setSig('sigRev', {
      value: hasFwd ? mw(k.next4h_revision_avg_mw) : '—',
      sub: hasFwd ? `${tr('range')} <b>${sgn(k.next4h_revision_min_mw)}</b> ${tr('to')} <b>${sgn(k.next4h_revision_max_mw)}</b> MW<br>${tr('restOfDay')} <b>${sgn(k.rest_of_day_revision_avg_mw)}</b> MW`
        : tr('revNone'),
      foot: hasFwd ? `<span>${hhmm(k.next4h_window_start)}–${hhmm(k.next4h_window_end)}</span><span>${tr('latestMinusDa')}</span>` : `<span>${tr('latestMinusDa')}</span>`,
      tag: dir(k.next4h_revision_avg_mw, 300, 'bear'),
    });

    const s = d.series || {};
    const T = state.resTech;
    const pts = (suffix) => s[`${T} ${suffix}`] || [];
    const err = T === 'RES' ? s['RES Forecast Error'] : diff(pts('Actual'), pts('Day-ahead'));
    const C = ['--s1', '--s2', '--s3', '--s4'].map(cssVar);
    const series = [[tr('actual'), 'Actual', 'solid'], [tr('dayAhead'), 'Day-ahead', 'dash'], [tr('id0800'), 'Intraday', 'dot'], [tr('latest'), 'Current', 'dashdot']];
    const main = series.map(([name, key, dash], i) => line(pts(key), name, C[i], dash));
    const dev = bars(err);
    const range = xRange(main, dev);
    const techName = { RES: tr('techRES'), Solar: 'Solar', 'Wind Onshore': 'Wind onshore', 'Wind Offshore': 'Wind offshore' }[T] || T;
    head('chRes', techName, 'MW', series.map(([name, , dash], i) => lk(name, C[i], dash)));
    plot('chRes', main, baseLayout(), { xLabels: false, range });
    head('chResDiff', tr('resDevTitle'), 'MW', [bk(tr('aboveFc'), cssVar('--pos')), bk(tr('belowFc'), cssVar('--neg'))]);
    plot('chResDiff', dev, baseLayout({ bargap: 0.15 }), { range });
    const errVals = (err || []).map((p) => p.v);
    const mae = errVals.length ? errVals.reduce((a, v) => a + Math.abs(v), 0) / errVals.length : null;
    const maxAbs = errVals.length ? errVals.reduce((a, v) => (Math.abs(v) > Math.abs(a) ? v : a), 0) : null;
    $('mRes').innerHTML = [
      metric(tr('errNow'), `${sgn(T === 'RES' ? k.res_error_mw : tech[T])} MW`, tr('actMinusDaAt', { t: hhmm(k.as_of) })),
      metric(tr('mae'), `${fmt(mae)} MW`, tr('maeSub')),
      metric(tr('maxErr'), `${sgn(maxAbs)} MW`, tr('since0')),
      metric(tr('actThrough'), hhmm(d.freshness?.actual_through), tr('lastComplete')),
    ].join('');
  }

  function renderLoad() {
    const d = state.data.load;
    if (!d) return;
    const k = d.kpi || {}, s = d.series || {};
    setSig('sigResid', {
      value: mw(k.residual_surprise_mw),
      sub: `${tr('residualLoad')} <b>${fmt(k.residual_load_mw)}</b> MW<br>${tr('loadActMinusDa')} <b>${sgn(k.load_error_mw)}</b> MW`,
      foot: `<span>${mtu(k.surprise_as_of)}</span><span>${tr('avg1h')} ${sgn(k.residual_surprise_1h_mw)} MW</span><span>${tr('avgDay')} ${sgn(k.day_avg_surprise_mw)} MW</span>`,
      tag: dir(k.residual_surprise_mw, 500, 'bull'),
    });
    // Colour = quantity (Last / Residuallast), dash = Ist / Day-Ahead.
    const cL = cssVar('--s1'), cR = cssVar('--s3');
    const keys = [lk(tr('loadAct'), cL), lk(tr('loadDa'), cL, 'dash'), lk(tr('residAct'), cR), lk(tr('residDa'), cR, 'dash')];
    const main = [s['Load Actual'], s['Load Forecast'], s['Residual Load Actual'], s['Residual Load Forecast']]
      .map((pts, i) => line(pts, keys[i].name, keys[i].color, keys[i].dash));
    const dev = bars(s['Residual Load Surprise'], 'y', 'MW', [tr('higherFc'), tr('lowerFc')]);
    const range = xRange(main, dev);
    head('chLoad', tr('loadTitle'), 'MW', keys);
    plot('chLoad', main, baseLayout(), { xLabels: false, range });
    head('chLoadDiff', tr('residDevTitle'), 'MW', [bk(tr('higherFc'), cssVar('--pos')), bk(tr('lowerFc'), cssVar('--neg'))]);
    plot('chLoadDiff', dev, baseLayout({ bargap: 0.15 }), { range });
    const la = ys(s['Load Actual']);
    $('mLoad').innerHTML = [
      metric(tr('residNow'), `${fmt(k.residual_load_mw)} MW`, hhmm(k.as_of)),
      metric(tr('loadFcErr'), `${sgn(k.load_error_mw)} MW`, tr('loadActMinusDaLong')),
      metric(tr('meanErr'), `${sgn(k.day_avg_surprise_mw)} MW`, tr('residSince0')),
      metric(tr('peakLoad'), `${fmt(la.length ? Math.max(...la) : null)} MW`, tr('since0')),
    ].join('');
  }

  function renderFlow() {
    const d = state.data.borders;
    if (!d) return;
    const k = d.kpi || {}, s = d.series || {}, cov = d.coverage || {};
    const flags = d.flags || [];
    setSig('sigFlow', {
      value: mw(k.net_import_mw),
      sub: `Intraday <b>${sgn(k.intraday_xb_mw)}</b> · ${tr('physMinusSched')} <b>${sgn(k.unscheduled_mw)}</b><br>${tr('daSched')} <b>${sgn(k.da_schedule_mw)}</b> MW`,
      foot: `<span>${mtu(k.as_of)}</span><span>${tr('bordersN', { n: cov.physical_series ?? 0, m: cov.expected ?? 0 })}</span>${flags.length ? `<span class="flag">⚠ ${flags.map((f) => BORDER_NAMES[f.border] || f.border).join(', ')} 0 MW</span>` : ''}`,
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
    const keys = [lk(tr('physical'), cssVar('--s1')), lk(tr('daSchedule'), cssVar('--s2'), 'dash'), lk(tr('totalSchedule'), cssVar('--s3'), 'dot')];
    head('chFlow', b === 'TOTAL' ? tr('netImportAll') : tr('border', { b: BORDER_NAMES[b] || b }), tr('flowNote'), keys);
    plot('chFlow', [src.p, src.da, src.tot].map((pts, i) => line(pts, keys[i].name, keys[i].color, keys[i].dash)), baseLayout());

    const rows = (d.table || []).slice().sort((a, c) => Math.abs(c.physical ?? 0) - Math.abs(a.physical ?? 0));
    const flagText = { zero_flow_all_day: tr('flag0All'), zero_physical_with_schedule: tr('flag0Phys') };
    $('tFlow').innerHTML = `<thead><tr><th>${tr('thBorder')}</th><th class="num">${tr('physical')}</th><th class="num">${tr('dayAhead')}</th><th class="num">${tr('thTotal')}</th><th class="num">Intraday</th><th class="num" title="${tr('physSchedTitle')}">${tr('physMinusSched')}</th><th>${tr('thNote')}</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${esc(BORDER_NAMES[r.border] || r.border)}</td><td class="num">${sgn(r.physical)}</td><td class="num">${sgn(r.scheduled)}</td><td class="num">${sgn(r.total)}</td><td class="num">${sgn(r.intraday)}</td><td class="num">${sgn(r.unscheduled)}</td><td>${r.flag ? `<span class="flag">⚠ ${esc(flagText[r.flag] || r.flag)}</span>` : ''}</td></tr>`).join('') +
      `<tr><td><b>${tr('total')}</b></td><td class="num"><b>${sgn(k.net_import_mw)}</b></td><td class="num"><b>${sgn(k.da_schedule_mw)}</b></td><td class="num"><b>${sgn(k.total_schedule_mw)}</b></td><td class="num"><b>${sgn(k.intraday_xb_mw)}</b></td><td class="num"><b>${sgn(k.unscheduled_mw)}</b></td><td class="muted">MTU ${hhmm(k.as_of)}</td></tr></tbody>`;
    // Chips only for gaps; the full coverage sits under "Quellen & Methodik".
    const covItems = [[tr('physical'), cov.physical_series, cov.physical_total_complete], [tr('daSchedule'), cov.scheduled_series, cov.scheduled_total_complete],
      [tr('totalSched'), cov.total_series, cov.total_schedule_complete]];
    const covText = ([name, n]) => tr('covOf', { name, n: n ?? 0, m: cov.expected ?? 0 });
    $('srcFlow').innerHTML = covItems.filter(([, , ok]) => !ok).map((c) => chip(covText(c), 'warn')).join('');
    $('srcFlowAll').textContent = covItems.map((c) => `${c[2] ? '✓' : '!'} ${covText(c)}`).join(' · ');
  }

  function renderOut() {
    const d = state.data.outages;
    if (!d) return;
    const k = d.kpi || {}, zones = d.zones || {};
    const de = zones.DE_LU || {};
    setSig('sigOut', {
      value: mw(de.unavailable_mw, false),
      sub: `${tr('ofWhichUnplanned')} <b>${fmt(de.forced_mw)}</b> MW<br>Δ 24 h <b>${sgn(de.delta_24h_mw)}</b> · ${tr('unplannedDelta')} <b>${sgn(de.forced_delta_24h_mw)}</b>`,
      foot: `<span>${tr('asOf', { t: hhmm(k.as_of) })}</span><span>${tr('newChanged', { n: k.recent_notices ?? 0, m: k.recent_forced_notices ?? 0 })}</span>${de.complete === false ? `<span class="flag">${tr('incompleteFlag')}</span>` : ''}`,
      tag: dir(de.delta_24h_mw, 300, 'bull'),
    });
    const zoneOrder = ['DE_LU', 'FR', 'NL', 'BE'];
    $('tZones').innerHTML = `<thead><tr><th>Zone</th><th class="num">${tr('thUnavail')}</th><th class="num">${tr('thUnplanned')}</th><th class="num">${tr('thPlanned')}</th><th class="num">Δ 24 h</th><th class="num">${tr('thUnplDelta')}</th><th class="num">${tr('thActive')}</th><th>${tr('thData')}</th></tr></thead><tbody>` +
      zoneOrder.map((z) => {
        const r = zones[z] || {};
        const what = r.source ? (String(r.source).includes('A77') ? tr('unitPlant') : tr('unitOnly'))
          + (r.a77_plant_only_units ? tr('plantsWithout', { n: r.a77_plant_only_units }) : '') : '';
        const src = !r.source ? `<span class="flag">${tr('noDataFlag')}</span>`
          : r.complete === false ? `<span class="flag" title="${esc(what)}">${tr('incompleteFlag')}</span>` : `<span class="muted" title="${esc(what)}">${tr('complete')}</span>`;
        return `<tr><td><b>${ZONE_NAMES[z]}</b></td><td class="num">${fmt(r.unavailable_mw)}</td><td class="num">${fmt(r.forced_mw)}</td><td class="num">${fmt(r.planned_mw)}</td><td class="num">${sgn(r.delta_24h_mw)}</td><td class="num">${sgn(r.forced_delta_24h_mw)}</td><td class="num">${fmt(r.active_events)}</td><td>${src}</td></tr>`;
      }).join('') + '</tbody>';

    // All zones in one chart, like the table above. Fixed categorical order
    // (validated: adjacent pairs pass CVD/normal-vision in both themes);
    // direct labels at the line ends are the second channel besides colour.
    const zoneColors = { DE_LU: cssVar('--s1'), FR: cssVar('--s2'), NL: cssVar('--s3'), BE: cssVar('--s4') };
    const outTraces = OUT_ZONES.map((zn) => line(d.series?.[zn], ZONE_NAMES[zn], zoneColors[zn]));
    const hiddenZones = state.hidden.chOut || new Set();
    const ends = OUT_ZONES.filter((zn) => !hiddenZones.has(ZONE_NAMES[zn]))
      .map((zn) => ({ zn, p: (d.series?.[zn] || []).at(-1) })).filter((e) => e.p && isNum(e.p.v));
    const top = Math.max(1, ...ends.map((e) => e.p.v));
    const minGap = top * 0.09;  // keep end labels apart when zones sit close (e.g. NL/BE)
    const placed = ends.sort((a, b) => a.p.v - b.p.v).map((e) => ({ ...e, y: e.p.v }));
    placed.forEach((e, i) => { if (i && e.y - placed[i - 1].y < minGap) e.y = placed[i - 1].y + minGap; });
    const labels = placed.map((e) => ({
      x: utcX(e.p.t), y: e.y, xref: 'x', yref: 'y', xanchor: 'left', xshift: 6, showarrow: false,
      text: `<span style="color:${zoneColors[e.zn]}">■</span> ${ZONE_NAMES[e.zn]}`, font: { size: 11, color: cssVar('--text-2') },
    }));
    head('chOut', tr('outTitle'), 'MW', OUT_ZONES.map((zn) => lk(ZONE_NAMES[zn], zoneColors[zn])));
    plot('chOut', outTraces, baseLayout({
      margin: { l: 56, r: 64, t: 12, b: 30 }, annotations: labels,
      yaxis: Object.assign(baseLayout().yaxis, { rangemode: 'tozero' }),
    }));

    const typeLabel = (n) => (n.notice_type === 'forced' ? `<span class="flag">${tr('unplanned')}</span>` : n.notice_type === 'planned' ? tr('planned') : tr('unknown'));
    const window_ = (n) => `${dayTime(n.event_start)} – ${n.duration_class === 'open_ended' ? tr('open') : dayTime(n.event_end)}`;
    const recent = d.recent || [];
    $('tRecent').innerHTML = recent.length
      ? `<thead><tr><th>${tr('thPublished')}</th><th>Zone</th><th>${tr('thPlant')}</th><th>${tr('thFuel')}</th><th class="num">MW</th><th>${tr('thType')}</th><th>${tr('thPeriod')}</th><th>Status</th></tr></thead><tbody>` +
        recent.map((n) => `<tr><td>${dayTime(n.published)}${n.revision > 1 ? ` <span class="muted">Rev. ${n.revision}</span>` : ''}</td><td>${ZONE_NAMES[n.zone] || esc(n.zone)}</td><td>${esc(n.plant)}</td><td>${fuel(n.fuel)}</td><td class="num">${fmt(n.unavailable_mw)}</td><td>${typeLabel(n)}</td><td>${window_(n)}</td><td>${noticeState(n)}</td></tr>`).join('') + '</tbody>'
      : `<tbody><tr><td class="muted">${tr('noRecent')}</td></tr></tbody>`;
    renderNotices();
    // Chips only for problems; the full status sits under "Quellen & Methodik".
    const st = Object.entries(d.source_status || {}).flatMap(([zone, docs]) => Object.entries(docs).map(([doc, v]) => ({ zone, doc, v })));
    const stText = (e) => `${ZONE_NAMES[e.zone] || e.zone} ${DOCS[lang()][e.doc] || e.doc}: ${stateName(e.v)}`;
    $('srcOut').innerHTML = st.filter((e) => stateCls(e.v) !== 'ok').map((e) => chip(stText(e), stateCls(e.v))).join('');
    $('srcOutAll').textContent = st.map((e) => `${stateCls(e.v) === 'ok' ? '✓' : '!'} ${stText(e)}`).join(' · ');
  }

  const noticeState = (n) => (n.state === 'active' ? tr('active') : n.state === 'upcoming' ? tr('upcoming') : tr('ended'));
  function renderNotices() {
    const d = state.data.outages;
    if (!d) return;
    const fz = $('fZone').value, ft = $('fType').value, fs = $('fState').value, q = $('fText').value.trim().toLowerCase();
    const rows = (d.notices || []).filter((n) => (!fz || n.zone === fz) && (!ft || n.notice_type === ft) && (!fs || n.state === fs)
      && (!q || `${n.plant} ${n.fuel} ${FUEL_DE[n.fuel] || ''} ${n.reason_text || ''}`.toLowerCase().includes(q)));
    $('fCount').textContent = tr('countNotices', { n: rows.length, m: (d.notices || []).length });
    const durLabel = { bounded: tr('lt30'), long_term: tr('ge30'), open_ended: tr('open'), unknown: '?' };
    $('tNotices').innerHTML = `<thead><tr><th>Status</th><th>Zone</th><th>${tr('thPlant')}</th><th>${tr('thFuel')}</th><th class="num">MW</th><th class="num">${tr('thNominal')}</th><th>${tr('thType')}</th><th>${tr('thDuration')}</th><th>${tr('thPeriod')}</th><th>${tr('thPublished')}</th><th>${tr('thReason')}</th></tr></thead><tbody>` +
      rows.slice(0, 500).map((n) => `<tr><td>${noticeState(n)}</td><td>${ZONE_NAMES[n.zone] || esc(n.zone)}</td><td>${esc(n.plant)}</td><td>${fuel(n.fuel)}</td><td class="num">${fmt(n.unavailable_mw)}</td><td class="num">${fmt(n.nominal_mw)}</td><td>${n.notice_type === 'forced' ? `<span class="flag">${tr('unplanned')}</span>` : n.notice_type === 'planned' ? tr('planned') : '?'}</td><td>${durLabel[n.duration_class] || ''}</td><td>${dayTime(n.event_start)} – ${n.duration_class === 'open_ended' ? tr('open') : dayTime(n.event_end)}</td><td>${dayTime(n.published)}</td><td class="wrapcell">${esc(n.reason_text || '')}</td></tr>`).join('') + '</tbody>';
  }

  // Display names for the balancing sources (API labels are technical).
  const SYS_SOURCES = {
    de: { A86: 'Bilanz (ENTSO-E A86)', A85: 'reBAP (ENTSO-E A85)', NTP: 'netztransparenz.de', '12.3.E': 'Regelenergie (ENTSO-E A24)' },
    en: { A86: 'Balance (ENTSO-E A86)', A85: 'reBAP (ENTSO-E A85)', NTP: 'netztransparenz.de', '12.3.E': 'Balancing energy (ENTSO-E A24)' },
  };
  function renderSys() {
    const d = state.data.balancing;
    if (!d) return;
    const k = d.kpi || {}, s = d.series || {}, src = d.sources || {};
    // Headline = freshest of A86 (x4) and −NRV-Saldo (same quantity);
    // netztransparenz is usually 15–30 min ahead of the A86 German sum.
    const nowState = k.system_now_state || k.imbalance_state;
    const short = nowState === 'deficit', long = nowState === 'surplus';
    const stateText = short ? tr('sysShort') : long ? tr('sysLong') : nowState === 'balanced' ? tr('balanced') : '—';
    const fromNrv = (k.system_now_source || '').startsWith('NRV');
    const single = k.price_mode === 'single' || !(s['Imbalance price long'] || []).length;
    const price = k.price_mode === 'single' ? k.imbalance_price_eur_mwh : k.imbalance_price_short_eur_mwh;
    // Same-day reBAP is an operational estimate; the settled price comes later.
    const prelim = k.price_status !== 'final';
    const deSeries = (s['aFRR net DE'] || []).length > 0;
    const afrrNow = isNum(k.afrr_de_mw) ? k.afrr_de_mw : k.afrr_partial_mw;
    const mfrrNow = isNum(k.mfrr_de_mw) ? k.mfrr_de_mw : k.mfrr_partial_mw;
    const missing = (k.afrr_missing_areas || []).join(', ');
    const act = isNum(afrrNow) ? `aFRR <b>${sgn(afrrNow)}</b> MW${isNum(k.afrr_de_mw) ? '' : ` <span class="muted">(${tr('excl')} ${esc(missing)})</span>`}` : 'aFRR —';
    const officialMw = isNum(k.imbalance_volume_mwh) ? k.imbalance_volume_mwh * 4 : null;
    setSig('sigSys', {
      value: isNum(k.system_now_mw) ? `${sgn(k.system_now_mw)}<small>MW</small>` : '—',
      sub: `${stateText} · ${prelim ? tr('rebapPrel') : 'reBAP'} <b>${fmt(price, NF2)}</b> €/MWh<br>${tr('officialAt', { t: hhmm(k.as_of) })}: <b>${sgn(officialMw)}</b> MW · ${act}`,
      foot: `<span>${mtu(k.system_now_as_of || k.as_of)}</span><span title="${fromNrv ? tr('fromNrvTitle') : tr('fromA86Title')}">${fromNrv ? tr('prelim') : tr('official')}</span><span title="${k.imbalance_nowcast_points ? tr('h1Nowcast') : tr('h1A86')}">${tr('avg1h')} ${sgn(k.imbalance_1h_avg_mw)} MW${k.imbalance_nowcast_points ? '*' : ''}</span>${deltas(k.system_now_changes || k.changes)}`,
      tag: short ? { cls: 'bull', text: tr('tagShort') } : long ? { cls: 'bear', text: tr('tagLong') } : null,
    });

    // One quantity, one sign, one unit: system balance in MW (A86 MWh per
    // 15 min × 4), + = long. The latest quarter-hours come from the TSOs'
    // own figure on netztransparenz until the A86 sum is complete.
    const toMw = (pts) => (pts || []).map((q) => ({ t: q.t, v: isNum(q.v) ? q.v * 4 : q.v }));
    const nowcast = toMw(s['Net imbalance volume nowcast']);
    const prelimSet = new Set(nowcast.map((q) => q.t));
    const bal = bars([...toMw(s['Net imbalance volume']), ...nowcast], 'y', 'MW', [tr('tagLong'), tr('tagShort')], prelimSet);
    const euro = { hovertemplate: '%{y:,.2f} €/MWh' };
    const cP = cssVar('--s2'), cA = cssVar('--s3'), cM = cssVar('--s4'), cN = cssVar('--text-2');
    const priceKeys = single ? [] : [lk(tr('priceLong'), cP), lk(tr('priceShort'), cM, 'dash')];
    const priceTr = single ? [line(s['Imbalance price'], 'reBAP', cP, 'solid', 'y', euro)]
      : priceKeys.map((k, i) => line(s[i ? 'Imbalance price short' : 'Imbalance price long'], k.name, k.color, k.dash, 'y', euro));
    // Regelenergie: need (NRV-Saldo) and what was activated (aFRR, mFRR);
    // all three share the German sign: + = Unterdeckung, hochregeln.
    const without = deSeries ? '' : ` (${tr('excl')} ${missing || '—'})`;
    const mfrrPts = (s['mFRR net DE'] || []).length ? s['mFRR net DE'] : s['mFRR net partial'];
    const regKeys = [lk(tr('nrv'), cN, 'dot'), lk(`aFRR${without}`, cA), lk(`mFRR${without}`, cM)];
    const reg = [s['NRV-Saldo'], deSeries ? s['aFRR net DE'] : s['aFRR net partial'], mfrrPts]
      .map((pts, i) => line(pts, regKeys[i].name, regKeys[i].color, regKeys[i].dash));
    const range = xRange(bal, priceTr, reg);

    const shapes = [];
    if (nowcast.length) {
      // Preliminary quarter-hours: a quiet band behind the lighter bars.
      const x0 = Date.parse(nowcast[0].t) - 450e3, x1 = Date.parse(nowcast.at(-1).t) + 450e3;
      shapes.push({ type: 'rect', layer: 'below', xref: 'x', yref: 'paper', x0: utcX(x0), x1: utcX(x1), y0: 0, y1: 1,
        fillcolor: cssVar('--line-soft'), opacity: 0.8, line: { width: 0 } });
    }
    head('chSys', tr('sysTitle'), tr('sysNote'), [bk(tr('tagLong'), cssVar('--pos')), bk(tr('tagShort'), cssVar('--neg')),
      ...(nowcast.length ? [bk(tr('prelim'), cssVar('--muted'), 0.35)] : [])]);
    plot('chSys', bal, baseLayout({ shapes, bargap: 0.15 }), { xLabels: false, range });
    head('chRebap', single ? 'reBAP' : tr('priceDual'), `€/MWh${prelim ? tr('priceEst') : ''}`, priceKeys);
    plot('chRebap', priceTr, baseLayout(), { xLabels: false, range });
    head('chReg', tr('regTitle'), tr('regNote'), regKeys.filter((k, i) => (reg[i].x || []).length));
    plot('chReg', reg, baseLayout(), { range });

    const stateShort = short ? tr('tagShort') : long ? tr('tagLong') : nowState === 'balanced' ? tr('balanced') : '—';
    $('mSys').innerHTML = [
      metric(tr('sysNow'), isNum(k.system_now_mw) ? `${sgn(k.system_now_mw)} MW` : '—',
        `${stateShort} · ${hhmm(k.system_now_as_of || k.as_of)} · ${fromNrv ? tr('prelim') : tr('official')}`),
      metric(tr('rebapNow'), `${fmt(price, NF2)} €/MWh`, `${hhmm(k.price_as_of)} · ${prelim ? tr('estimate') : tr('final')}`),
      metric(tr('rebapToday'), `${fmt(k.day_avg_price_eur_mwh)} / ${fmt(k.day_max_price_eur_mwh)} / ${fmt(k.day_min_price_eur_mwh)}`, tr('avgMaxMin')),
      metric(tr('regNow'), isNum(afrrNow) ? `aFRR ${sgn(afrrNow)} MW` : '—',
        `mFRR ${sgn(mfrrNow)} MW${isNum(k.afrr_de_mw) ? '' : ` · ${tr('excl')} ${esc(missing)}`}`),
    ].join('');
    // Only problems are shown; the full status sits under "Quellen & Methodik".
    // With netztransparenz active, ENTSO-E A24 is just the fallback.
    const ntpOk = src.NTP?.state === 'ok';
    const relevant = Object.entries(src).filter(([key]) => !(ntpOk && key === '12.3.E'));
    const label = (key, v) => `${SYS_SOURCES[lang()][key] || v.label}: ${stateName(v.state)}`;
    $('srcSys').innerHTML = relevant.filter(([, v]) => stateCls(v.state) !== 'ok').map(([key, v]) => chip(label(key, v), stateCls(v.state), v.scope || '')).join('');
    $('srcSysAll').textContent = relevant.map(([key, v]) => `${stateCls(v.state) === 'ok' ? '✓' : '!'} ${label(key, v)}`).join(' · ');
  }

  const RENDER = { renewables: renderRes, load: renderLoad, borders: renderFlow, outages: renderOut, balancing: renderSys };
  const CHART_OWNER = { chRes: 'renewables', chResDiff: 'renewables', chLoad: 'load', chLoadDiff: 'load', chFlow: 'borders',
    chOut: 'outages', chSys: 'balancing', chRebap: 'balancing', chReg: 'balancing' };

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
    const stamp = tr('asOf', { t: built.length ? hhmm(new Date(Math.max(...built)).toISOString()) : '—' });
    const names = (list) => list.map((n) => PANEL_NAMES[lang()][n] || n).join(', ');
    if (failed.length) setStatus('bad', stamp, tr('errorIn', { p: names(failed) }), `${names(failed)}: ${state.errors[failed[0]]}`);
    else if (partial.length) setStatus('warn', stamp, tr('incompleteIn', { p: names(partial) }), tr('incompleteInfo'));
    else setStatus('ok', stamp, '', tr('allComplete'));
  }

  async function loadAll() {
    if (state.loading) return;
    state.loading = true;
    setStatus('warn', tr('loading'), '');
    $('refresh').classList.add('spinning');
    await loadPanels();
    state.loading = false;
    $('refresh').classList.remove('spinning');
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
        $('refresh').classList.add('spinning');
        await loadPanels(changed);
        state.loading = false;
        $('refresh').classList.remove('spinning');
      }
      updateStatus();
    } catch (_) { /* keep the current view; next poll retries */ }
  }

  // Short line (dot + "Stand 14:38" + problem, if any); everything else in the
  // tooltip, so the header stays one row on small screens.
  function setStatus(cls, text, detail = '', info = '') {
    $('statusDot').className = 'dot ' + cls;
    $('statusText').textContent = text;
    $('statusDetail').textContent = detail ? `· ${detail}` : '';
    const live = isLive() && $('auto').checked && !state.loading;
    const next = live ? tr(state.background ? 'liveNext' : 'nextUpdate', { s: Math.max(0, state.countdown) }) : tr('liveOff');
    $('status').title = [info, next].filter(Boolean).join(' · ');
    state.lastStatus = [cls, text, detail, info];
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
    const label = theme === 'dark' ? tr('themeLight') : tr('themeDark');
    btn.title = label; btn.setAttribute('aria-label', label);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#111110' : '#f3f2ee');
    Object.values(RENDER).forEach((f) => f());  // Plotly reads colors at render time
  }

  // Attributes and <option> texts cannot be switched by CSS: the German
  // original is kept in data-de-*, the English one comes from data-en-*.
  const ATTRS = [['enTitle', 'title'], ['enAria', 'aria-label'], ['enPh', 'placeholder']];
  function applyLang(l) {
    document.documentElement.lang = l;
    setNumberFormats();
    ATTRS.forEach(([key, attr]) => document.querySelectorAll(`[data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}]`).forEach((el) => {
      const deKey = key.replace(/^en/, 'de');
      if (el.dataset[deKey] === undefined) el.dataset[deKey] = el.getAttribute(attr) || '';
      el.setAttribute(attr, l === 'en' ? el.dataset[key] : el.dataset[deKey]);
    }));
    document.querySelectorAll('option[data-en]').forEach((o) => {
      if (o.dataset.de === undefined) o.dataset.de = o.textContent;
      o.textContent = l === 'en' ? o.dataset.en : o.dataset.de;
    });
    state.hidden = {};  // series names change with the language
    $('version').textContent = state.health ? `· v${state.health.version}${state.health.netztransparenz ? ` · ${tr('ntpActive')}` : ''}` : '';
    $('year').textContent = new Date().toLocaleDateString(LOCALE(), { year: 'numeric', timeZone: 'Europe/Berlin' });
    applyTheme(document.documentElement.dataset.theme || systemTheme());  // re-labels and re-renders everything
    if (state.loading) setStatus('warn', tr('loading'), '');
    else if (PANELS.some((n) => state.data[n] || state.errors[n])) updateStatus();
  }

  async function init() {
    applyLang(lang());
    document.querySelectorAll('.js-lang').forEach((b) => b.addEventListener('click', () => {
      const next = lang() === 'de' ? 'en' : 'de';
      try { localStorage.setItem(LANG_KEY, next); } catch (_) { /* private mode: still switch for this view */ }
      applyLang(next);
    }));
    $('theme').addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* private mode: still switch for this view */ }
      applyTheme(next);
    });
    $('day').value = todayBerlin();
    $('day').max = new Date(Date.now() + 2 * 864e5).toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
    // Tap anywhere on the date opens the picker (small phones hide its icon).
    $('day').addEventListener('click', () => { try { $('day').showPicker(); } catch (_) { /* older browsers: native behaviour */ } });
    $('day').addEventListener('change', () => { $('flowBorder').length = 1; state.zoom = {}; loadAll(); });
    $('today').addEventListener('click', () => { $('day').value = todayBerlin(); state.zoom = {}; loadAll(); });
    $('refresh').addEventListener('click', loadAll);
    $('flowBorder').addEventListener('change', renderFlow);
    // Key in a chart head: show / hide that series (kept across refreshes).
    document.addEventListener('click', (ev) => {
      const b = ev.target.closest('button.key[data-chart]');
      if (!b) return;
      const set = (state.hidden[b.dataset.chart] ||= new Set());
      if (!set.delete(b.dataset.name)) set.add(b.dataset.name);
      RENDER[CHART_OWNER[b.dataset.chart]]?.();
    });
    ['fZone', 'fType', 'fState'].forEach((id) => $(id).addEventListener('change', renderNotices));
    $('fText').addEventListener('input', renderNotices);
    segment('resTech', 'resTech', renderRes);
    window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', () => { if (!storedTheme()) applyTheme(systemTheme()); });
    // Rotating a phone / resizing a window across the breakpoint: re-render
    // so legend placement and tick spacing match the new width.
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        const flipped = [...document.querySelectorAll('.js-plotly-plot')].some((el) => el.dataset.narrow !== String(isNarrow(el)));
        if (flipped) Object.values(RENDER).forEach((f) => f());
      }, 250);
    });
    try {
      const h = await getJSON('/health', 30000);
      $('authChip').classList.toggle('hidden', !!h.auth_enabled || !!h.public_ok);
      state.health = h;
      $('version').textContent = `· v${h.version}${h.netztransparenz ? ` · ${tr('ntpActive')}` : ''}`;
      state.background = !!h.background_refresh;
    } catch (_) { /* panels report their own errors */ }
    setInterval(tick, 5000);
    loadAll();
  }
  document.addEventListener('DOMContentLoaded', init);
})();

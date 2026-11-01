/* Year in Review charts (tracker/templates/tracker/wrapped_review.html).
   Everything is plain SVG/HTML drawn from the JSON the page embeds
   (#yr-data); colours come from CSS (.yr-*), the world map from the
   vendored d3-geo + topojson files, so the page makes no outside requests. */
(function () {
  const NS = 'http://www.w3.org/2000/svg';

  function svg(tag, attrs, parent) {
    const el = document.createElementNS(NS, tag);
    for (const k in attrs || {}) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }

  function frame(host, w, h) {
    host.textContent = '';
    return svg('svg', { viewBox: `0 0 ${w} ${h}`, role: 'img', preserveAspectRatio: 'xMidYMid meet' }, host);
  }

  function empty(host, text) {
    host.textContent = '';
    const p = document.createElement('p');
    p.className = 'yr-note';
    p.textContent = text;
    host.appendChild(p);
  }

  /* One bar per week of the year, with gridlines and hour labels; the
     busiest week is picked out. */
  function weekly(host, section) {
    const points = section.weekly || [];
    const max = Math.max(1, ...points.map((p) => p.minutes));
    if (!points.some((p) => p.minutes)) return empty(host, 'Nothing watched this year.');
    const W = 960, H = 250, pad = { l: 44, r: 8, t: 12, b: 28 };
    const s = frame(host, W, H);
    const plotH = H - pad.t - pad.b;
    // Round the top of the scale up to a tidy number of hours.
    const maxHours = max / 60;
    const step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500].find((n) => n * 4 >= maxHours) || Math.ceil(maxHours / 4);
    const top = step * 4;
    for (let i = 0; i <= 4; i += 1) {
      const y = pad.t + plotH - (plotH * i) / 4;
      svg('line', { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: 'yr-grid' }, s);
      svg('text', { x: pad.l - 8, y: y + 4, 'text-anchor': 'end', class: 'yr-axis' }, s).textContent = `${step * i}h`;
    }
    const bw = (W - pad.l - pad.r) / points.length;
    const peak = points.reduce((a, p, i) => (p.minutes > points[a].minutes ? i : a), 0);
    points.forEach((p, i) => {
      const h = (plotH * (p.minutes / 60)) / top;
      const r = svg('rect', {
        x: pad.l + i * bw + 1.5, y: pad.t + plotH - h, width: Math.max(bw - 3, 1), height: Math.max(h, p.minutes ? 1 : 0),
        rx: 2, class: i === peak ? 'yr-bar yr-bar-peak' : 'yr-bar',
      }, s);
      svg('title', {}, r).textContent = `${p.label}: ${(p.minutes / 60).toFixed(1)} h, ${p.plays} plays`;
    });
    let lastMonth = '';
    points.forEach((p, i) => {
      const month = (p.label.split(' ')[1] || '').slice(0, 3);
      if (month && month !== lastMonth) {
        svg('text', { x: pad.l + i * bw, y: H - 8, class: 'yr-axis' }, s).textContent = month;
        lastMonth = month;
      }
    });
  }

  /* Hours or plays per month as a filled line. */
  function line(host, section, key) {
    const points = section.monthly || [];
    const values = points.map((p) => (key === 'hours' ? p.minutes / 60 : p.plays));
    if (!values.some((v) => v)) return empty(host, 'Nothing watched this year.');
    const W = 420, H = 130, pad = { l: 8, r: 8, t: 10, b: 22 };
    const max = Math.max(...values, 1);
    const x = (i) => pad.l + (i * (W - pad.l - pad.r)) / (values.length - 1);
    const y = (v) => H - pad.b - ((H - pad.t - pad.b) * v) / max;
    const s = frame(host, W, H);
    const path = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    svg('path', { d: `${path} L${x(values.length - 1)},${H - pad.b} L${x(0)},${H - pad.b} Z`, class: 'yr-area' }, s);
    svg('path', { d: path, class: 'yr-line' }, s);
    values.forEach((v, i) => {
      const c = svg('circle', { cx: x(i), cy: y(v), r: 2.6, class: 'yr-dot' }, s);
      svg('title', {}, c).textContent = `${points[i].label}: ${key === 'hours' ? v.toFixed(1) + ' h' : v + ' plays'}`;
    });
    [0, 3, 6, 9, 11].forEach((i) => {
      svg('text', { x: x(i), y: H - 6, class: 'yr-axis', 'text-anchor': 'middle' }, s).textContent = points[i].label;
    });
  }

  /* Names as circles sized by how often; a simple spiral packs them. */
  function bubbles(host, section) {
    const items = (section.secondary || []).slice(0, 28);
    if (!items.length) return empty(host, 'Nothing recorded for these.');
    const W = 420, H = 300, cx = W / 2, cy = H / 2;
    const max = items[0].count;
    const placed = [];
    items.forEach((it) => {
      const r = 14 + 40 * Math.sqrt(it.count / max);
      for (let t = 0; t < 4000; t += 1) {
        const a = t * 0.35, d = 2.2 * t * 0.35 * (1 / 2);
        const x = cx + Math.cos(a) * d * 1.6, y = cy + Math.sin(a) * d;
        if (x - r < 2 || x + r > W - 2 || y - r < 2 || y + r > H - 2) continue;
        if (placed.every((p) => Math.hypot(p.x - x, p.y - y) >= p.r + r + 2)) { placed.push({ x, y, r, it }); return; }
      }
    });
    const s = frame(host, W, H);
    placed.forEach(({ x, y, r, it }, i) => {
      const g = svg('g', { class: 'yr-bubble yr-tone-' + (i % 6) }, s);
      svg('circle', { cx: x, cy: y, r }, g);
      svg('title', {}, g).textContent = `${it.name}: ${it.count}`;
      if (r >= 24) {
        const label = it.name.length > Math.floor(r / 3.4) ? it.name.slice(0, Math.floor(r / 3.4) - 1) + '…' : it.name;
        svg('text', { x, y: y + 3, 'text-anchor': 'middle' }, g).textContent = label;
      }
    });
  }

  /* One long segmented bar, widths in proportion to plays. */
  function genres(host, section) {
    const items = section.genres || [];
    if (!items.length) return empty(host, 'No genres recorded for these.');
    const total = items.reduce((a, g) => a + g.count, 0);
    host.textContent = '';
    const bar = document.createElement('div');
    bar.className = 'yr-genre-bar';
    items.forEach((g, i) => {
      const seg = document.createElement('div');
      seg.className = 'yr-genre yr-tone-' + (i % 8);
      seg.style.flexGrow = String(g.count);
      seg.style.flexBasis = '0';
      seg.title = `${g.name}: ${g.count}`;
      const name = document.createElement('span');
      name.textContent = g.name;
      const count = document.createElement('small');
      count.textContent = `${Math.round((g.count / total) * 100)}%`;
      seg.append(name, count);
      bar.appendChild(seg);
    });
    host.appendChild(bar);
  }

  /* TMDB's display names -> the names world-atlas uses. */
  const ALIASES = {
    'united states': 'united states of america', 'usa': 'united states of america', 'us': 'united states of america',
    'korea, republic of': 'south korea', 'republic of korea': 'south korea', 'korea, democratic people\'s republic of': 'north korea',
    'czech republic': 'czechia', 'russian federation': 'russia', 'türkiye': 'turkey', 'viet nam': 'vietnam',
    'iran, islamic republic of': 'iran', 'syrian arab republic': 'syria', 'lao people\'s democratic republic': 'laos',
    'ivory coast': 'côte d\'ivoire', 'democratic republic of the congo': 'dem. rep. congo', 'republic of the congo': 'congo',
    'bosnia and herzegovina': 'bosnia and herz.', 'dominican republic': 'dominican rep.', 'central african republic': 'central african rep.',
    'south sudan': 'S. Sudan'.toLowerCase(), 'eswatini': 'eswatini', 'north macedonia': 'macedonia', 'macedonia': 'macedonia',
    'taiwan, province of china': 'taiwan', 'hong kong': 'china', 'macao': 'china', 'palestinian territory': 'palestine',
    'myanmar (burma)': 'myanmar', 'moldova, republic of': 'moldova', 'tanzania, united republic of': 'tanzania',
    'venezuela, bolivarian republic of': 'venezuela', 'bolivia, plurinational state of': 'bolivia', 'brunei darussalam': 'brunei',
    'equatorial guinea': 'eq. guinea', 'solomon islands': 'solomon is.', 'western sahara': 'w. sahara', 'falkland islands': 'falkland is.',
  };
  const norm = (n) => { const k = (n || '').toLowerCase().trim(); return ALIASES[k] || k; };

  function countries(host, section) {
    const items = section.countries || [];
    if (!items.length) return empty(host, 'No countries recorded for these.');
    const counts = {};
    items.forEach((c) => { counts[norm(c.name)] = (counts[norm(c.name)] || 0) + c.count; });
    const max = Math.max(...Object.values(counts));
    // The image export inlines the map data (nothing to fetch there).
    const load = window.__yrWorldData ? Promise.resolve(window.__yrWorldData) : fetch(window.__yrWorld).then((r) => r.json());
    load.then((world) => {
      const features = topojson.feature(world, world.objects.countries).features;
      const W = 960, H = 480;
      const projection = d3.geoNaturalEarth1().fitSize([W, H], { type: 'Sphere' });
      const path = d3.geoPath(projection);
      const s = frame(host, W, H);
      svg('path', { d: path({ type: 'Sphere' }), class: 'yr-sea' }, s);
      features.forEach((f) => {
        const name = f.properties.name || '';
        const n = counts[norm(name)] || 0;
        const p = svg('path', { d: path(f), class: n ? 'yr-land yr-land-on' : 'yr-land' }, s);
        if (n) {
          p.style.fillOpacity = String(0.35 + 0.65 * (n / max));
          svg('title', {}, p).textContent = `${name}: ${n}`;
        }
      });
    }).catch(() => empty(host, 'The map could not be loaded.'));
  }

  window.spoolYearReview = function () {
    const node = document.getElementById('yr-data');
    if (!node) return;
    const data = JSON.parse(node.textContent);
    document.querySelectorAll('[data-chart]').forEach((host) => {
      const section = data[host.dataset.section];
      if (!section) return;
      const kind = host.dataset.chart;
      if (kind === 'weekly') weekly(host, section);
      else if (kind === 'hours' || kind === 'plays') line(host, section, kind);
      else if (kind === 'secondary') bubbles(host, section);
      else if (kind === 'genres') genres(host, section);
      else if (kind === 'countries') countries(host, section);
    });
  };
  const me = document.currentScript;
  if (me && me.dataset.world) window.__yrWorld = me.dataset.world;
  if (document.readyState !== 'loading') window.spoolYearReview();
  else document.addEventListener('DOMContentLoaded', () => window.spoolYearReview());
})();

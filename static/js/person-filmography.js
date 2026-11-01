/* The person page's Filmography: department tabs, watched/type filters,
   sorting and the grid/list switch, all in the browser over the credits the
   server already rendered (once as grid tiles, once as list rows). The server
   order is newest first and everything is visible without this script; the
   script only narrows, reorders and collapses. The chosen view is remembered
   per browser (localStorage "spool-person-view"). */
(function () {
  const root = document.querySelector('[data-pf]');
  if (!root) return;

  const grid = root.querySelector('[data-pf-grid]');
  const rowsBody = root.querySelector('[data-pf-rows]');
  const gridItems = [...grid.querySelectorAll('[data-pf-item]')];
  const rowItems = [...rowsBody.querySelectorAll('[data-pf-item]')];
  const moreWrap = root.querySelector('[data-pf-more-wrap]');
  const moreButton = root.querySelector('[data-pf-more]');
  const emptyLine = root.querySelector('[data-pf-empty]');
  const statusLine = root.querySelector('[data-pf-status]');
  const limits = { grid: +root.dataset.limitGrid || 18, list: +root.dataset.limitList || 15 };

  const state = {
    tab: 'all', watch: 'all', type: 'all', sort: 'newest', expanded: false,
    view: root.dataset.view === 'list' ? 'list' : 'grid',
  };

  const num = (el, name) => parseFloat(el.dataset[name]) || 0;
  const comparators = {
    newest: (a, b) => (b.dataset.date || '').localeCompare(a.dataset.date || ''),
    rating: (a, b) => num(b, 'rating') - num(a, 'rating'),
    popular: (a, b) => num(b, 'pop') - num(a, 'pop'),
  };

  function matches(el) {
    if (state.tab !== 'all' && !el.dataset.depts.split(' ').includes(state.tab)) return false;
    if (state.watch === 'watched' && el.dataset.watched !== '1') return false;
    if (state.watch === 'unwatched' && el.dataset.watched === '1') return false;
    if (state.type !== 'all' && el.dataset.type !== state.type) return false;
    return true;
  }

  function decadeHeading(label, items) {
    const watched = items.filter((el) => el.dataset.watched === '1').length;
    const heading = document.createElement('div');
    heading.className = 'pf-decade';
    const title = document.createElement('h3');
    title.textContent = label;
    const count = document.createElement('span');
    count.textContent = `${items.length} credit${items.length === 1 ? '' : 's'} · ${watched} watched`;
    heading.append(title, count);
    return heading;
  }

  function refresh() {
    const compare = comparators[state.sort];
    const sortedGrid = gridItems.filter(matches).sort(compare);
    const total = sortedGrid.length;
    const limit = limits[state.view];
    const shown = state.expanded ? total : Math.min(total, limit);

    const gridNodes = [];
    const visible = sortedGrid.slice(0, shown);
    if (state.view === 'grid') {
      const byDecade = {};
      sortedGrid.forEach((el) => { (byDecade[el.dataset.decade] = byDecade[el.dataset.decade] || []).push(el); });
      let lastDecade = null;
      visible.forEach((el) => {
        if (state.sort === 'newest' && el.dataset.decade !== lastDecade) {
          lastDecade = el.dataset.decade;
          gridNodes.push(decadeHeading(lastDecade, byDecade[lastDecade]));
        }
        gridNodes.push(el);
      });
    }
    grid.replaceChildren(...gridNodes);

    if (state.view === 'list') {
      const sortedRows = rowItems.filter(matches).sort(compare);
      rowsBody.replaceChildren(...sortedRows.slice(0, shown));
    } else {
      rowsBody.replaceChildren();
    }

    emptyLine.hidden = total > 0;
    moreWrap.hidden = total <= limit;
    moreButton.textContent = state.expanded ? 'Show fewer credits' : `Show all ${total} credits`;
    statusLine.textContent = `${total} credit${total === 1 ? '' : 's'} shown`;
  }

  function setPressed(selector, attr, value) {
    root.querySelectorAll(selector).forEach((button) => {
      const on = button.dataset[attr] === value;
      button.classList.toggle('is-active', on);
      button.setAttribute('aria-pressed', String(on));
    });
  }

  function setView(view) {
    state.view = view;
    state.expanded = false;
    root.dataset.view = view;
    setPressed('[data-pf-view]', 'pfView', view);
    try { localStorage.setItem('spool-person-view', view); } catch (e) { /* private mode: the view just isn't remembered */ }
    refresh();
  }

  root.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || !root.contains(button)) return;
    if (button.dataset.pfTab !== undefined) { state.tab = button.dataset.pfTab; state.expanded = false; setPressed('[data-pf-tab]', 'pfTab', state.tab); refresh(); }
    else if (button.dataset.pfWatch !== undefined) { state.watch = button.dataset.pfWatch; state.expanded = false; setPressed('[data-pf-watch]', 'pfWatch', state.watch); refresh(); }
    else if (button.dataset.pfType !== undefined) { state.type = button.dataset.pfType; state.expanded = false; setPressed('[data-pf-type]', 'pfType', state.type); refresh(); }
    else if (button.dataset.pfView !== undefined) setView(button.dataset.pfView);
    else if (button.hasAttribute('data-pf-more')) { state.expanded = !state.expanded; refresh(); }
  });
  root.querySelector('[data-pf-sort]').addEventListener('change', (event) => {
    state.sort = event.target.value;
    state.expanded = false;
    refresh();
  });

  setPressed('[data-pf-view]', 'pfView', state.view);
  refresh();
})();

/* PlaceType PH documentation: lightweight, dependency-free browsing UI. */
(function () {
  'use strict';
  const root = document.documentElement;
  const themeKey = 'placetype-ph-docs-theme';
  try { const stored = localStorage.getItem(themeKey); if (stored === 'dark' || stored === 'light') root.dataset.theme = stored; } catch (_) { /* private mode */ }
  const theme = document.getElementById('theme-toggle');
  if (theme) theme.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem(themeKey, next); } catch (_) { /* private mode */ }
  });

  const menu = document.querySelector('.menu-toggle');
  const sidebar = document.querySelector('.sidebar');
  const scrim = document.querySelector('.nav-scrim');
  function closeMenu() { sidebar?.classList.remove('open'); scrim?.classList.remove('visible'); menu?.setAttribute('aria-expanded', 'false'); }
  if (menu) menu.addEventListener('click', () => {
    const isOpen = !sidebar?.classList.contains('open');
    sidebar?.classList.toggle('open', isOpen); scrim?.classList.toggle('visible', isOpen);
    menu.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  });
  scrim?.addEventListener('click', closeMenu);
  sidebar?.querySelectorAll('nav a').forEach(a => a.addEventListener('click', closeMenu));

  const search = document.getElementById('doc-search');
  const results = document.getElementById('search-results');
  const docs = window.PLACETYPE_DOCS_SEARCH || [];
  function norm(s) { return String(s || '').toLowerCase().normalize('NFKD'); }
  function searchDocs(term) {
    const terms = norm(term).split(/\s+/).filter(t => t.length > 1);
    if (!terms.length) return [];
    return docs.map(d => {
      const hay = norm(d.title + ' ' + d.headings.join(' ') + ' ' + d.text);
      const score = terms.every(t => hay.includes(t)) ? terms.reduce((n, t) => n + (norm(d.title).includes(t) ? 7 : 0) + (norm(d.headings.join(' ')).includes(t) ? 3 : 1), 0) : 0;
      return {d, score};
    }).filter(v => v.score).sort((a, b) => b.score - a.score).slice(0, 10);
  }
  function showSearch() {
    if (!results || !search) return;
    const term = search.value.trim();
    results.replaceChildren();
    if (!term) { results.hidden = true; return; }
    const hits = searchDocs(term);
    if (!hits.length) {
      const div = document.createElement('div'); div.className = 'search-help'; div.textContent = 'No matching chapter. Try “crosswalk”, “ownership”, or “backoff”.'; results.append(div);
    } else hits.forEach(({d}) => {
      const a = document.createElement('a'); a.href = d.slug + '.html'; a.textContent = d.title; results.append(a);
    });
    results.hidden = false;
  }
  search?.addEventListener('input', showSearch);
  search?.addEventListener('keydown', e => { if (e.key === 'Enter') { const a = results?.querySelector('a'); if (a) window.location.href = a.href; } });
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) { e.preventDefault(); search?.focus(); }
    if (e.key === 'Escape') { closeMenu(); if (results) results.hidden = true; search?.blur(); }
  });
  document.addEventListener('click', e => { if (results && search && !results.contains(e.target) && e.target !== search) results.hidden = true; });

  document.querySelectorAll('.prose pre').forEach(pre => {
    const wrap = document.createElement('div'); wrap.className = 'code-wrap';
    pre.parentNode?.insertBefore(wrap, pre); wrap.append(pre);
    const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'copy-code'; btn.textContent = 'Copy'; btn.setAttribute('aria-label', 'Copy code');
    btn.addEventListener('click', async () => { try { await navigator.clipboard.writeText(pre.textContent || ''); btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1200); } catch (_) { btn.textContent = 'Select to copy'; } });
    wrap.append(btn);
  });

  // Documentation illustration only: NOT a live invocation of the classifier.
  const examples = {
    agree: {a:['OSM','shop=pharmacy','Independent OSM lineage'], b:['Overture','pharmacy','Independent Overture lineage'], result:'Compatible — one supported PSIC branch', detail:'In the illustrated taxonomy, compatible reviewed mappings to 4772 keep 4772. Agreement is corroboration, not a numerical probability.', css:'agree'},
    dependent: {a:['Foursquare','Bank','FSQ lineage'], b:['Overture','bank','Derived from the same FSQ lineage'], result:'Dependent evidence — one lineage', detail:'The two records can constrain a classification but cannot count as two independent confirmations. The dependency group records common origin.', css:'dependent'},
    conflict: {a:['OSM','amenity=bank','Independent OSM lineage'], b:['Overture','pharmacy','Independent Overture lineage'], result:'Conflict — no forced exact code', detail:'If reviewed mappings assert disjoint activity branches, the classifier abstains (CONFLICT or REVIEW_ENTITY_MATCH where entity-resolution warnings exist).', css:'conflict'}
  };
  const panel = document.querySelector('[data-scenario-panel]');
  if (panel) {
    const set = key => {
      const d = examples[key]; if (!d) return;
      for (const [sel, source] of [['[data-evidence-a]', d.a], ['[data-evidence-b]', d.b]]) {
        const el = panel.querySelector(sel); if (!el) continue;
        const bits = el.querySelectorAll('[data-field]');
        bits.forEach((node,i) => node.textContent = source[i]);
      }
      panel.querySelector('[data-result-title]').textContent = d.result;
      panel.querySelector('[data-result-detail]').textContent = d.detail;
      panel.querySelectorAll('[data-scenario]').forEach(btn => { const selected = btn.dataset.scenario === key; btn.classList.toggle('selected', selected); btn.setAttribute('aria-pressed', selected ? 'true' : 'false'); });
    };
    panel.querySelectorAll('[data-scenario]').forEach(btn => btn.addEventListener('click', () => set(btn.dataset.scenario)));
    set('agree');
  }
})();

/*
  UI revision after the UAT (Phase 1 prototype; NOT loaded by the app). Run by Playwright after prototype.css to show the
  page header pattern and the button hierarchy on the real pages. Phase 3 makes the same changes in the templates.
  Only classes and positions change: every button keeps its link, form and behaviour.
*/
() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const setLevel = (el, level) => {
    if (!el) return;
    el.classList.remove('btn-primary', 'btn-secondary', 'btn-ghost', 'btn-warning', 'btn-danger', 'btn-arrow');
    el.classList.add('btn', level === 'primary' ? 'btn-primary' : level === 'secondary' ? 'btn-secondary' : level === 'danger' ? 'btn-danger' : 'btn-ghost');
    [...el.classList].filter(c => /^(bg-|text-white|text-primary|border-white|hover:bg-)/.test(c)).forEach(c => el.classList.remove(c));
  };
  const flatten = el => ['background', 'border', 'box-shadow', 'padding'].forEach((k, i) => el.style.setProperty(k, ['transparent', '0', 'none', '0'][i], 'important'));
  const iconOnly = el => { el.classList.add('btn-icon'); el.style.color = 'var(--color-muted)'; };

  // Shell: the top bar keeps search, Ask AI and the bell; the page title moves into the page header below.
  const bar = $('#content-panel > header');
  const barTitle = bar && $('h1', bar);
  const barSubtitle = barTitle && barTitle.nextElementSibling;
  if (barTitle) barTitle.closest('div').style.visibility = 'hidden';
  setLevel($('a[href="/assistant/"].btn', bar), 'secondary');  // Ask AI: always reachable, never louder than the page

  const header = (title, description, actions = []) => {
    const h = document.createElement('div');
    h.className = 'page-header';
    h.innerHTML = `<div class="min-w-0"><h1 class="page-title"></h1><p class="page-description"></p></div><div class="page-header-actions"></div>`;
    $('h1', h).textContent = title;
    $('p', h).textContent = description;
    actions.forEach(a => $('.page-header-actions', h).append(a));
    $('#main').prepend(h);
    return h;
  };
  const path = location.pathname;

  // ---------------------------------------------------------------- Dashboard
  if (path === '/') {
    const newActivity = $('.welcome-banner a[href="/activities/new/"]');
    if (newActivity) setLevel(newActivity, 'primary');
    header('Dashboard', (barSubtitle ? barSubtitle.textContent.split('·')[0].trim() : '') + ' · What needs your attention today', newActivity ? [newActivity] : []);
    setLevel($('.welcome-banner a[href="#needs-attention"]'), 'tertiary');
    $$('#needs-attention .btn').forEach(b => setLevel(b, 'secondary'));       // row actions: outlined, the chip shows the urgency
    $$('.spotlight .btn').forEach(b => setLevel(b, 'secondary'));
    const banner = $('.welcome-banner .banner-illustration'); if (banner) banner.style.width = '12rem';
  }

  // ---------------------------------------------------------------- Activity page
  const m = path.match(/^\/activities\/(\d+)\/$/);
  if (m) {
    const card = $('section[aria-labelledby="activity-title"]');
    const title = $('#activity-title', card);
    // Back link becomes a breadcrumb; the activity title is THE page title (no second title in the top bar).
    const back = $('a[href="/activities/"]', card);
    back.innerHTML = 'Activities <span aria-hidden="true" style="color:var(--color-helper)">/</span>';
    back.classList.add('text-sm');
    title.classList.add('page-title'); title.classList.remove('text-2xl');
    flatten(card);
    // Actions: Edit = primary, Change status / Copy link = secondary, More (⋯) = tertiary with Delete inside.
    const group = title.closest('.flex-wrap.items-start').lastElementChild;
    group.classList.add('page-header-actions');
    $$('.btn', group).forEach(b => {
      const t = b.textContent.trim();
      if (/^Edit/.test(t)) setLevel(b, 'primary');
      else if (b.getAttribute('aria-label') === 'More actions for this activity') setLevel(b, 'tertiary');
      else setLevel(b, 'secondary');
    });
    const edit = $$('.btn', group).find(b => /^Edit/.test(b.textContent.trim()));
    if (edit) group.append(edit);  // primary at the far right
    const more = $('[aria-label="More actions for this activity"]', group);
    if (more) group.append(more.closest('.relative'));
    // Needs attention: a quiet panel with an amber marker instead of a full amber box.
    const att = $('[aria-labelledby="attention-title"]', card);
    if (att) { att.style.background = '#fff'; att.style.borderColor = 'var(--color-line)'; att.style.borderLeft = '3px solid #F79009';
      att.style.borderRadius = '12px'; att.style.color = 'var(--color-ink)';
      $$('a', att).forEach(a => { a.style.color = 'var(--color-primary)'; a.style.textDecoration = 'none'; a.style.fontWeight = '500'; }); }
    // Tabs: underline tabs instead of a solid blue pill.
    const tabs = $('nav[aria-label="Activity sections"]');
    if (tabs) {
      Object.assign(tabs.style, { borderRadius: '0', border: '0', borderBottom: '1px solid var(--color-line)', background: 'transparent', boxShadow: 'none', padding: '0', marginBottom: '24px', gap: '24px' });
      $$('a', tabs).forEach(a => {
        a.classList.remove('bg-primary', 'text-white', 'rounded-full', 'px-4');
        Object.assign(a.style, { borderRadius: '0', padding: '10px 0', marginBottom: '-1px', borderBottom: '2px solid transparent', color: 'var(--color-muted)' });
        if (a.getAttribute('aria-current') || a.classList.contains('font-semibold') && a.href.includes(new URLSearchParams(location.search).get('tab') || 'overview')) {}
      });
      const cur = $$('a', tabs).find(a => a.getAttribute('aria-current') === 'page') || $$('a', tabs)[0];
      Object.assign(cur.style, { color: 'var(--color-primary)', borderBottomColor: 'var(--color-primary)' });
    }
    $$('#tab-area .btn').forEach(b => { if (b.classList.contains('btn-primary')) setLevel(b, 'secondary'); });
  }

  // ---------------------------------------------------------------- Documents page
  if (path === '/documents/' || path === '/documents/review/') {
    const card = $('section[aria-labelledby="documents-title"]');
    const switcher = $('nav[aria-label="Document lists"]', card);
    $('#documents-title', card).parentElement.remove();
    header('Documents', 'Required documents of every activity: what is missing, what waits for review and what is approved.', [switcher]);
    flatten(card);
    $$('[data-actions-group]').forEach(g => {
      $$(':scope > a, :scope > form > button', g).forEach(b => {
        const t = b.textContent.trim();
        if (/^Approve/.test(t)) setLevel(b, 'secondary');                      // the row's main action
        else if (/^Return/.test(t)) setLevel(b, 'tertiary');
        else if (/Download|Preview/.test(t) || b.title) { setLevel(b, 'tertiary'); iconOnly(b); }
        else if (/^Delete/.test(t)) { setLevel(b, 'tertiary'); b.style.color = 'var(--color-danger)'; b.style.marginLeft = '12px'; }
        else setLevel(b, 'secondary');
      });
    });
    $$('a.btn').filter(b => /^Upload/.test(b.textContent.trim())).forEach(b => setLevel(b, 'secondary'));
    $$('section .card, ul[aria-label^="Documents of"]').forEach(() => {});
  }
}

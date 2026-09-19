/* Language + theme runtime shared by every page.
   Requires i18n.js (dictionary) and prefs.js (initial attributes in <head>).
   Pages listen for "isteps:lang" to re-render text they build in JS. */
window.I18N = (() => {
  const root = document.documentElement;
  const DICT = window.ISTEPS_DICT;
  const LANGS = {
    fr: { locale:'fr-FR', dir:'ltr', label:'Français', code:'FR' },
    en: { locale:'en-GB', dir:'ltr', label:'English',  code:'EN' },
    ar: { locale:'ar-TN', dir:'rtl', label:'العربية',  code:'ع' },
  };
  const store = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
  const read = k => { try { return localStorage.getItem(k); } catch { return null; } };

  let lang = LANGS[root.lang] ? root.lang : 'fr';
  let pluralRules = new Intl.PluralRules(LANGS[lang].locale);

  function t(key, vars){
    const s = DICT[lang][key] ?? DICT.fr[key];
    if (s == null) return key;
    return vars ? s.replace(/\{(\w+)\}/g, (m, k) => vars[k] != null ? vars[k] : m) : s;
  }

  function tn(key, n, vars = {}){
    const cat = pluralRules.select(n);
    const k = DICT[lang][`${key}.${cat}`] != null ? `${key}.${cat}` : `${key}.other`;
    return t(k, { n: new Intl.NumberFormat(LANGS[lang].locale).format(n), ...vars });
  }

  function apply(scope = document){
    scope.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    // dictionary strings are our own static content, so innerHTML is safe here
    scope.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
    scope.querySelectorAll('[data-i18n-attr]').forEach(el => {
      el.dataset.i18nAttr.split(';').forEach(pair => {
        const [attr, key] = pair.split(':').map(s => s.trim());
        if (attr && key) el.setAttribute(attr, t(key));
      });
    });
    if (root.dataset.i18nTitle) document.title = t(root.dataset.i18nTitle);
    syncControls();
  }

  function setLang(next){
    if (!LANGS[next] || next === lang) return;
    lang = next;
    pluralRules = new Intl.PluralRules(LANGS[lang].locale);
    root.lang = lang;
    root.dir = LANGS[lang].dir;
    store('isteps.lang', lang);
    apply();
    dispatchEvent(new CustomEvent('isteps:lang', { detail:{ lang } }));
  }

  /* ---------------- theme ---------------- */
  let animTimer = 0;
  function setTheme(theme, persist){
    root.classList.add('theme-anim');
    root.dataset.theme = theme;
    if (persist) store('isteps.theme', theme);
    clearTimeout(animTimer);
    animTimer = setTimeout(() => root.classList.remove('theme-anim'), 450);
    syncControls();
    dispatchEvent(new CustomEvent('isteps:theme', { detail:{ theme } }));
  }
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', e => {
    if (!read('isteps.theme')) setTheme(e.matches ? 'light' : 'dark', false);
  });

  /* ---------------- controls ---------------- */
  const ICONS = {
    globe: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/></svg>',
    sun:   '<svg class="i-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/></svg>',
    moon:  '<svg class="i-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
    check: '<svg class="lang-check" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 13 4 4L19 7"/></svg>',
  };

  function buildControls(host){
    host.innerHTML = `
      <div class="lang-menu">
        <button type="button" class="icon-btn lang-btn" aria-haspopup="menu" aria-expanded="false">
          ${ICONS.globe}<span class="lang-code"></span>
        </button>
        <div class="lang-list" role="menu" hidden>
          ${Object.entries(LANGS).map(([k, v]) => `
            <button type="button" role="menuitemradio" data-lang="${k}">
              <span lang="${k}" dir="${v.dir}">${v.label}</span><small>${k.toUpperCase()}</small>${ICONS.check}
            </button>`).join('')}
        </div>
      </div>
      <button type="button" class="icon-btn theme-btn">${ICONS.sun}${ICONS.moon}</button>`;

    const btn = host.querySelector('.lang-btn');
    const list = host.querySelector('.lang-list');
    const items = [...list.querySelectorAll('button')];
    const close = (focusBtn) => { list.hidden = true; btn.setAttribute('aria-expanded', 'false'); if (focusBtn) btn.focus(); };
    const open = () => {
      list.hidden = false; btn.setAttribute('aria-expanded', 'true');
      (items.find(i => i.dataset.lang === lang) || items[0]).focus();
    };

    btn.addEventListener('click', () => list.hidden ? open() : close());
    items.forEach(i => i.addEventListener('click', () => { close(true); setLang(i.dataset.lang); }));
    list.addEventListener('keydown', e => {
      const idx = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); items[(idx + 1) % items.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); items[(idx - 1 + items.length) % items.length].focus(); }
      else if (e.key === 'Escape') { e.preventDefault(); close(true); }
      else if (e.key === 'Tab') close(false);
    });
    document.addEventListener('click', e => { if (!host.contains(e.target)) close(false); });

    host.querySelector('.theme-btn').addEventListener('click', () => {
      setTheme(root.dataset.theme === 'dark' ? 'light' : 'dark', true);
    });
  }

  function syncControls(){
    document.querySelectorAll('[data-prefs]').forEach(host => {
      const code = host.querySelector('.lang-code');
      if (!code) return;
      code.textContent = LANGS[lang].code;
      const btn = host.querySelector('.lang-btn');
      btn.setAttribute('aria-label', `${t('ui.language')} : ${LANGS[lang].label}`);
      btn.title = t('ui.language');
      host.querySelectorAll('.lang-list button').forEach(b => b.setAttribute('aria-checked', String(b.dataset.lang === lang)));
      const themeBtn = host.querySelector('.theme-btn');
      const label = root.dataset.theme === 'dark' ? t('ui.toLight') : t('ui.toDark');
      themeBtn.setAttribute('aria-label', label);
      themeBtn.title = label;
    });
  }

  document.querySelectorAll('[data-prefs]').forEach(buildControls);
  apply();
  root.classList.remove('i18n-pending');

  return {
    t, tn, apply, setLang,
    get lang(){ return lang; },
    get locale(){ return LANGS[lang].locale; },
    get dir(){ return LANGS[lang].dir; },
  };
})();

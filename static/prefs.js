/* Runs in <head> before first paint: apply the saved (or system) theme and saved language. */
(function () {
  var root = document.documentElement;
  function read(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

  var theme = read('isteps.theme');
  if (theme !== 'light' && theme !== 'dark') {
    theme = window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  root.setAttribute('data-theme', theme);

  var lang = read('isteps.lang');
  if (lang !== 'en' && lang !== 'ar') lang = 'fr';
  root.lang = lang;
  root.dir = lang === 'ar' ? 'rtl' : 'ltr';

  // markup ships in French; hide until site.js has swapped the text in
  if (lang !== 'fr') {
    root.classList.add('i18n-pending');
    setTimeout(function () { root.classList.remove('i18n-pending'); }, 1500);
  }
})();

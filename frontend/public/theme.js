/*
 * Dark-mode toggle for the static content pages. Uses the exact same
 * localStorage contract as the React app (src/lib/themePreference.js):
 * `taali-theme` = "dark" | "light" (+ legacy `taali_dark_mode` = "1" | "0"),
 * applied as `data-theme` on <html>. So toggling here persists to the app and
 * vice-versa. A tiny inline pre-paint snippet in each page sets the initial
 * theme before this runs, to avoid a flash.
 */
(function () {
  var MOON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
  var SUN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>';

  function read() {
    try {
      var t = localStorage.getItem('taali-theme');
      if (t === 'dark') return true;
      if (t === 'light') return false;
      var l = localStorage.getItem('taali_dark_mode');
      if (l != null) return l === '1';
    } catch (e) {}
    return false;
  }

  function apply(dark) {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.innerHTML = dark ? SUN : MOON;
      btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    }
  }

  var dark = read();
  apply(dark);

  var btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.addEventListener('click', function () {
      dark = !dark;
      try {
        localStorage.setItem('taali-theme', dark ? 'dark' : 'light');
        localStorage.setItem('taali_dark_mode', dark ? '1' : '0');
      } catch (e) {}
      apply(dark);
    });
  }
})();

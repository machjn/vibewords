// Apply saved theme before first paint to avoid flash
(function () {
  var t = localStorage.getItem('vw-theme');
  if (t === 'dark') document.documentElement.dataset.theme = 'dark';
}());

function setTheme(t) {
  if (t === 'dark') {
    document.documentElement.dataset.theme = 'dark';
  } else {
    delete document.documentElement.dataset.theme;
  }
  localStorage.setItem('vw-theme', t);
  _syncThemeToggles();
}

function toggleTheme() {
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
}

function _syncThemeToggles() {
  var dark = document.documentElement.dataset.theme === 'dark';
  document.querySelectorAll('[data-theme-item]').forEach(function (el) {
    el.classList.toggle('active', dark);
  });
}

document.addEventListener('DOMContentLoaded', _syncThemeToggles);

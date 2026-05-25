// Apply saved theme before first paint to avoid flash
(function () {
  var t = localStorage.getItem('vw-theme');
  if (t === 'dark') document.documentElement.dataset.theme = 'dark';
}());

var _THEMES = [
  { id: 'light', label: 'Light' },
  { id: 'dark',  label: 'Dark'  },
];

var _SUN_SVG  = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>';
var _MOON_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401"/></svg>';

function _themeIcon(id) { return id === 'dark' ? _MOON_SVG : _SUN_SVG; }

function setTheme(id) {
  if (id === 'dark') {
    document.documentElement.dataset.theme = 'dark';
  } else {
    delete document.documentElement.dataset.theme;
  }
  localStorage.setItem('vw-theme', id);
  _syncPickerState();
}

function _currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

function openThemePicker(e) {
  e.stopPropagation();
  var picker = document.getElementById('theme-picker');
  if (!picker) return;
  picker.hidden = !picker.hidden;
}

function _closePicker() {
  var picker = document.getElementById('theme-picker');
  if (picker) picker.hidden = true;
}

function _syncPickerState() {
  var current = _currentTheme();
  document.querySelectorAll('.theme-picker-btn').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.themeId === current);
  });
}

document.addEventListener('DOMContentLoaded', function () {
  var themeBtn = document.querySelector('[data-theme-btn]');
  if (!themeBtn) return;

  var picker = document.createElement('div');
  picker.id = 'theme-picker';
  picker.className = 'theme-picker';
  picker.hidden = true;

  _THEMES.forEach(function (theme) {
    var btn = document.createElement('button');
    btn.className = 'theme-btn theme-picker-btn';
    btn.dataset.themeId = theme.id;
    btn.title = theme.label;
    btn.setAttribute('aria-label', theme.label + ' theme');
    btn.innerHTML = _themeIcon(theme.id);
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      setTheme(theme.id);
      _closePicker();
    });
    picker.appendChild(btn);
  });

  var bar = themeBtn.closest('.corner-bar');
  if (bar) bar.insertBefore(picker, bar.firstChild);

  document.addEventListener('click', _closePicker);
  _syncPickerState();
});

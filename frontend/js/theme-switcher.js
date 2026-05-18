(function () {
  var THEMES = [
    { id: 'default',    label: 'Default',    dot: '#2563EB' },
    { id: 'classified', label: 'Classified', dot: '#B80000' },
    { id: 'broadsheet', label: 'Broadsheet', dot: '#8B1A1A' },
    { id: 'redacted',   label: 'Redacted',   dot: '#111111' },
    { id: 'manila',     label: 'Manila',     dot: '#C4A050' },
    { id: 'pressroom',  label: 'Press Room', dot: '#E63946' },
  ];

  // Apply a theme without touching the DOM switcher UI
  function _loadTheme(id) {
    var link = document.getElementById('theme-stylesheet');
    if (!link) {
      link = document.createElement('link');
      link.id = 'theme-stylesheet';
      link.rel = 'stylesheet';
      document.head.appendChild(link);
    }
    if (id && id !== 'default') {
      link.href = '/css/theme-' + id + '.css';
      localStorage.setItem('limboTheme', id);
    } else {
      link.removeAttribute('href');
      localStorage.removeItem('limboTheme');
    }
  }

  // Full apply — also updates the switcher UI
  function applyTheme(id) {
    _loadTheme(id);
    document.querySelectorAll('.ts-option').forEach(function (btn) {
      var active = btn.dataset.themeId === id || (id === 'default' && btn.dataset.themeId === 'default');
      btn.style.background = active ? 'var(--accent-light, #EFF6FF)' : 'transparent';
      btn.style.color = active ? 'var(--accent, #2563EB)' : 'var(--text, #111)';
      btn.style.fontWeight = active ? '700' : '400';
    });
    var lbl = document.getElementById('ts-current-label');
    if (lbl) {
      var t = THEMES.find(function (x) { return x.id === id; }) || THEMES[0];
      lbl.textContent = t.label;
    }
    var panel = document.getElementById('ts-panel');
    if (panel) panel.style.display = 'none';
  }

  // Inject switcher into header
  function buildSwitcher() {
    var inner = document.querySelector('.header-inner');
    if (!inner) return;

    var saved = localStorage.getItem('limboTheme') || 'default';
    var current = THEMES.find(function (x) { return x.id === saved; }) || THEMES[0];

    var wrap = document.createElement('div');
    wrap.id = 'ts-wrap';
    wrap.style.cssText = 'position:relative;margin-left:auto;flex-shrink:0;';

    var btn = document.createElement('button');
    btn.id = 'ts-toggle';
    btn.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + current.dot + ';margin-right:5px;vertical-align:middle;"></span><span id="ts-current-label">' + current.label + '</span><span style="margin-left:4px;font-size:10px;">▾</span>';
    btn.style.cssText = [
      'background:none',
      'border:1px solid var(--border,#E5E7EB)',
      'color:var(--text-muted,#9CA3AF)',
      'font-size:11px',
      'letter-spacing:0.06em',
      'font-family:inherit',
      'padding:4px 10px',
      'cursor:pointer',
      'border-radius:4px',
      'display:flex',
      'align-items:center',
      'gap:0',
      'white-space:nowrap',
    ].join(';');

    var panel = document.createElement('div');
    panel.id = 'ts-panel';
    panel.style.cssText = [
      'display:none',
      'position:absolute',
      'right:0',
      'top:calc(100% + 8px)',
      'background:var(--bg-raised,#FFF)',
      'border:1px solid var(--border,#E5E7EB)',
      'box-shadow:0 4px 16px rgba(0,0,0,0.12)',
      'z-index:9999',
      'min-width:148px',
      'border-radius:6px',
      'overflow:hidden',
    ].join(';');

    THEMES.forEach(function (theme) {
      var opt = document.createElement('button');
      opt.className = 'ts-option';
      opt.dataset.themeId = theme.id;
      opt.innerHTML = '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + theme.dot + ';margin-right:8px;flex-shrink:0;"></span>' + theme.label;
      var isActive = theme.id === saved;
      opt.style.cssText = [
        'display:flex',
        'align-items:center',
        'width:100%',
        'text-align:left',
        'background:' + (isActive ? 'var(--accent-light,#EFF6FF)' : 'transparent'),
        'border:none',
        'border-bottom:1px solid var(--border-subtle,#F3F4F6)',
        'padding:9px 14px',
        'font-family:inherit',
        'font-size:13px',
        'color:' + (isActive ? 'var(--accent,#2563EB)' : 'var(--text,#111)'),
        'font-weight:' + (isActive ? '700' : '400'),
        'cursor:pointer',
      ].join(';');

      opt.addEventListener('mouseenter', function () {
        if (opt.dataset.themeId !== (localStorage.getItem('limboTheme') || 'default')) {
          opt.style.background = 'var(--border-subtle,#F3F4F6)';
        }
      });
      opt.addEventListener('mouseleave', function () {
        if (opt.dataset.themeId !== (localStorage.getItem('limboTheme') || 'default')) {
          opt.style.background = 'transparent';
        }
      });
      opt.addEventListener('click', function (e) {
        e.stopPropagation();
        applyTheme(theme.id);
      });

      panel.appendChild(opt);
    });

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    });

    document.addEventListener('click', function () {
      panel.style.display = 'none';
    });

    wrap.appendChild(btn);
    wrap.appendChild(panel);
    inner.appendChild(wrap);
  }

  // Load saved theme immediately (before DOMContentLoaded to avoid flash)
  var saved = localStorage.getItem('limboTheme');
  if (saved) _loadTheme(saved);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', buildSwitcher);
  } else {
    buildSwitcher();
  }
})();

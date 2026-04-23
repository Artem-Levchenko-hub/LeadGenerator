/* Stenvik Leads — клиентский JS. */

// ==== Lucide icons ====
// Lucide заменяет <i data-lucide="name"></i> на inline SVG.
// После HTMX-свопа иконки в новом фрагменте теряются — перерисовываем.
function renderIcons(root) {
  if (!window.lucide || typeof window.lucide.createIcons !== 'function') return;
  try {
    if (root && root !== document) {
      window.lucide.createIcons({ nameAttr: 'data-lucide', attrs: {}, icons: {}, root });
    } else {
      window.lucide.createIcons();
    }
  } catch (e) {
    // fallback — глобальный пас, если локальный рендер не сработал
    try { window.lucide.createIcons(); } catch (_) {}
  }
}

function scheduleIconsInit() {
  // Lucide загружается defer — ждём DOMContentLoaded, плюс микрозадержку
  // на случай, если скрипт ещё парсится.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => renderIcons());
  } else {
    renderIcons();
  }
  // Повторяем через короткий таймер — страхуемся от гонки с defer-загрузкой.
  setTimeout(() => renderIcons(), 100);
  setTimeout(() => renderIcons(), 500);
}
scheduleIconsInit();

// После HTMX swap/update — рендерим иконки в новом фрагменте.
document.body && document.body.addEventListener('htmx:afterSwap', (e) => {
  renderIcons(e.target);
});
document.addEventListener('htmx:afterSwap', (e) => {
  renderIcons(e.target);
});
document.addEventListener('htmx:load', (e) => {
  renderIcons(e.target);
});

// ==== Service Worker registration ====
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then((reg) => {
        if (reg.waiting) {
          reg.waiting.postMessage('SKIP_WAITING');
        }
        reg.addEventListener('updatefound', () => {
          const newSW = reg.installing;
          if (!newSW) return;
          newSW.addEventListener('statechange', () => {
            if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
              newSW.postMessage('SKIP_WAITING');
            }
          });
        });
      })
      .catch((err) => console.warn('[sw] registration failed:', err));
  });

  let refreshing = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshing) return;
    refreshing = true;
    window.location.reload();
  });
}

// ==== PWA install prompt ====
let deferredInstallPrompt = null;

function isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
}

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches ||
         window.navigator.standalone === true;
}

function initInstallButton() {
  const btn = document.getElementById('install-app-btn');
  if (!btn) return;

  if (isStandalone()) {
    btn.style.display = 'none';
    return;
  }

  if (deferredInstallPrompt) {
    btn.hidden = false;
    btn.onclick = async () => {
      btn.hidden = true;
      deferredInstallPrompt.prompt();
      const { outcome } = await deferredInstallPrompt.userChoice;
      console.log('[install] outcome:', outcome);
      deferredInstallPrompt = null;
    };
  } else if (isIOS()) {
    btn.hidden = false;
    btn.querySelector('span').textContent = 'Как установить?';
    btn.onclick = () => {
      showIOSInstallHint();
    };
  }
}

function showIOSInstallHint() {
  const existing = document.getElementById('ios-hint');
  if (existing) { existing.remove(); return; }
  const hint = document.createElement('div');
  hint.id = 'ios-hint';
  hint.className = 'ios-hint';
  hint.innerHTML = `
    <div class="ios-hint-inner">
      <button class="ios-hint-close" onclick="document.getElementById('ios-hint').remove()">✕</button>
      <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #737373; margin-bottom: 10px; font-weight: 600;">
        Добавить на главный экран
      </div>
      <p style="font-size: 13px; color: #737373; margin: 0 0 12px; line-height: 1.55;">
        Чтобы Stenvik Leads работал как приложение:
      </p>
      <ol style="text-align: left; font-size: 13px; padding-left: 20px; margin: 0 0 8px; line-height: 1.7;">
        <li>Нажми кнопку <strong>«Поделиться»</strong></li>
        <li>Выбери <strong>«На экран „Домой"»</strong></li>
        <li>Нажми <strong>«Добавить»</strong></li>
      </ol>
    </div>
  `;
  document.body.appendChild(hint);
  renderIcons(hint);
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  initInstallButton();
});

window.addEventListener('appinstalled', () => {
  console.log('[install] App installed');
  deferredInstallPrompt = null;
  const btn = document.getElementById('install-app-btn');
  if (btn) btn.style.display = 'none';
});

document.addEventListener('DOMContentLoaded', initInstallButton);

// ==== Cache-aware reload ====
window.hardReload = function () {
  var url = location.href.replace(/[?&]_t=\d+/, '');
  url += (url.indexOf('?') === -1 ? '?' : '&') + '_t=' + Date.now();
  location.replace(url);
};

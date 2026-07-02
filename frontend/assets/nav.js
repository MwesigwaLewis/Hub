/* ════════════════════════════════════════
   FUTURE AI HUB — NAV ICONS (Lucide, https://lucide.dev)
════════════════════════════════════════ */

const NAV_ICONS = {
  home:   'home',
  raffle: 'ticket',
  ai:     'cpu',
  income: 'wallet',
  my:     'user',
};

// Some pages (currently my.html) already load Lucide themselves; for
// everywhere else, load it once here so the nav icons render everywhere.
function ensureLucide(cb) {
  if (window.lucide) { cb(); return; }
  const existing = document.querySelector('script[src*="lucide"]');
  if (existing) { existing.addEventListener('load', cb); return; }
  const script = document.createElement('script');
  script.src = 'https://unpkg.com/lucide@latest/dist/umd/lucide.js';
  script.onload = cb;
  document.head.appendChild(script);
}

function renderNav(activePage) {
  const pages = [
    { id: 'home',   label: 'Home',   href: 'home.html' },
    { id: 'raffle', label: 'Raffle', href: 'raffle.html' },
    { id: 'ai',     label: 'AI',     href: 'ai.html' },
    { id: 'income', label: 'Income', href: 'income.html' },
    { id: 'my',     label: 'My',     href: 'my.html' },
  ];
  const nav = document.getElementById('bottom-nav');
  if (!nav) return;
  nav.innerHTML = pages.map(p => `
    <a href="${p.href}" class="nav-item${p.id === activePage ? ' active' : ''}">
      <div class="nav-icon"><i data-lucide="${NAV_ICONS[p.id]}"></i></div>
      <span>${p.label}</span>
    </a>
  `).join('');
  ensureLucide(() => window.lucide.createIcons());
}

function toast(msg) {
  let el = document.getElementById('toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.className = 'toast show';
  setTimeout(() => { el.className = 'toast'; }, 2900);
}

function initWaveBg() {
  const bg = document.querySelector('.dot-bg');
  if (!bg) return;
  const canvas = document.createElement('canvas');
  bg.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  window.addEventListener('resize', resize);
  resize();

  const SEPARATION = 32;
  const AMOUNTX = 75;
  const AMOUNTY = 55;
  let count = 0;

  function render() {
    ctx.fillStyle = 'rgba(0,0,0,1)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const centerX = canvas.width / 2;
    const centerY = canvas.height * 0.4;

    for (let ix = 0; ix < AMOUNTX; ix++) {
      for (let iy = 0; iy < AMOUNTY; iy++) {
        const xPos = (ix * SEPARATION) - ((AMOUNTX * SEPARATION) / 2);
        const zPos = (iy * SEPARATION) - ((AMOUNTY * SEPARATION) / 2);
        const yPos = Math.sin((ix + count * 0.8) * 0.18) * 75 +
                     Math.sin((iy + count * 0.5) * 0.22) * 65 +
                     Math.cos((ix + iy + count) * 0.12) * 30;

        const fov = 400;
        const distance = fov / (fov + zPos + 350);
        const projX = centerX + xPos * distance;
        const projY = centerY + (yPos + 220) * distance;
        const dotSize = Math.max(0.4, distance * 2.2);
        const opacity = Math.min(1, Math.max(0.12, distance * 1.4));

        ctx.beginPath();
        ctx.arc(projX, projY, dotSize, 0, Math.PI * 2, true);
        ctx.fillStyle = `rgba(0, 238, 255, ${opacity})`;
        ctx.fill();
      }
    }
    count += 0.075;
    requestAnimationFrame(render);
  }
  render();
}

initWaveBg();

/* ════════════════════════════════════════
   PWA INSTALL / "DOWNLOAD APP"
   Real .apk compilation needs an Android SDK + signing toolchain that
   doesn't exist in a plain web stack, so "Download App" installs Future AI
   Hub as a Progressive Web App instead — same icon-on-homescreen, full-screen,
   offline-capable result a TWA-wrapped APK would give you, with no app store
   review and no APK to host. See PWA_TO_APK.md if you specifically need a
   .apk file (e.g. for a store listing) — it walks through wrapping this
   same PWA with PWABuilder once it's deployed at a real URL.
════════════════════════════════════════ */

// Make the manifest + theme-color discoverable on every page without
// having to hand-edit <head> in 9 separate HTML files.
(function injectPwaHeadTags() {
  if (!document.querySelector('link[rel="manifest"]')) {
    const link = document.createElement('link');
    link.rel = 'manifest';
    link.href = 'manifest.json';
    document.head.appendChild(link);
  }
  if (!document.querySelector('meta[name="theme-color"]')) {
    const meta = document.createElement('meta');
    meta.name = 'theme-color';
    meta.content = '#060a10';
    document.head.appendChild(meta);
  }
})();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  });
}

// Chrome/Android fires this ~instantly if install criteria are met; we stash
// it and use it later so the button can trigger the native prompt on tap
// (browsers require install() to be called from a user gesture).
window.__deferredInstallPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  window.__deferredInstallPrompt = e;
});

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches ||
         window.navigator.standalone === true; // iOS Safari
}

function isIOS() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

async function installApp() {
  if (isStandalone()) {
    toast('Future AI Hub is already installed');
    return;
  }

  if (window.__deferredInstallPrompt) {
    window.__deferredInstallPrompt.prompt();
    const { outcome } = await window.__deferredInstallPrompt.userChoice;
    window.__deferredInstallPrompt = null;
    toast(outcome === 'accepted' ? 'Installing…' : 'Install cancelled');
    return;
  }

  if (isIOS()) {
    toast('Tap Share, then "Add to Home Screen"');
    return;
  }

  // No native prompt available yet (criteria not met, already dismissed
  // this session, or an unsupported browser) — this is the same "just tell
  // them what to do" fallback a store-listing APK button would need anyway.
  toast('Open this site in Chrome, then use the browser menu → "Install app"');
}


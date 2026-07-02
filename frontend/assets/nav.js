/* ════════════════════════════════════════
   FUTURE AI HUB — NAV ICONS (inline SVG via JS)
════════════════════════════════════════ */

const NAV_ICONS = {
  home: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm0 8a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm8-8a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zm0 8a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>`,
  raffle: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect x="2" y="2" width="9" height="9" rx="1.5"/><rect x="13" y="2" width="9" height="9" rx="1.5"/><rect x="2" y="13" width="9" height="9" rx="1.5"/><rect x="13" y="13" width="9" height="9" rx="1.5"/></svg>`,
  ai: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M5 3h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2zm1 2v14h2V5H6zm4 0v14h2V5h-2zm4 0v14h2V5h-2zm4 0v14h-2V5h2zM5 9h14v2H5V9zm0 4h14v2H5v-2z"/></svg>`,
  income: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2zm.75 14.5v1.25a.75.75 0 01-1.5 0V16.5a3.25 3.25 0 01-.42-6.46V8.75a.75.75 0 011.5 0v1.25a3.25 3.25 0 01.42 6.5zM12 14.5a1.75 1.75 0 100-3.5 1.75 1.75 0 000 3.5z"/></svg>`,
  my: `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 12a5 5 0 110-10 5 5 0 010 10zm0 2c5.523 0 9 2.686 9 5 0 .828 0 1-9 1S3 19.828 3 19c0-2.314 3.477-5 9-5z"/></svg>`,
};

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
      <div class="nav-icon">${NAV_ICONS[p.id]}</div>
      <span>${p.label}</span>
    </a>
  `).join('');
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

                            

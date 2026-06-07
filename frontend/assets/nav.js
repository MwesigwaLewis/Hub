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

/* ── OCEAN WAVE BACKGROUND (auto-runs on every page via this shared file) ── */
(function () {
  const canvas = document.createElement('canvas');
  canvas.id = 'ocean-canvas';
  document.body.insertBefore(canvas, document.body.firstChild);

  const ctx = canvas.getContext('2d');
  const COLS = 30, ROWS = 60;
  let W, H, cellW, cellH, dots, t = 0;

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
    cellW = W / (COLS - 1);
    cellH = H / (ROWS - 1);
    buildDots();
  }

  function buildDots() {
    dots = [];
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        dots.push({
          bx:    c * cellW,
          by:    r * cellH,
          phase: c * 0.44 + r * 0.27,
          amp:   4 + Math.random() * 5,
          size:  0.9 + Math.random() * 1.5,
        });
      }
    }
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    t += 0.016;
    for (const d of dots) {
      const swell  = Math.sin(t * 0.85 + d.phase)       * d.amp;
      const ripple = Math.sin(t * 2.2  + d.phase * 1.8) * (d.amp * 0.28);
      const cross  = Math.cos(t * 0.55 + d.phase * 0.6) * (d.amp * 0.38);
      const x = d.bx + Math.sin(t * 0.45 + d.phase * 0.7) * 2.5;
      const y = d.by + swell + ripple + cross;
      const waveBright = 0.5 + 0.5 * ((swell / d.amp + 1) / 2);
      const depthFade  = 0.22 + 0.78 * (d.by / H);
      const alpha      = (waveBright * depthFade * 0.75).toFixed(2);
      ctx.beginPath();
      ctx.arc(x, y, d.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(0,170,255,${alpha})`;
      ctx.fill();
    }
    requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize);
  resize();
  draw();
})();

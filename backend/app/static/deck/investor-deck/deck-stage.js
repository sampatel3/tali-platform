/* deck-stage.js — minimal web component the investor deck relies on.
 *
 * Each <section data-slide="…"> inside <deck-stage> is one slide,
 * sized 1920×1080. The component scales the slide to fit the viewport
 * (CSS transform), wires keyboard navigation (←/→ · home · end), and
 * sets up @page rules so window.print() exports each slide as one
 * landscape PDF page.
 *
 * Hand-rolled rather than imported from a framework so the deck stays
 * one-file-portable. ~150 lines, no deps.
 */
(function () {
  'use strict';

  const STAGE_W = 1920;
  const STAGE_H = 1080;

  class DeckStage extends HTMLElement {
    constructor() {
      super();
      this.index = 0;
      this._onResize = this._fit.bind(this);
      this._onKey = this._key.bind(this);
    }

    connectedCallback() {
      // Style ourselves + each slide. Done in JS not <style> so the
      // component is fully self-contained.
      Object.assign(this.style, {
        position: 'relative',
        display: 'block',
        width: '100vw',
        height: '100vh',
        margin: '0',
        overflow: 'hidden',
        background: 'var(--deck-bg, #f7f4fb)',
      });

      this._slides = Array.from(this.querySelectorAll(':scope > section[data-slide]'));
      this._slides.forEach((s, i) => {
        Object.assign(s.style, {
          position: 'absolute',
          top: '0',
          left: '50%',
          width: STAGE_W + 'px',
          height: STAGE_H + 'px',
          transformOrigin: 'top center',
          display: i === 0 ? 'flex' : 'none',
          flexDirection: 'column',
        });
      });

      this._buildHud();

      this._fit();
      window.addEventListener('resize', this._onResize);
      document.addEventListener('keydown', this._onKey);

      // Hash-based deep link: #s=4 jumps to slide 4 (1-indexed).
      this._readHash();
      window.addEventListener('hashchange', () => this._readHash());
    }

    disconnectedCallback() {
      window.removeEventListener('resize', this._onResize);
      document.removeEventListener('keydown', this._onKey);
    }

    _fit() {
      const scale = Math.min(window.innerWidth / STAGE_W, window.innerHeight / STAGE_H);
      this._slides.forEach((s) => {
        s.style.transform = `translateX(-50%) scale(${scale})`;
      });
      // Vertically centre the scaled slide.
      const offsetY = Math.max(0, (window.innerHeight - STAGE_H * scale) / 2);
      this._slides.forEach((s) => {
        s.style.top = offsetY + 'px';
      });
    }

    _key(e) {
      // Don't steal navigation from form fields if the deck ever gains them.
      if (e.target && /input|textarea|select/i.test(e.target.tagName)) return;
      if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') {
        e.preventDefault();
        this.go(this.index + 1);
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault();
        this.go(this.index - 1);
      } else if (e.key === 'Home') {
        e.preventDefault();
        this.go(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        this.go(this._slides.length - 1);
      } else if (e.key.toLowerCase() === 'p') {
        // P — print to PDF
        e.preventDefault();
        window.print();
      }
    }

    go(next) {
      const n = Math.max(0, Math.min(this._slides.length - 1, next));
      this._slides[this.index].style.display = 'none';
      this._slides[n].style.display = 'flex';
      this.index = n;
      this._updateHud();
      // Update hash without scrolling.
      const newHash = '#s=' + (n + 1);
      if (location.hash !== newHash) history.replaceState(null, '', newHash);
    }

    _readHash() {
      const m = (location.hash || '').match(/s=(\d+)/);
      if (!m) return;
      const n = parseInt(m[1], 10) - 1;
      if (!isNaN(n) && n !== this.index) this.go(n);
    }

    _buildHud() {
      const hud = document.createElement('div');
      hud.className = 'deck-hud';
      Object.assign(hud.style, {
        position: 'fixed',
        bottom: '14px',
        right: '16px',
        zIndex: '9999',
        display: 'flex',
        gap: '8px',
        alignItems: 'center',
        fontFamily: 'Geist Mono, ui-monospace, monospace',
        fontSize: '11px',
        letterSpacing: '0.08em',
        color: 'rgba(20,15,40,0.55)',
      });
      hud.innerHTML = `
        <button data-act="prev" aria-label="Previous slide" style="background:#fff;border:1px solid #e7e0f0;border-radius:999px;padding:5px 10px;cursor:pointer;font:inherit;color:inherit">←</button>
        <span class="deck-hud-counter" style="padding:0 6px"></span>
        <button data-act="next" aria-label="Next slide" style="background:#fff;border:1px solid #e7e0f0;border-radius:999px;padding:5px 10px;cursor:pointer;font:inherit;color:inherit">→</button>
        <button data-act="print" aria-label="Download as PDF" title="Download as PDF (P)" style="background:#1a1228;color:#fff;border:0;border-radius:999px;padding:5px 12px;cursor:pointer;font:inherit;letter-spacing:0.06em">↓ PDF</button>
      `;
      hud.addEventListener('click', (e) => {
        const act = e.target && e.target.getAttribute && e.target.getAttribute('data-act');
        if (act === 'prev') this.go(this.index - 1);
        else if (act === 'next') this.go(this.index + 1);
        else if (act === 'print') window.print();
      });
      document.body.appendChild(hud);
      this._hud = hud;
      this._updateHud();
    }

    _updateHud() {
      if (!this._hud) return;
      const c = this._hud.querySelector('.deck-hud-counter');
      if (c) c.textContent = (this.index + 1) + ' / ' + this._slides.length;
    }
  }

  if (!customElements.get('deck-stage')) {
    customElements.define('deck-stage', DeckStage);
  }

  // Print rules: each slide becomes one landscape PDF page at 1920×1080.
  // Hide the HUD when printing.
  const printCss = `
    @page { size: 1920px 1080px; margin: 0; }
    @media print {
      html, body { background: #fff; }
      .deck-hud { display: none !important; }
      deck-stage { width: auto !important; height: auto !important; }
      deck-stage > section[data-slide] {
        position: relative !important;
        top: auto !important;
        left: auto !important;
        transform: none !important;
        width: 1920px !important;
        height: 1080px !important;
        page-break-after: always;
        display: flex !important;
        flex-direction: column !important;
      }
      deck-stage > section[data-slide]:last-child { page-break-after: auto; }
    }
  `;
  const styleEl = document.createElement('style');
  styleEl.textContent = printCss;
  document.head.appendChild(styleEl);
})();

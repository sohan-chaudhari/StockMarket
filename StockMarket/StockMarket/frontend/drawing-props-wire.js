/**
 * drawing-props-wire.js — connects the drawing context bar to the live chart.
 *
 * DELIBERATELY ADDITIVE. This file does not modify drawings.js, drawing-core.js,
 * the toolbar or the existing style panel. It only *listens* to the drawing
 * engine's own events and drives the engine's own API. To revert the whole
 * feature, delete the two tags this file needs in stock.html — nothing else.
 *
 * Uses the engine's public surface only:
 *   engine.eventBus  'drawing:select' | 'drawing:deselect' | 'drawing:delete'
 *   window.toolManager.selectedDrawing / deleteSelected()
 * and, for style changes, drives the EXISTING style-panel controls (#sp-color,
 * #sp-width-group buttons) by dispatching their own events — so the app's
 * proven code path applies the change instead of a new one we would have to
 * re-verify.
 */
(function () {
  'use strict';

  if (!window.DrawingProps) return;          // css/js not loaded -> do nothing
  if (window.__dpWired) return;              // never double-wire
  window.__dpWired = true;

  var LEGEND_ID = 'ohlc-floating-legend';
  var bar = null, palette = null, card = null, host = null;
  var openPaletteFor = null;

  function legend() { return document.getElementById(LEGEND_ID); }

  /* ---------------------------------------------------------------------
     Host: the legend's own parent, so the bar shares its coordinate space and
     can be positioned exactly on top of it at any viewport width.
     ------------------------------------------------------------------ */
  function ensureHost() {
    var lg = legend();
    if (!lg || !lg.parentElement) return null;
    host = lg.parentElement;
    return host;
  }

  /* ---------------------------------------------------------------------
     Current drawing + its style, read defensively
     ------------------------------------------------------------------ */
  function currentDrawing() {
    var tm = window.toolManager;
    if (!tm) return null;
    var sel = tm.selectedDrawing;
    if (!sel) return null;
    return sel.model ? sel.model : sel;
  }
  function currentStyle() {
    var d = currentDrawing();
    var s = d && d.style ? d.style : null;
    return {
      color: (s && (s.color || s.lineColor)) || '#2962ff',
      width: (s && (s.width || s.lineWidth)) || 2
    };
  }

  /* ---------------------------------------------------------------------
     Applying changes through the EXISTING panel controls.
     Set the control, then dispatch the same event the panel already handles.
     ------------------------------------------------------------------ */
  function setColor(colour) {
    var input = document.getElementById('sp-color');
    if (!input) return false;
    input.value = colour;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }
  function setWidth(px) {
    var group = document.getElementById('sp-width-group');
    if (!group) return false;
    var buttons = group.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      var n = parseInt((b.dataset && (b.dataset.width || b.dataset.w)) || b.textContent, 10);
      if (n === px) { b.click(); return true; }
    }
    return false;
  }

  /* ---------------------------------------------------------------------
     Bar / palette / card
     ------------------------------------------------------------------ */
  function buildBar() {
    if (bar) return bar;
    bar = window.DrawingProps.createBar({
      onEdit:  function () { showCard('style'); },
      onText:  function () { showCard('text'); },
      onColor: function (anchor) { togglePalette(anchor); },
      onDelete: function () {
        try {
          var tm = window.toolManager;
          if (tm && typeof tm.deleteSelected === 'function') tm.deleteSelected();
        } catch (e) {}
        hideAll();
      },
      onMore:  function () { showCard('style'); }
    });
    /* Clicks inside the bar must not reach the chart, or they would deselect
       the drawing and close everything — the bug seen in the preview. */
    ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
      bar.addEventListener(ev, function (e) { e.stopPropagation(); });
    });
    host.appendChild(bar);
    return bar;
  }

  function togglePalette(anchor) {
    if (!palette) {
      palette = window.DrawingProps.createPalette({
        color: currentStyle().color,
        opacity: 1,
        onPick: function (c) { bar.setColor(c); setColor(c); },
        onOpacity: function () {}
      });
      ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
        palette.addEventListener(ev, function (e) { e.stopPropagation(); });
      });
      host.appendChild(palette);
      palette.style.display = 'none';
    }
    if (palette.style.display === 'none') {
      hideCard();                 /* one popover at a time */
      openPaletteFor = anchor;
      placePalette(anchor);
      palette.style.display = 'block';
    } else {
      palette.style.display = 'none';
    }
  }

  function placePalette(anchor) {
    if (!palette || !anchor) return;
    var r = anchor.getBoundingClientRect();
    var h = host.getBoundingClientRect();
    var left = r.left - h.left;
    left = Math.max(8, Math.min(left, h.width - 312));
    palette.style.left = left + 'px';
    palette.style.top = (r.bottom - h.top + 8) + 'px';
  }

  function showCard(tab) {
    if (!card) {
      card = window.DrawingProps.createCard({
        title: 'Drawing',
        onOk: function () { hideCard(); },
        onCancel: function () { hideCard(); }
      });
      ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
        card.addEventListener(ev, function (e) { e.stopPropagation(); });
      });
      host.appendChild(card);
      card.style.display = 'none';
    }
    card.style.display = 'flex';
    card.selectTab(tab || 'style');
    /* keep it inside the chart, away from the OHLC strip the bar occupies */
    var h = host.getBoundingClientRect();
    card.style.left = 'auto';
    card.style.right = '12px';
    card.style.top = Math.max(12, (bar ? bar.offsetTop + bar.offsetHeight + 8 : 64)) + 'px';
    if (card.offsetHeight > h.height - 80) card.style.top = '12px';
  }
  function hideCard() { if (card) card.style.display = 'none'; }

  /* ---------------------------------------------------------------------
     Placement rule: the bar takes the OHLC legend's rectangle and the legend
     is hidden, so the OHLC is REPLACED by the bar rather than partly covered.
     Same spot at every viewport width — that is what keeps mobile and desktop
     consistent. visibility (not display) so nothing reflows on deselect.
     ------------------------------------------------------------------ */
  function alignBar() {
    var lg = legend();
    if (!bar || !lg || !host) return;
    var r = lg.getBoundingClientRect();
    var h = host.getBoundingClientRect();
    bar.style.left = Math.round(r.left - h.left) + 'px';
    bar.style.top  = Math.round(r.top  - h.top)  + 'px';
  }
  function suppressLegend(on) {
    var lg = legend();
    if (!lg) return;
    if (on) lg.classList.add('dp-hide-ohlc');
    else lg.classList.remove('dp-hide-ohlc');
  }

  /* ---------------------------------------------------------------------
     Show / hide
     ------------------------------------------------------------------ */
  function showForSelection() {
    if (!ensureHost()) return;
    buildBar();
    var st = currentStyle();
    bar.setWidth(st.width);
    bar.setColor(st.color);
    bar.style.display = 'flex';
    alignBar();
    suppressLegend(true);
    var d = currentDrawing();
    if (card && d) card.setTitle((d.type || 'Drawing'));
  }

  function hideAll() {
    if (bar) bar.style.display = 'none';
    if (palette) palette.style.display = 'none';
    hideCard();
    suppressLegend(false);
  }

  /* ---------------------------------------------------------------------
     Hook the engine's own events. Poll briefly for the engine to exist, then
     attach once. Nothing else in the app is touched.
     ------------------------------------------------------------------ */
  var tries = 0;
  (function attach() {
    var tm = window.toolManager;
    var engine = tm && tm.engine;
    if (!engine || !engine.eventBus) {
      if (++tries < 100) return setTimeout(attach, 200);
      return;                                    // engine never appeared -> stay dormant
    }
    engine.eventBus.on('drawing:select', function () {
      try { showForSelection(); } catch (e) {}
    });
    engine.eventBus.on('drawing:deselect', function () {
      try { hideAll(); } catch (e) {}
    });
    engine.eventBus.on('drawing:delete', function () {
      try { hideAll(); } catch (e) {}
    });
    engine.eventBus.on('drawing:create', function () {
      try { hideAll(); } catch (e) {}
    });

    /* click-away and Escape, matching how the chart already behaves */
    document.addEventListener('pointerdown', function (e) {
      if (!bar || bar.style.display === 'none') return;
      if (e.target && e.target.closest && e.target.closest('.dp-bar, .dp-palette, .dp-card')) return;
      var tm2 = window.toolManager;
      var stillSelected = tm2 && tm2.selectedDrawing;
      if (!stillSelected) hideAll();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') hideAll();
    });

    window.addEventListener('resize', function () {
      if (bar && bar.style.display !== 'none') { alignBar(); if (openPaletteFor) placePalette(openPaletteFor); }
    });
    window.addEventListener('scroll', function () {
      if (bar && bar.style.display !== 'none') alignBar();
    }, true);

    /* if a drawing is already selected when we attach, reflect it */
    try { if (window.toolManager && window.toolManager.selectedDrawing) showForSelection(); } catch (e) {}
  })();
})();

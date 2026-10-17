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
    /* The bar/card are absolutely positioned. If the host is `static`, absolute
       positioning would resolve against some ancestor further up and the bar
       would land off-screen — which is why it appeared on mobile but not on
       desktop. Make the host a containing block, in JS, so no stylesheet or
       existing markup is touched. */
    try {
      if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    } catch (e) {}
    return host;
  }

  /* The app's own click-outside guards (drawings.js) already ignore
     `.style-panel` and `.floating-panel`. Tagging our elements with those
     classes makes the app skip them too, so clicking blank space inside the card
     can no longer deselect the drawing — without editing drawings.js. */
  function tagGuarded(node) {
    try {
      node.classList.add('style-panel');
      node.classList.add('floating-panel');
    } catch (e) {}
    return node;
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
    host.appendChild(tagGuarded(bar));
    return bar;
  }

  function togglePalette(anchor) {
    if (!palette) {
      palette = window.DrawingProps.createPalette({
        color: currentStyle().color,
        opacity: 1,
        onPick: function (c) { bar.setColor(c); setColor(c); },
        onOpacity: function () {},
        onClose: function () { palette.style.display = 'none'; }
      });
      ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
        palette.addEventListener(ev, function (e) { e.stopPropagation(); });
      });
      /* document.body, not the chart: position:fixed inside a transformed
         ancestor is contained by it, which pushed the popovers off-screen. */
      document.body.appendChild(tagGuarded(palette));
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

  /* Viewport coordinates, because the popover is position:fixed. On mobile the
     stylesheet docks it to the bottom and an inline top must NOT be left set —
     an inline top plus a CSS bottom is what stretched it into a tall empty box. */
  function placePalette(anchor) {
    if (!palette || !anchor) return;
    if (window.innerWidth <= 768) {
      palette.style.top = '';
      palette.style.left = '';
      return;
    }
    var r = anchor.getBoundingClientRect();
    var left = Math.max(8, Math.min(r.left, window.innerWidth - 312));
    palette.style.left = left + 'px';
    palette.style.top = (r.bottom + 8) + 'px';
  }

  /* Apply what the card can actually apply. Line style maps onto the EXISTING
     panel's #sp-style-group buttons (same indirect approach as colour/width), so
     the app's proven handler does the work. Text / coordinates / visibility have
     no engine API for a trendline yet, so they are accepted but not applied. */
  var LINE_STYLE_ORDER = ['solid', 'dashed', 'dotted'];
  function applyCardValues(v) {
    try {
      if (!v || !v.lineStyle) return;
      var group = document.getElementById('sp-style-group');
      if (!group) return;
      var btns = group.querySelectorAll('button');
      if (!btns.length) return;
      /* prefer an explicit data attribute, else fall back to the standard order */
      var wanted = String(v.lineStyle).toLowerCase();
      var idx = LINE_STYLE_ORDER.indexOf(wanted);
      var target = null;
      for (var i = 0; i < btns.length; i++) {
        var b = btns[i];
        var key = (b.dataset && (b.dataset.style || b.dataset.line || b.dataset.s)) || '';
        if (key && String(key).toLowerCase() === wanted) { target = b; break; }
      }
      if (!target && idx >= 0 && btns[idx]) target = btns[idx];
      if (target) target.click();
    } catch (e) {}
  }

  function showCard(tab) {
    if (!card) {
      card = window.DrawingProps.createCard({
        title: 'Drawing',
        onOk: function (v) { applyCardValues(v); hideCard(); },
        onCancel: function () { hideCard(); }
      });
      ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
        card.addEventListener(ev, function (e) { e.stopPropagation(); });
      });
      document.body.appendChild(tagGuarded(card));
      card.style.display = 'none';
    }
    card.style.display = 'flex';
    card.selectTab(tab || 'style');

    /* Positioning: desktop docks it beside the bar in viewport coordinates; on
       mobile the stylesheet's bottom sheet takes over, so every inline offset
       must be CLEARED or it overrides the media query and hangs off-screen. */
    var isMobile = window.innerWidth <= 768;
    if (isMobile) {
      card.style.left = '';
      card.style.right = '';
      card.style.top = '';
      card.style.width = '';
    } else {
      var br = bar ? bar.getBoundingClientRect() : null;
      card.style.left = 'auto';
      card.style.right = '12px';
      card.style.width = '';
      var wantedTop = br ? Math.round(br.bottom + 8) : 64;
      card.style.top = Math.max(12, Math.min(wantedTop, window.innerHeight - 120)) + 'px';
      if (card.offsetHeight > window.innerHeight - 80) card.style.top = '12px';
    }
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

    /* Hide ONLY on the engine's own deselect event. An earlier version also
       hid on document pointerdown when toolManager.selectedDrawing looked empty,
       which fired mid-drag while moving a drawing and made the bar vanish. */
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') hideAll();
    });

    window.addEventListener('resize', function () {
      if (bar && bar.style.display !== 'none') {
        alignBar();
        if (openPaletteFor && palette && palette.style.display !== 'none') placePalette(openPaletteFor);
      }
    });
    window.addEventListener('scroll', function () {
      if (bar && bar.style.display !== 'none') alignBar();
    }, true);

    /* if a drawing is already selected when we attach, reflect it */
    try { if (window.toolManager && window.toolManager.selectedDrawing) showForSelection(); } catch (e) {}
  })();
})();

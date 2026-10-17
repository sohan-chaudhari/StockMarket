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
  /* width dropdown: 1px / 2px / 3px / 4px, like the reference */
  function openWidthMenu(anchor) {
    if (menu) { menu.remove(); menu = null; }
    var items = [1, 2, 3, 4].map(function (px) {
      return {
        id: 'w' + px,
        label: px + 'px',
        icon: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
              'stroke-width="' + px + '" stroke-linecap="round"><path d="M3 12h18"/></svg>',
        onSelect: function () {
          if (bar) bar.setWidth(px);
          setWidth(px);
        }
      };
    });
    menu = window.DrawingProps.createMenu(items, { onClose: function () { if (menu) { menu.remove(); menu = null; } } });
    document.body.appendChild(tagGuarded(menu));
    placeMenu(menu, anchor);
  }

  /* line-style dropdown: Line / Dashed line / Dotted line */
  var STYLE_LABELS = { solid: 'Line', dashed: 'Dashed line', dotted: 'Dotted line' };
  function openStyleMenu(anchor) {
    if (menu) { menu.remove(); menu = null; }
    var items = LINE_STYLE_ORDER.map(function (id) {
      var dash = id === 'dashed' ? ' stroke-dasharray="4 3"' : id === 'dotted' ? ' stroke-dasharray="1.5 3"' : '';
      return {
        id: id,
        label: STYLE_LABELS[id],
        icon: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
              'stroke-width="2" stroke-linecap="round"' + dash + '><path d="M3 12h18"/></svg>',
        onSelect: function () { applyCardValues({ lineStyle: id }); }
      };
    });
    menu = window.DrawingProps.createMenu(items, { onClose: function () { if (menu) { menu.remove(); menu = null; } } });
    document.body.appendChild(tagGuarded(menu));
    placeMenu(menu, anchor);
  }

  function placeMenu(m, anchor) {
    try {
      var r = anchor.getBoundingClientRect();
      var w = m.offsetWidth || 264;
      m.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 12)) + 'px';
      m.style.top = Math.min(r.bottom + 6, window.innerHeight - (m.offsetHeight || 200) - 12) + 'px';
    } catch (e) {}
  }

  /* ---------------------------------------------------------------------
     ••• menu — mirrors the TradingView list, but only with actions the engine
     can actually perform. Template / Visual order / Object tree / Add alert are
     omitted rather than shown as dead entries.
     ------------------------------------------------------------------ */
  var menu = null;

  function setToggle(id, on) {
    var box = document.getElementById(id);
    if (!box) return false;
    box.checked = !!on;
    box.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function openMenu(anchor) {
    if (menu) { menu.remove(); menu = null; }
    var I = window.DrawingProps.ICON;
    var items = [
      { id: 'visibility', label: 'Visibility on intervals', icon: I.eye,
        onSelect: function () { showCard('visibility'); } },
      { id: 'settings', label: 'Settings…', icon: I.pencil, separatorBefore: true,
        onSelect: function () { showCard('style'); } },
      { id: 'clone', label: 'Clone', icon: I.pencil,
        onSelect: function () {
          try {
            var d = currentDrawing();
            var engine = window.toolManager && window.toolManager.engine;
            if (d && engine && typeof engine.duplicateDrawing === 'function') engine.duplicateDrawing(d.id);
          } catch (e) {}
        } },
      { id: 'lock', label: 'Lock', icon: I.grip,
        onSelect: function () { setToggle('sp-locked', true); } },
      { id: 'unlock', label: 'Unlock', icon: I.grip,
        onSelect: function () { setToggle('sp-locked', false); } },
      { id: 'hide', label: 'Hide', icon: I.eyeOff,
        onSelect: function () { setToggle('sp-hidden', true); } },
      { id: 'remove', label: 'Remove', icon: I.trash, shortcut: 'Del', danger: true,
        separatorBefore: true,
        onSelect: function () {
          try {
            var tm = window.toolManager;
            if (tm && typeof tm.deleteSelected === 'function') tm.deleteSelected();
          } catch (e) {}
          hideAll();
        } }
    ];
    menu = window.DrawingProps.createMenu(items, {
      onClose: function () { if (menu) { menu.remove(); menu = null; } }
    });
    document.body.appendChild(tagGuarded(menu));
    /* place it just under the ••• button, kept inside the viewport */
    try {
      var r = anchor.getBoundingClientRect();
      var left = Math.min(r.right - 264, window.innerWidth - 276);
      menu.style.left = Math.max(8, left) + 'px';
      menu.style.top = Math.min(r.bottom + 6, window.innerHeight - 340) + 'px';
    } catch (e) {}
  }

  /* T -> a colour pad that changes the TEXT colour (not the line colour), and
     applies it straight away. The Text tab (font size, bold/italic, alignment)
     stays reachable through ••• -> Settings…. */
  function openTextColor(anchor) {
    if (palette) { palette.remove(); palette = null; }
    var d = currentDrawing();
    var start = (d && d.style && (d.style.textColor || d.style.color)) || '#d1d4dc';
    palette = window.DrawingProps.createPalette({
      color: start,
      opacity: 1,
      onPick: function (c) {
        try {
          var eng = window.toolManager && window.toolManager.engine;
          var dd = currentDrawing();
          if (eng && dd && typeof eng.updateDrawing === 'function') {
            /* text colour, kept separate from style.color (the line colour) */
            eng.updateDrawing(dd.id, { style: { textColor: c } });
          }
        } catch (e) {}
        if (card) card.setTextColor(c);
      },
      onOpacity: function () {},
      onClose: function () { if (palette) { palette.remove(); palette = null; } }
    });
    ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
      palette.addEventListener(ev, function (e) { e.stopPropagation(); });
    });
    document.body.appendChild(tagGuarded(palette));
    try {
      var r = anchor.getBoundingClientRect();
      palette.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 312)) + 'px';
      palette.style.top = Math.min(r.bottom + 6, window.innerHeight - 380) + 'px';
    } catch (e) {}
  }

  function buildBar() {
    if (bar) return bar;
    bar = window.DrawingProps.createBar({
      /* pencil -> the LINE colour pad (as asked). The settings card is reached
         through ••• -> Settings…, which is how the reference does it too. */
      onEdit:  function (anchor) { togglePalette(anchor || bar); },
      onText:  function (anchor) { openTextColor(anchor || bar); },
      onWidthMenu: function (anchor) { openWidthMenu(anchor); },
      onStyleMenu: function (anchor) { openStyleMenu(anchor); },
      onColor: function (anchor) { togglePalette(anchor); },
      onDelete: function () {
        try {
          var tm = window.toolManager;
          if (tm && typeof tm.deleteSelected === 'function') tm.deleteSelected();
        } catch (e) {}
        hideAll();
      },
      onMore:  function (anchor) { openMenu(anchor || bar); }
    });
    /* Clicks inside the bar must not reach the chart, or they would deselect
       the drawing and close everything — the bug seen in the preview. */
    ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
      bar.addEventListener(ev, function (e) { e.stopPropagation(); });
    });
    host.appendChild(tagGuarded(bar));
    return bar;
  }

  /* Position BOTH popovers from the BAR's viewport rect. They are
     position:fixed, so this puts them just under the bar — inside the chart —
     instead of at top:64px, which landed them over the page header and search
     bar. Only top/left are set (never bottom), so nothing can stretch. */
  function placeUnderBar(node) {
    try {
      var a = bar || anchorRef;
      if (!a || !node) return;
      var r = a.getBoundingClientRect();
      var w = node.offsetWidth || 300;
      node.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 12)) + 'px';
      node.style.top = Math.min(r.bottom + 8, window.innerHeight - (node.offsetHeight || 200) - 12) + 'px';
    } catch (e) {}
  }
  var anchorRef = null;

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
      anchorRef = anchor || bar;
      openPaletteFor = anchorRef;
      palette.style.display = 'block';
      placeUnderBar(palette);
    } else {
      palette.style.display = 'none';
    }
  }

  /* Positioning is owned by the STYLESHEET, never by JS. Setting inline offsets
     here is what repeatedly broke mobile: an inline value always beats the media
     query, and a viewport-width test in JS is unreliable on real devices. CSS
     now docks it as a panel on desktop and a bottom sheet on mobile. */
  function placePalette() {
    if (!palette) return;
    palette.style.top = '';
    palette.style.left = '';
    palette.style.right = '';
    palette.style.bottom = '';
  }

  /* Apply what the card can actually apply. Line style maps onto the EXISTING
     panel's #sp-style-group buttons (same indirect approach as colour/width), so
     the app's proven handler does the work. Text / coordinates / visibility have
     no engine API for a trendline yet, so they are accepted but not applied. */
  var LINE_STYLE_ORDER = ['solid', 'dashed', 'dotted'];
  /* Text: the engine takes content via updateDrawing(id, { content, style }).
     `content.plain` is the text payload; colour/size/weight/alignment ride in
     `style`. Every field is optional and undefined keys are stripped, so an
     untouched control can never clobber an existing value. */
  function applyText(v) {
    try {
      var d = currentDrawing();
      var engine = window.toolManager && window.toolManager.engine;
      if (!d || !engine || !v) return false;
      var text = (v.text == null) ? '' : String(v.text);
      var style = {};
      /* TEXT colour, not the line colour. Setting style.color here was a real
         bug: choosing a text colour in the card then pressing Ok recoloured the
         LINE instead of the text. */
      if (v.textColor) style.textColor = v.textColor;
      if (v.fontSize) style.fontSize = parseInt(v.fontSize, 10) || undefined;
      style.bold = !!v.bold;
      style.italic = !!v.italic;
      if (v.alignV) style.textAlignV = v.alignV;
      if (v.alignH) style.textAlignH = v.alignH;
      Object.keys(style).forEach(function (k) { if (style[k] === undefined) delete style[k]; });
      if (typeof engine.updateDrawing === 'function') {
        engine.updateDrawing(d.id, { content: { plain: text }, style: style });
        return true;
      }
    } catch (e) {}
    return false;
  }

  function applyCardValues(v) {
    try {
      if (!v) return;
      /* line style -> the EXISTING panel's buttons (proven path) */
      if (v.lineStyle) {
        var group = document.getElementById('sp-style-group');
        if (group) {
          var btns = group.querySelectorAll('button');
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
        }
      }
      /* text -> the engine's own updateDrawing content API */
      applyText(v);
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

      /* Text tab's colour swatch: opens its own palette and writes ONLY the text
         colour swatch, which Ok then applies via applyText(). It must never
         touch the line colour. */
      try {
        var textColorBtn = card.querySelector('#dp-text-color');
        var textSwatch = card.querySelector('#dp-text-swatch');
        if (textColorBtn && textSwatch) {
          textColorBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            var tp = window.DrawingProps.createPalette({
              color: '#d1d4dc',
              opacity: 1,
              onPick: function (c) { textSwatch.style.background = c; },
              onClose: function () { tp.remove(); }
            });
            ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
              tp.addEventListener(ev, function (ev2) { ev2.stopPropagation(); });
            });
            document.body.appendChild(tagGuarded(tp));
            var r = textColorBtn.getBoundingClientRect();
            tp.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 312)) + 'px';
            tp.style.top = Math.min(r.bottom + 6, window.innerHeight - 380) + 'px';
          });
        }
      } catch (e) {}
    }
    card.style.display = 'flex';
    card.selectTab(tab || 'style');
    /* clear inline offsets, then place under the bar (viewport coords) */
    card.style.left = '';
    card.style.right = '';
    card.style.top = '';
    card.style.width = '';
    placeUnderBar(card);
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
  function suppressLegend() {
    /* DELIBERATELY EMPTY. An earlier version hid the OHLC legend while a drawing
       was selected. The OHLC (and the ticker / LTP readout) must STAY visible and
       simply be overlapped by the bar — it should never disappear. */
    return;
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

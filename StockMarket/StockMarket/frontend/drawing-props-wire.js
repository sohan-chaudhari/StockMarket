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
    if (menuKind === 'width') { closeMenu(); return; }   /* same button toggles */
    closeMenu();
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
    menu = window.DrawingProps.createMenu(items, { onClose: closeMenu });
    menuKind = 'width';
    menu.style.visibility = 'hidden';       /* no flash at the default spot */
    document.body.appendChild(tagGuarded(menu));
    placeMenu(menu, anchor);
    menu.style.visibility = '';
  }

  /* line-style dropdown: Line / Dashed line / Dotted line */
  var STYLE_LABELS = { solid: 'Line', dashed: 'Dashed line', dotted: 'Dotted line' };
  function openStyleMenu(anchor) {
    if (menuKind === 'style') { closeMenu(); return; }    /* same button toggles */
    closeMenu();
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
    menu = window.DrawingProps.createMenu(items, { onClose: closeMenu });
    menuKind = 'style';
    menu.style.visibility = 'hidden';       /* no flash at the default spot */
    document.body.appendChild(tagGuarded(menu));
    placeMenu(menu, anchor);
    menu.style.visibility = '';
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
  var menuKind = null;          /* which menu is open: 'width' | 'style' | 'more' */

  function setToggle(id, on) {
    var box = document.getElementById(id);
    if (!box) return false;
    box.checked = !!on;
    box.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  /* Prefer the ENGINE API over dispatching a change on the app's checkbox: it
     updates the model AND the renderer and emits the proper event, so it can
     never silently do nothing. The checkbox is then kept in sync so the app's own
     panel still agrees. */
  function setLockState(want) {
    try {
      var eng = window.toolManager && window.toolManager.engine;
      var d = currentDrawing();
      if (eng && d && typeof eng.setLock === 'function') eng.setLock(d.id, !!want);
      var box = document.getElementById('sp-locked');
      if (box) box.checked = !!want;
      if (window.toolManager && typeof window.toolManager.redraw === 'function') window.toolManager.redraw();
      return true;
    } catch (e) { return false; }
  }
  function setHiddenState(want) {
    try {
      var eng = window.toolManager && window.toolManager.engine;
      var d = currentDrawing();
      if (eng && d && typeof eng.setVisibility === 'function') eng.setVisibility(d.id, !!want);
      var box = document.getElementById('sp-hidden');
      if (box) box.checked = !!want;
      if (window.toolManager && typeof window.toolManager.redraw === 'function') window.toolManager.redraw();
      return true;
    } catch (e) { return false; }
  }

  function openMenu(anchor) {
    if (menuKind === 'more') { closeMenu(); return; }       /* same button toggles */
    closeMenu();
    var I = window.DrawingProps.ICON;
    var sel = window.toolManager && window.toolManager.selectedDrawing;
    var locked = !!(sel && sel.locked);
    var hidden = !!(sel && sel.hidden);
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
      /* ONE entry whose label reflects the real state. Listing "Lock" AND
         "Unlock" together was wrong: whichever did not apply was dead. */
      { id: 'lock', label: locked ? 'Unlock' : 'Lock', icon: I.grip,
        onSelect: function () { setLockState(!locked); } },
      { id: 'hide', label: hidden ? 'Show' : 'Hide', icon: hidden ? I.eye : I.eyeOff,
        onSelect: function () { setHiddenState(!hidden); } },
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
    menu = window.DrawingProps.createMenu(items, { onClose: closeMenu });
    menuKind = 'more';
    menu.style.visibility = 'hidden';       /* no flash at the default spot */
    document.body.appendChild(tagGuarded(menu));
    /* place it just under the ••• button, kept inside the viewport */
    try {
      var r = anchor.getBoundingClientRect();
      var left = Math.min(r.right - 264, window.innerWidth - 276);
      menu.style.left = Math.max(8, left) + 'px';
      menu.style.top = Math.min(r.bottom + 6, window.innerHeight - 340) + 'px';
    } catch (e) {}
    menu.style.visibility = '';
  }

  /* T -> a colour pad that changes the TEXT colour (not the line colour), and
     applies it straight away. The Text tab (font size, bold/italic, alignment)
     stays reachable through ••• -> Settings…. */
  function openTextColor(anchor) {
    dropPalette();
    var tdStart = attachedText();
    var selStart = window.toolManager && window.toolManager.selectedDrawing;
    var start = (tdStart && tdStart.style && tdStart.style.color) ||
                (selStart && selStart._dpTextColour) || '#d1d4dc';
    palette = window.DrawingProps.createPalette({
      color: start,
      opacity: 1,
      onPick: function (c) { setTextColour(c); },
      onOpacity: function () {},
      onClose: function () { if (palette) { palette.remove(); palette = null; } }
    });
    ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
      palette.addEventListener(ev, function (e) { e.stopPropagation(); });
    });
    /* hidden until positioned, so it can never flash at the default spot */
    palette.style.visibility = 'hidden';
    document.body.appendChild(tagGuarded(palette));
    /* same rule as the line-colour pad: opens under the bar via the CSS
       variables, never at the top of the page over the header/search */
    placeUnderBar(palette);
    palette.style.visibility = '';
    paletteKind = 'text';
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

  /* The popovers are position:fixed and live in document.body, but the BAR
     lives inside the chart. So we publish the bar's viewport rect as CSS
     variables and let the STYLESHEET decide what to do with them.

     JS deliberately sets NO inline offsets here. An inline left/top always beats
     the media query, and on mobile that fought the docking rules
     (`left:0; right:0; width:100%`) — the over-constrained box is what made the
     card render clipped off the left edge. A CSS variable cannot do that. */
  function placeUnderBar(node) {
    try {
      var a = bar || anchorRef;
      if (!a) return;
      var r = a.getBoundingClientRect();
      var root = document.documentElement;
      root.style.setProperty('--dp-top', Math.round(r.bottom + 8) + 'px');
      root.style.setProperty('--dp-left', Math.round(r.left) + 'px');
      if (node) {
        node.style.left = '';
        node.style.right = '';
        node.style.top = '';
        node.style.bottom = '';
        node.style.width = '';
      }
    } catch (e) {}
  }
  var anchorRef = null;

  /* The line pad and the text pad both lived in one `palette` variable, so after
     opening the text pad the next pencil click found a visible palette and merely
     hid it — the line pad appeared not to open. Track which one is live. */
  var paletteKind = null;
  function dropPalette() {
    paletteKind = null;
    if (!palette) return;
    var p = palette;
    palette = null;
    try { p.remove(); } catch (e) {}
  }

  function togglePalette(anchor) {
    if (palette && paletteKind !== 'line') dropPalette();
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
      paletteKind = 'line';
    }
    if (palette.style.display === 'none') {
      hideCard();                 /* one popover at a time */
      anchorRef = anchor || bar;
      openPaletteFor = anchorRef;
      /* Position BEFORE showing. Revealing first and positioning afterwards made
         the pad paint for one frame at its default spot, which is the "something
         comes and goes, then it opens" the user saw. */
      placeUnderBar(palette);
      palette.style.display = 'block';
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
  /* Which drawing classes paint their own text? Everything else — trendline,
     ray, channel, rectangle, fib, position … — has NO text renderer at all.
     `content.plain` is stored and restored but never painted, which is exactly
     why typing text on a line did nothing visible. */
  var TEXT_NATIVE = {
    TextDrawing: 1, TextNote: 1, AnchoredText: 1, Callout: 1,
    PriceLabel: 1, Balloon: 1, ArrowLabel: 1
  };

  /* Find the TextDrawing attached to the currently selected drawing.
     Matched by remembered id first, then by the deterministic name set at attach
     time — so the link survives a page reload, when `_dpTextId` is gone. */
  function attachedText() {
    var tm = window.toolManager, eng = tm && tm.engine;
    var sel = tm && tm.selectedDrawing;
    if (!eng || !sel) return null;
    var want = sel._dpTextId || null;
    var lineId = sel.model ? sel.model.id : null;
    var wantName = lineId ? ('dp-label:' + lineId) : null;
    var byName = null;
    for (var i = 0; i < eng.drawings.length; i++) {
      var d = eng.drawings[i];
      var id = d.model ? d.model.id : d.id;
      var nm = d.model ? d.model.name : null;
      if (want && id === want) return d;
      if (wantName && nm === wantName) byName = d;
    }
    if (byName) {
      sel._dpTextId = byName.model ? byName.model.id : byName.id;
      return byName;
    }
    return null;
  }

  /* Change the TEXT colour. The old code called
     engine.updateDrawing(selectedId, {style:{textColor:c}}) — i.e. it set the
     property on the LINE, which nothing renders, so the pick appeared to do
     nothing (and users read the unchanged text as "it disappeared"). Now the
     colour is written straight onto the attached TextDrawing, which is the
     object that actually paints it (TextDrawing.draw reads this.style.color). */
  function setTextColour(c) {
    try {
      var tm = window.toolManager, eng = tm && tm.engine;
      var sel = tm && tm.selectedDrawing;
      if (sel) sel._dpTextColour = c;          /* remembered for the next attach */
      var td = attachedText();
      if (td && eng) {
        td.style = td.style || {};
        td.style.color = c;
        if (typeof eng.syncModel === 'function') eng.syncModel(td);
        if (typeof eng.markDirty === 'function') eng.markDirty();
      }
      if (tm && typeof tm.redraw === 'function') tm.redraw();
      if (card && typeof card.setTextColor === 'function') card.setTextColor(c);
      return true;
    } catch (e) { return false; }
  }

  /* Keep an attached label glued to its drawing's midpoint AND lying along the
     drawing's own angle, so moving or reshaping the line carries the text with
     it and the text stays parallel to the line (a 70-degree line gets
     70-degree text instead of always-horizontal text).
     commit === false is the cheap in-drag path: it only repaints, so the label
     travels smoothly with the line instead of jumping when the drag ends. */
  var _inReposition = false;
  function repositionAttachedText(commit, noRedraw) {
    if (_inReposition) return;      /* never recurse: this is called from the render hook */
    _inReposition = true;
    try {
      var tm = window.toolManager, eng = tm && tm.engine;
      var sel = tm && tm.selectedDrawing;
      var td = attachedText();
      if (!eng || !sel || !td) return;
      var cs = tm.getChartState ? tm.getChartState() : null;
      if (!cs) return;
      var ps = sel.getPixels ? sel.getPixels(cs) : null;
      if (!ps || !ps.length) return;

      var px = null;
      var mid = sel.getMidpoints ? sel.getMidpoints(cs) : null;
      if (mid && mid.length && mid[0]) px = { x: mid[0].x, y: mid[0].y };
      if (!px) {
        px = { x: (ps[0].x + ps[ps.length - 1].x) / 2, y: (ps[0].y + ps[ps.length - 1].y) / 2 };
      }

      var rot = 0;
      if (ps.length >= 2) {
        var dx = ps[ps.length - 1].x - ps[0].x;
        var dy = ps[ps.length - 1].y - ps[0].y;
        if (dx || dy) {
          rot = Math.atan2(dy, dx) * 180 / Math.PI;
          /* keep the text readable: never upside-down */
          while (rot > 90) rot -= 180;
          while (rot < -90) rot += 180;
          rot = Math.round(rot * 10) / 10;
        }
      }

      var c = cs.pixelToCoord(px.x, px.y);
      if (!c || c.logical == null || c.price == null) return;

      td.coords = [{ logical: c.logical, price: c.price, time: c.time }];
      td.rotation = rot;
      if (typeof td._invalidateCache === 'function') td._invalidateCache();

      if (commit !== false) {
        if (typeof eng.syncModel === 'function') eng.syncModel(td);
        if (typeof eng.markDirty === 'function') eng.markDirty();
      }
      if (!noRedraw && tm && typeof tm.redraw === 'function') tm.redraw();
    } catch (e) {} finally {
      _inReposition = false;
    }
  }

  /* Safety net: a drag must never be able to LOSE a drawing. If any renderer's
     coords ever become non-finite (NaN/undefined), restore the last good copy
     instead of leaving an invisible, unpickable object behind. */
  var lastGoodCoords = {};
  function coordsFinite(d) {
    if (!d || !d.coords || !d.coords.length) return false;
    for (var i = 0; i < d.coords.length; i++) {
      var c = d.coords[i];
      if (!c) return false;
      if (typeof c.logical !== 'number' || !isFinite(c.logical)) return false;
      if (typeof c.price !== 'number' || !isFinite(c.price)) return false;
    }
    return true;
  }
  function guardCoords() {
    try {
      var eng = window.toolManager && window.toolManager.engine;
      if (!eng || !eng.drawings) return;
      for (var i = 0; i < eng.drawings.length; i++) {
        var d = eng.drawings[i];
        var id = d.model ? d.model.id : d.id;
        if (!id) continue;
        if (coordsFinite(d)) {
          lastGoodCoords[id] = JSON.parse(JSON.stringify(d.coords));
        } else if (lastGoodCoords[id]) {
          d.coords = JSON.parse(JSON.stringify(lastGoodCoords[id]));
          if (typeof eng.syncModel === 'function') eng.syncModel(d);
          if (typeof eng.markDirty === 'function') eng.markDirty();
        }
      }
    } catch (e) {}
  }

  /* Attach a REAL TextDrawing at the selected drawing's midpoint.
     The engine's own TextDrawing paints `this.text` (see TextDrawing.draw in
     drawings.js) and the engine exposes the class on
     window.__ToolDefinitions.text = { class: TextDrawing, points: 1 }, so we
     reuse the proven renderer instead of writing a new one. Registration goes
     through engine.finalizeDrawing() — the same call the interactive tools use
     (toolManager.finishDrawing -> engine.finalizeDrawing) — so the model, undo
     stack, autosave and rendering all behave exactly as for any other drawing. */
  function attachTextDrawing(text, opts) {
    var tm = window.toolManager;
    var eng = tm && tm.engine;
    var def = window.__ToolDefinitions && window.__ToolDefinitions.text;
    if (!tm || !eng || !def || !def.class) return false;

    var sel = tm.selectedDrawing;                 /* the RENDERER, not the model */
    if (!sel) return false;

    var cs = tm.getChartState ? tm.getChartState() : null;
    if (!cs) return false;

    /* Remember the previous label, but do NOT delete it yet: it is only removed
       after the new one exists, so a failed re-create can never leave the
       drawing with no text at all (that was a real way for text to vanish). */
    var previousTextId = sel._dpTextId || null;

    /* anchor pixel = the drawing's midpoint, falling back to its own pixels */
    var px = null;
    try {
      var mid = sel.getMidpoints ? sel.getMidpoints(cs) : null;
      if (mid && mid.length && mid[0]) px = { x: mid[0].x, y: mid[0].y };
      if (!px) {
        var ps = sel.getPixels ? sel.getPixels(cs) : null;
        if (ps && ps.length) {
          px = {
            x: (ps[0].x + ps[ps.length - 1].x) / 2,
            y: (ps[0].y + ps[ps.length - 1].y) / 2
          };
        }
      }
    } catch (e) {}
    if (!px) return false;

    var options = {};
    try { options = JSON.parse(JSON.stringify(def.options || {})); } catch (e) { options = {}; }
    options.points = def.points || 1;

    var td = null;
    try { td = new def.class({ x: px.x, y: px.y }, cs, options); } catch (e) { return false; }
    if (!td) return false;

    td.text = text;
    td._userEdited = true;                 /* TextDrawing.isValid() needs this */
    /* The label belongs to the drawing, so it must never intercept the pointer:
       it sits exactly on the midpoint, and without this the engine's hit-test
       picked the TEXT and dragged it while the line stayed put. The engine skips
       any renderer flagged noHit. */
    td.noHit = true;
    /* and it must never draw axis badges of its own — those belong to the line */
    td.showAxisLabels = false;
    td.style = td.style || {};
    /* explicit choice > colour remembered from the T pad > the class default */
    var wantedColour = (opts && opts.textColor) || sel._dpTextColour || td.style.color;
    if (wantedColour) td.style.color = wantedColour;
    if (opts && opts.fontSize) {
      var fs = parseInt(opts.fontSize, 10);
      if (fs) td.style.fontSize = fs;
    }
    if (opts && opts.bold) td.style.bold = true;
    if (opts && opts.italic) td.style.italic = true;

    try {
      var res = eng.finalizeDrawing(td);
      if (res && res.drawing && res.drawing.id) {
        sel._dpTextId = res.drawing.id;    /* remember it, so re-Ok replaces it */
        /* deterministic name -> the line/label link survives a reload, when the
           in-memory _dpTextId is gone */
        try {
          var lineId = sel.model ? sel.model.id : null;
          if (lineId) res.drawing.name = 'dp-label:' + lineId;
        } catch (e) {}
        /* the new label is live, so the old one can go now */
        if (previousTextId && typeof eng.deleteDrawing === 'function') {
          try { eng.deleteDrawing(previousTextId); } catch (e) {}
        }
        /* place it on the midpoint and align it with the line straight away */
        repositionAttachedText(true);
        return true;
      }
    } catch (e) {}
    return false;
  }

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

      /* keep the model's own text field in sync — that is what the app's own
         style panel reads, so leaving it empty would look like the text was lost */
      if (typeof engine.updateDrawing === 'function') {
        engine.updateDrawing(d.id, { content: { plain: text }, style: style });
      }

      if (!text) return true;

      /* A line/shape cannot paint text, so give it a real TextDrawing. Types
         that already render their own text are left to do so. */
      var sel = window.toolManager.selectedDrawing;
      var cname = (sel && sel.constructor && sel.constructor.name) || '';
      if (!TEXT_NATIVE[cname]) attachTextDrawing(text, v);
      return true;
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
      /* The app's global keydown handler (drawings.js init) treats Delete AND
         Backspace as "delete the selected drawing". Without this guard, pressing
         Backspace to fix a typo in the Text tab DELETED THE DRAWING instead of
         erasing a character — which is also why the drawing seemed to "suddenly
         disappear". The app's listener is on window in the bubble phase, so
         stopping propagation at the card is enough, and the fix stays in this
         file. */
      var swallowEditingKeys = function (e) {
        var k = e.key;
        if (k === 'Backspace' || k === 'Delete' || k === 'Del') {
          e.stopPropagation();
        }
      };
      card.addEventListener('keydown', swallowEditingKeys);
      card.addEventListener('keyup', swallowEditingKeys);
      card.addEventListener('keypress', swallowEditingKeys);

      document.body.appendChild(tagGuarded(card));
      card.style.display = '';

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
    /* The card is now a right sidebar (like the Trade panel): it is always
       display:flex and slides in via the .is-open class. Never set display here —
       that is what stopped the transition from ever being seen. */
    card.style.display = '';
    card.style.left = '';
    card.style.right = '';
    card.style.top = '';
    card.style.bottom = '';
    card.style.width = '';
    card.style.transform = '';
    card.classList.add('is-open');
    /* Populate the Text tab from THIS drawing. Without this the box kept whatever
       was typed for the previously selected drawing, so pressing Ok stamped that
       text onto whatever was selected next — a silent way to corrupt a drawing. */
    try {
      var ta = card.querySelector('textarea');
      if (ta) {
        var tdNow = attachedText();
        var model = currentDrawing();
        var existing = tdNow ? (tdNow.text || '')
                             : ((model && model.content && model.content.plain) || '');
        ta.value = existing;
      }
    } catch (e) {}
    card.selectTab(tab || 'style');
  }
  function hideCard() {
    if (!card) return;
    card.classList.remove('is-open');
    card.style.transform = '';
  }

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
  /* Human names for the card title. It used to show the raw type ("trendline"). */
  var TYPE_NAMES = {
    trendline: 'Trend Line', horizontal_line: 'Horizontal Line',
    vertical_line: 'Vertical Line', horizontal_ray: 'Horizontal Ray',
    cross_line: 'Cross Line', ray: 'Ray', extended: 'Extended Line',
    infoline: 'Info Line', trend_angle: 'Trend Angle',
    channel: 'Parallel Channel', flat_top_channel: 'Flat Top Channel',
    flat_bottom_channel: 'Flat Bottom Channel', rectangle: 'Rectangle',
    circle: 'Circle', ellipse: 'Ellipse', triangle: 'Triangle', path: 'Path',
    brush: 'Brush', highlighter: 'Highlighter', text: 'Text', text_note: 'Note',
    callout: 'Callout', fib_retracement: 'Fib Retracement',
    fib_trend_ext: 'Fib Trend Extension', fib_channel: 'Fib Channel',
    fib_fan: 'Fib Fan', arrow: 'Arrow', dot: 'Dot', measure: 'Measure',
    long_position: 'Long Position', short_position: 'Short Position',
    price_range: 'Price Range', date_range: 'Date Range',
    date_price_range: 'Date and Price Range'
  };
  function prettifyType(t) {
    if (!t) return 'Drawing';
    if (TYPE_NAMES[t]) return TYPE_NAMES[t];
    var s = String(t).replace(/[_-]+/g, ' ');
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

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
    if (card && d) card.setTitle(prettifyType(d.type));
    /* re-assert the label's position and angle for the newly selected drawing —
       this also restores the angle after a reload, since model.rotation is not
       in the renderer-restore allow-list */
    try { repositionAttachedText(true); } catch (e) {}
  }

  /* Every popover must be closable from one place. hideAll() previously closed
     only the bar, the palette and the card — an open ••• / width / style menu
     survived a delete and stayed on screen as a dead list. */
  function closeMenu() {
    menuKind = null;
    if (!menu) return;
    var m = menu;
    menu = null;
    try { m.remove(); } catch (e) {}
  }

  function hideAll() {
    if (bar) bar.style.display = 'none';
    if (palette) palette.style.display = 'none';
    closeMenu();
    hideCard();
    suppressLegend(false);
  }

  /* The bar must never outlive its drawing. A deletion can arrive from many
     places — the ••• menu, the left toolbar, an eraser tool, "delete all",
     undo/redo — and not all of them emit drawing:delete with a selection
     cleanup, which is why the six-option bar could linger after the drawing had
     gone. Instead of chasing every producer, the bar validates its own state on
     each render pass. It only runs while the bar is visible, so it costs nothing
     when idle. */
  function syncBarState() {
    if (!bar || bar.style.display === 'none') return;
    var tm = window.toolManager, eng = tm && tm.engine;
    var sel = tm && tm.selectedDrawing;
    var alive = false;
    if (sel && eng && eng.drawings) {
      for (var i = 0; i < eng.drawings.length; i++) {
        if (eng.drawings[i] === sel) { alive = true; break; }
      }
    }
    /* Gone or deselected — the bar has nothing left to act on. A HIDDEN drawing
       deliberately keeps its bar: otherwise Hide would be impossible to undo,
       because the bar is the only place "Show" can be reached. */
    if (!alive) hideAll();
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
    engine.eventBus.on('drawing:update', function () {
      /* moving or reshaping the line carries its attached label with it */
      try { repositionAttachedText(); } catch (e) {}
    });

    /* Hide ONLY on the engine's own deselect event. An earlier version also
       hid on document pointerdown when toolManager.selectedDrawing looked empty,
       which fired mid-drag while moving a drawing and made the bar vanish. */
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') hideAll();
    });

    /* ONE rAF-throttled pass for every scroll and resize. Previously every
       scroll event ran alignBar() synchronously (two forced layouts each), which
       on a phone is a layout thrash on every frame — a real source of the lag. */
    var rafPending = false;
    function scheduleLayoutPass() {
      if (rafPending) return;
      rafPending = true;
      var run = function () {
        rafPending = false;
        try {
          if (bar && bar.style.display !== 'none') {
            alignBar();
            if (palette && palette.style.display !== 'none') placeUnderBar(palette);
          }
        } catch (e) {}
      };
      if (window.requestAnimationFrame) window.requestAnimationFrame(run);
      else setTimeout(run, 16);
    }
    window.addEventListener('resize', scheduleLayoutPass);
    /* NO scroll listener. The bar is position:absolute inside the chart
       container, so it does NOT move when the page or the chart scrolls. The old
       scroll listener ran alignBar() (two forced layouts) on EVERY scroll event,
       which is a per-frame layout thrash while panning — a real source of lag.
       A ResizeObserver fires only when the chart genuinely changes size. */
    try {
      if (window.ResizeObserver) {
        var hostEl = ensureHost();
        if (hostEl) new ResizeObserver(scheduleLayoutPass).observe(hostEl);
      }
    } catch (e) {}

    /* SMOOTH FOLLOW. The label must travel WITH the line, not jump when the drag
       ends. Hooking the engine's own render pass guarantees the label is moved in
       the SAME frame the line is drawn — no extra redraw, no listener-order
       dependence, and it works for every drag path (body drag, handle drag,
       auto-scroll, touch). It runs before the draw, so the label is painted at
       its new position immediately. */
    try {
      if (typeof engine.render === 'function') {
        var origRender = engine.render;
        engine.render = function () {
          /* the bar validates its own state every frame, so it can never linger
             after a delete that did not emit an event */
          try { syncBarState(); } catch (e) {}
          try { repositionAttachedText(false, true); } catch (e) {}
          return origRender.apply(engine, arguments);
        };
      }
    } catch (e) {}

    /* Any open popover closes on the next pointerdown OUTSIDE it. The bar, the
       pads and the card all stop propagation themselves, so a bubble-phase
       listener here only ever sees genuine outside clicks. Without this, an open
       ••• / width / style menu stayed on screen until an item was picked. */
    document.addEventListener('pointerdown', function (e) {
      try {
        var t = e.target;
        if (t && t.closest && t.closest('.dp-menu, .dp-palette, .dp-bar, .dp-card')) return;
        closeMenu();
        if (palette) palette.style.display = 'none';
      } catch (err) {}
    }, false);

    /* After any interaction: settle the label, and make sure no drawing was left
       with unusable coordinates. */
    var afterInteraction = function () {
      try { repositionAttachedText(true); } catch (e) {}
      try { guardCoords(); } catch (e) {}
    };
    document.addEventListener('pointerup', afterInteraction, true);
    document.addEventListener('touchend', afterInteraction, true);
    document.addEventListener('pointercancel', afterInteraction, true);

    /* if a drawing is already selected when we attach, reflect it */
    try { if (window.toolManager && window.toolManager.selectedDrawing) showForSelection(); } catch (e) {}
  })();
})();

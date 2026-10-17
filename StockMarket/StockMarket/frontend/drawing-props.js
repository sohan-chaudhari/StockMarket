/**
 * drawing-props.js — drawing context bar, colour palette and settings card.
 *
 * Self-contained and additive: it creates its own elements and exposes a small
 * API. Nothing here reaches into the existing toolbar, drawing engine or style
 * panel, so wiring it in later cannot disturb what already works.
 *
 *   DrawingProps.PALETTE                       -> the colour grid (array of rows)
 *   DrawingProps.createBar(opts)               -> the compact bar  ⠿ ✏️ 2px ── 🗑 •••
 *   DrawingProps.createPalette(opts)           -> the colour popover
 *   DrawingProps.createCard(opts)              -> the tabbed settings card
 *
 * Styling lives in drawing-props.css — one set of tokens and one 768px
 * breakpoint, so mobile (bottom sheet) and desktop (panel) cannot drift apart.
 */
(function () {
  'use strict';

  /* -------------------------------------------------------------------------
     Palette data. Greyscale row, then one column per hue with light -> dark
     shades, exactly like the reference: rows are lightness bands, columns are
     hues. 70 colours in total.
     ---------------------------------------------------------------------- */
  var GREYS = ['#ffffff', '#d4d4d4', '#b0b0b0', '#8c8c8c', '#6b6b6b',
               '#4d4d4d', '#333333', '#1f1f1f', '#0d0d0d', '#000000'];

  var HUES = [
    { h: 355, s: 78 },  // red
    { h: 28,  s: 92 },  // orange
    { h: 48,  s: 95 },  // yellow
    { h: 122, s: 45 },  // green
    { h: 173, s: 88 },  // teal
    { h: 187, s: 92 },  // cyan
    { h: 209, s: 90 },  // blue
    { h: 231, s: 62 },  // indigo
    { h: 285, s: 58 },  // purple
    { h: 331, s: 82 }   // magenta
  ];
  var SHADE_LIGHTNESS = [90, 78, 66, 54, 42, 32];

  function hsl(h, s, l) { return 'hsl(' + h + ', ' + s + '%, ' + l + '%)'; }

  function buildPalette() {
    var rows = [GREYS.slice()];
    for (var i = 0; i < SHADE_LIGHTNESS.length; i++) {
      var row = [];
      for (var j = 0; j < HUES.length; j++) {
        row.push(hsl(HUES[j].h, HUES[j].s, SHADE_LIGHTNESS[i]));
      }
      rows.push(row);
    }
    return rows;
  }
  var PALETTE = buildPalette();

  /* -------------------------------------------------------------------------
     Small helpers
     ---------------------------------------------------------------------- */
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function svg(paths, size) {
    return '<svg viewBox="0 0 24 24" width="' + (size || 18) + '" height="' + (size || 18) + '" ' +
           'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
           'stroke-linejoin="round" aria-hidden="true">' + paths + '</svg>';
  }
  var ICON = {
    grip:    svg('<circle cx="9" cy="6" r="1.3" fill="currentColor" stroke="none"/><circle cx="15" cy="6" r="1.3" fill="currentColor" stroke="none"/><circle cx="9" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="9" cy="18" r="1.3" fill="currentColor" stroke="none"/><circle cx="15" cy="18" r="1.3" fill="currentColor" stroke="none"/>', 16),
    pencil:  svg('<path d="M4 20h4L18 10l-4-4L4 16v4z"/><path d="M14 6l4 4"/>', 16),
    trash:   svg('<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/><path d="M10 10v7M14 10v7"/>', 16),
    more:    svg('<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>', 16),
    close:   svg('<path d="M6 6l12 12M18 6L6 18"/>', 18)
  };

  /* -------------------------------------------------------------------------
     1. Compact bar
     opts: { width, color, onWidth, onColor, onEdit, onDelete, onMore, onMove }
     Width cycles 1 -> 2 -> 3 -> 4 px on tap and shows the current value.
     ---------------------------------------------------------------------- */
  /* Width cycles through what the app actually supports. The existing panel's
     buttons are data-w="1|2|3" (drawings.js), so 4 would silently do nothing. */
  var WIDTHS = [1, 2, 3];

  function createBar(opts) {
    opts = opts || {};
    var bar = el('div', 'dp-bar');
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Drawing actions');

    var handle = el('div', 'dp-bar__handle', ICON.grip);
    handle.title = 'Drag to move';
    bar.appendChild(handle);

    bar.appendChild(el('div', 'dp-bar__sep'));

    var editBtn = el('button', 'dp-bar__btn', ICON.pencil);
    editBtn.type = 'button';
    editBtn.title = 'Edit style';
    editBtn.addEventListener('click', function (e) { e.stopPropagation(); opts.onEdit && opts.onEdit(); });
    bar.appendChild(editBtn);

    /* "Text" sits beside the pencil and opens the card already switched to the
       Text tab, so the common case is one tap instead of tab-hunting. */
    var textBtn = el('button', 'dp-bar__btn dp-bar__text', 'Text');
    textBtn.type = 'button';
    textBtn.title = 'Add or edit text';
    textBtn.addEventListener('click', function (e) { e.stopPropagation(); opts.onText && opts.onText(); });
    bar.appendChild(textBtn);

    var widthBtn = el('button', 'dp-bar__btn dp-bar__width', '2px');
    widthBtn.type = 'button';
    widthBtn.title = 'Line width';
    widthBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var cur = parseInt(widthBtn.textContent, 10) || 2;
      var next = WIDTHS[(WIDTHS.indexOf(cur) + 1) % WIDTHS.length];
      widthBtn.textContent = next + 'px';
      opts.onWidth && opts.onWidth(next);
    });
    bar.appendChild(widthBtn);

    var swatchBtn = el('button', 'dp-bar__btn');
    swatchBtn.type = 'button';
    swatchBtn.title = 'Line colour';
    var swatch = el('span', 'dp-bar__swatch');
    swatchBtn.appendChild(swatch);
    swatchBtn.addEventListener('click', function (e) { e.stopPropagation(); opts.onColor && opts.onColor(swatchBtn); });
    bar.appendChild(swatchBtn);

    bar.appendChild(el('div', 'dp-bar__sep'));

    var delBtn = el('button', 'dp-bar__btn dp-bar__danger', ICON.trash);
    delBtn.type = 'button';
    delBtn.title = 'Delete drawing';
    delBtn.addEventListener('click', function (e) { e.stopPropagation(); opts.onDelete && opts.onDelete(); });
    bar.appendChild(delBtn);

    var moreBtn = el('button', 'dp-bar__btn', ICON.more);
    moreBtn.type = 'button';
    moreBtn.title = 'More options';
    moreBtn.addEventListener('click', function (e) { e.stopPropagation(); opts.onMore && opts.onMore(); });
    bar.appendChild(moreBtn);

    /* drag the bar by its handle */
    (function () {
      var dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
      handle.addEventListener('pointerdown', function (e) {
        dragging = true;
        sx = e.clientX; sy = e.clientY;
        ox = bar.offsetLeft; oy = bar.offsetTop;
        handle.setPointerCapture(e.pointerId);
        e.preventDefault();
      });
      handle.addEventListener('pointermove', function (e) {
        if (!dragging) return;
        var nx = ox + (e.clientX - sx), ny = oy + (e.clientY - sy);
        bar.style.left = nx + 'px';
        bar.style.top = ny + 'px';
        opts.onMove && opts.onMove(nx, ny);
      });
      handle.addEventListener('pointerup', function () { dragging = false; });
      handle.addEventListener('pointercancel', function () { dragging = false; });
    })();

    bar.setWidth = function (px) { widthBtn.textContent = px + 'px'; };
    bar.setColor = function (c) { swatch.style.background = c; };
    return bar;
  }

  /* -------------------------------------------------------------------------
     2. Colour palette popover
     opts: { color, opacity, onPick, onOpacity, onCustom }
     ---------------------------------------------------------------------- */
  function createPalette(opts) {
    opts = opts || {};
    var root = el('div', 'dp-palette');

    var grid = el('div', 'dp-palette__grid');
    var swatches = [];
    PALETTE.forEach(function (row) {
      row.forEach(function (colour) {
        var b = el('button', 'dp-swatch');
        b.type = 'button';
        b.style.background = colour;
        b.dataset.color = colour;
        b.title = colour;
        b.addEventListener('click', function (e) {
          e.stopPropagation();
          mark(colour);
          opts.onPick && opts.onPick(colour);
        });
        swatches.push(b);
        grid.appendChild(b);
      });
    });
    root.appendChild(grid);

    function mark(colour) {
      swatches.forEach(function (s) {
        s.classList.toggle('is-selected', s.dataset.color === colour);
      });
      if (preview) preview.style.background = colour;
    }
    if (opts.color) mark(opts.color);

    /* Selected colour preview + close. The "+" is gone: the swatch below simply
       reflects whatever was picked in the grid. Tapping it still opens the native
       colour picker, so custom colours stay reachable. */
    var custom = el('div', 'dp-palette__custom');
    var preview = el('span', 'dp-palette__preview');
    preview.title = 'Custom colour';
    var closeBtn = el('button', 'dp-palette__close', ICON.close);
    closeBtn.type = 'button';
    closeBtn.title = 'Close';
    closeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (opts.onClose) opts.onClose();
      else root.style.display = 'none';
    });

    var picker = el('input');
    picker.type = 'color';
    picker.style.display = 'none';
    picker.value = opts.color && opts.color.charAt(0) === '#' ? opts.color : '#2962ff';
    preview.addEventListener('click', function (e) { e.stopPropagation(); picker.click(); });
    picker.addEventListener('input', function () { mark(picker.value); opts.onPick && opts.onPick(picker.value); });

    custom.appendChild(preview);
    custom.appendChild(el('div', 'dp-spacer'));
    custom.appendChild(closeBtn);
    root.appendChild(custom);
    root.appendChild(picker);

    root.appendChild(el('div', 'dp-palette__opacity-label', 'Opacity'));
    var op = el('div', 'dp-palette__opacity');
    var range = el('input');
    range.type = 'range';
    range.min = '5'; range.max = '100'; range.step = '1';
    range.value = String(Math.round((opts.opacity == null ? 1 : opts.opacity) * 100));
    var pct = el('span', 'dp-palette__pct', range.value + '%');
    range.addEventListener('input', function () {
      pct.textContent = range.value + '%';
      opts.onOpacity && opts.onOpacity(parseInt(range.value, 10) / 100);
    });
    op.appendChild(range);
    op.appendChild(pct);
    root.appendChild(op);

    root.setColor = mark;
    return root;
  }

  /* -------------------------------------------------------------------------
     3. Settings card — Style / Text / Coordinates / Visibility
     ---------------------------------------------------------------------- */
  var LINE_STYLES = [
    { id: 'solid',  label: 'Solid' },
    { id: 'dashed', label: 'Dashed' },
    { id: 'dotted', label: 'Dotted' }
  ];
  var VIS_ROWS = [
    { id: 'ticks',   label: 'Ticks',   num: false },
    { id: 'seconds', label: 'Seconds', num: true, val: 1, max: 59 },
    { id: 'minutes', label: 'Minutes', num: true, val: 1, max: 59 },
    { id: 'hours',   label: 'Hours',   num: true, val: 1, max: 24 },
    { id: 'days',    label: 'Days',    num: true, val: 1, max: 366 },
    { id: 'weeks',   label: 'Weeks',   num: true, val: 1, max: 52 },
    { id: 'months',  label: 'Months',  num: true, val: 1, max: 12 },
    { id: 'ranges',  label: 'Ranges',  num: false }
  ];

  function lineStyleSwatch(id) {
    var dash = id === 'dashed' ? ' stroke-dasharray="4 3"'
             : id === 'dotted' ? ' stroke-dasharray="1.5 3"' : '';
    return '<svg viewBox="0 0 24 24" width="24" height="14" fill="none" stroke="currentColor" ' +
           'stroke-width="2" stroke-linecap="round"' + dash + '><path d="M2 7h20"/></svg>';
  }

  function createCard(opts) {
    opts = opts || {};
    var card = el('div', 'dp-card');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', 'Drawing settings');

    /* ---- header ---- */
    var head = el('div', 'dp-card__head');
    var title = el('div', 'dp-card__title', opts.title || 'Drawing');
    var rename = el('button', 'dp-card__iconbtn', ICON.pencil);
    rename.type = 'button';
    rename.title = 'Rename';
    var close = el('button', 'dp-card__iconbtn', ICON.close);
    close.type = 'button';
    close.title = 'Close';
    close.addEventListener('click', function () { opts.onCancel && opts.onCancel(); });
    head.appendChild(title);
    head.appendChild(rename);
    head.appendChild(el('div', 'dp-spacer'));
    head.appendChild(close);
    card.appendChild(head);

    /* ---- tabs ---- */
    var TABS = [
      { id: 'style',       label: 'Style' },
      { id: 'text',        label: 'Text' },
      { id: 'coordinates', label: 'Coordinates' },
      { id: 'visibility',  label: 'Visibility' }
    ];
    var tabsBar = el('div', 'dp-tabs');
    tabsBar.setAttribute('role', 'tablist');
    var tabBtns = {};
    TABS.forEach(function (t) {
      var b = el('button', 'dp-tab', t.label);
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.addEventListener('click', function () { select(t.id); });
      tabBtns[t.id] = b;
      tabsBar.appendChild(b);
    });
    card.appendChild(tabsBar);

    /* ---- body ---- */
    var body = el('div', 'dp-card__body');
    var panes = {};

    /* Style pane */
    var pStyle = el('div', 'dp-pane');
    pStyle.innerHTML =
      '<div class="dp-row">' +
        '<div class="dp-row__label">Line</div>' +
        '<div class="dp-row__control">' +
          '<button type="button" class="dp-seg__btn" id="dp-style-color" title="Line colour">' +
            '<span class="dp-bar__swatch" id="dp-style-swatch" style="width:22px;height:14px;"></span>' +
          '</button>' +
          '<div class="dp-seg" id="dp-style-line">' +
            LINE_STYLES.map(function (s) {
              return '<button type="button" class="dp-seg__btn' + (s.id === 'solid' ? ' is-active' : '') +
                     '" data-line="' + s.id + '" title="' + s.label + '">' + lineStyleSwatch(s.id) + '</button>';
            }).join('') +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="dp-row">' +
        '<div class="dp-row__label">Extend</div>' +
        '<div class="dp-row__control"><select class="dp-select" id="dp-extend">' +
          '<option>Don\u2019t extend</option><option>Left</option><option>Right</option><option>Both</option>' +
        '</select></div>' +
      '</div>' +
      '<div class="dp-row">' +
        '<div class="dp-row__label"></div>' +
        '<div class="dp-row__control"><label class="dp-check"><input type="checkbox" id="dp-midpoint"> Middle point</label></div>' +
      '</div>' +
      '<div class="dp-row">' +
        '<div class="dp-row__label"></div>' +
        '<div class="dp-row__control"><label class="dp-check"><input type="checkbox" id="dp-pricelabels" checked> Price labels</label></div>' +
      '</div>' +
      '<div class="dp-group-label">Info</div>' +
      '<div class="dp-row">' +
        '<div class="dp-row__label">Stats</div>' +
        '<div class="dp-row__control"><select class="dp-select" id="dp-stats">' +
          '<option>Hidden</option><option>Visible</option></select></div>' +
      '</div>' +
      '<div class="dp-row">' +
        '<div class="dp-row__label">Stats position</div>' +
        '<div class="dp-row__control"><select class="dp-select" id="dp-stats-pos">' +
          '<option>Right</option><option>Left</option><option>Top</option><option>Bottom</option></select></div>' +
      '</div>' +
      '<div class="dp-row">' +
        '<div class="dp-row__label"></div>' +
        '<div class="dp-row__control"><label class="dp-check"><input type="checkbox" id="dp-always-stats"> Always show stats</label></div>' +
      '</div>';
    panes.style = pStyle;

    /* Text pane */
    var pText = el('div', 'dp-pane');
    pText.innerHTML =
      '<div class="dp-row">' +
        '<div class="dp-row__control">' +
          '<button type="button" class="dp-seg__btn" id="dp-text-color" title="Text colour">' +
            '<span class="dp-bar__swatch" id="dp-text-swatch" style="width:22px;height:14px;background:#d1d4dc;"></span>' +
          '</button>' +
          '<select class="dp-select" id="dp-font-size" style="width:90px">' +
            ['8','9','10','11','12','14','16','18','20','24','28','32','40'].map(function (n) {
              return '<option' + (n === '12' ? ' selected' : '') + '>' + n + '</option>';
            }).join('') +
          '</select>' +
          '<button type="button" class="dp-seg__btn" id="dp-bold" title="Bold" style="font-weight:800">B</button>' +
          '<button type="button" class="dp-seg__btn" id="dp-italic" title="Italic" style="font-style:italic;font-family:Georgia,serif">I</button>' +
        '</div>' +
      '</div>' +
      '<textarea class="dp-textarea" id="dp-text" placeholder="Add text"></textarea>' +
      '<div class="dp-row" style="margin-top:14px">' +
        '<div class="dp-row__label">Text alignment</div>' +
        '<div class="dp-row__control">' +
          '<select class="dp-select" id="dp-align-v" style="width:110px"><option>Top</option><option>Middle</option><option>Bottom</option></select>' +
          '<select class="dp-select" id="dp-align-h" style="width:120px"><option>Center</option><option>Left</option><option>Right</option></select>' +
        '</div>' +
      '</div>';
    panes.text = pText;

    /* Coordinates pane */
    var pCoords = el('div', 'dp-pane');
    pCoords.innerHTML =
      '<div class="dp-row">' +
        '<div class="dp-row__label">#1 (price, bar)</div>' +
        '<div class="dp-row__control">' +
          '<input type="number" step="0.05" class="dp-input dp-input--price" id="dp-c1-price" style="flex:1 1 auto">' +
          '<input type="number" step="1" class="dp-input dp-input--num" id="dp-c1-bar" style="flex:0 0 96px">' +
        '</div>' +
      '</div>' +
      '<div class="dp-row">' +
        '<div class="dp-row__label">#2 (price, bar)</div>' +
        '<div class="dp-row__control">' +
          '<input type="number" step="0.05" class="dp-input dp-input--price" id="dp-c2-price" style="flex:1 1 auto">' +
          '<input type="number" step="1" class="dp-input dp-input--num" id="dp-c2-bar" style="flex:0 0 96px">' +
        '</div>' +
      '</div>';
    panes.coordinates = pCoords;

    /* Visibility pane */
    var pVis = el('div', 'dp-pane');
    pVis.innerHTML = VIS_ROWS.map(function (r) {
      if (!r.num) {
        return '<div class="dp-row">' +
                 '<div class="dp-row__control"><label class="dp-check">' +
                   '<input type="checkbox" checked data-vis="' + r.id + '"> ' + r.label +
                 '</label></div>' +
               '</div>';
      }
      return '<div class="dp-row">' +
               '<div class="dp-row__control">' +
                 '<label class="dp-check" style="min-width:92px">' +
                   '<input type="checkbox" checked data-vis="' + r.id + '"> ' + r.label +
                 '</label>' +
                 '<input type="number" class="dp-input dp-input--num" data-vis-num="' + r.id + '" ' +
                   'min="1" max="' + r.max + '" value="' + r.val + '" style="flex:0 0 72px">' +
                 '<input type="range" class="dp-slider" data-vis-range="' + r.id + '" ' +
                   'min="1" max="' + r.max + '" value="' + r.val + '">' +
                 '<span class="dp-palette__pct" data-vis-max="' + r.id + '">' + r.max + '</span>' +
               '</div>' +
             '</div>';
    }).join('');
    panes.visibility = pVis;

    Object.keys(panes).forEach(function (k) { body.appendChild(panes[k]); });
    card.appendChild(body);

    /* ---- footer ---- */
    var foot = el('div', 'dp-card__foot');
    var template = el('button', 'dp-btn dp-btn--ghost');
    template.type = 'button';
    template.innerHTML = 'Template <span style="color:var(--dp-text-dim)">\u2304</span>';
    var cancel = el('button', 'dp-btn', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', function () { opts.onCancel && opts.onCancel(); });
    var ok = el('button', 'dp-btn dp-btn--primary', 'Ok');
    ok.type = 'button';
    ok.addEventListener('click', function () { opts.onOk && opts.onOk(card.getValues()); });
    foot.appendChild(template);
    foot.appendChild(el('div', 'dp-spacer'));
    foot.appendChild(cancel);
    foot.appendChild(ok);
    card.appendChild(foot);

    /* ---- tab switching ---- */
    function select(id) {
      Object.keys(panes).forEach(function (k) {
        panes[k].classList.toggle('is-active', k === id);
        tabBtns[k].classList.toggle('is-active', k === id);
      });
    }
    select(opts.tab || 'style');

    /* ---- line style buttons ---- */
    pStyle.querySelectorAll('#dp-style-line .dp-seg__btn').forEach(function (b) {
      b.addEventListener('click', function () {
        pStyle.querySelectorAll('#dp-style-line .dp-seg__btn').forEach(function (o) { o.classList.remove('is-active'); });
        b.classList.add('is-active');
      });
    });
    /* ---- visibility slider <-> number sync ---- */
    pVis.querySelectorAll('[data-vis-range]').forEach(function (range) {
      var id = range.dataset.visRange;
      var num = pVis.querySelector('[data-vis-num="' + id + '"]');
      range.addEventListener('input', function () { num.value = range.value; });
      num.addEventListener('input', function () { range.value = num.value; });
    });

    /* ---- bold / italic toggles (they had no state before) ---- */
    ['#dp-bold', '#dp-italic'].forEach(function (sel) {
      var b = pText.querySelector(sel);
      if (b) b.addEventListener('click', function () { b.classList.toggle('is-active'); });
    });

    card.selectTab = select;
    card.setTitle = function (t) { title.textContent = t; };
    card.setLineColor = function (c) { pStyle.querySelector('#dp-style-swatch').style.background = c; };
    card.setValues = function (v) {
      v = v || {};
      if (v.lineColor) card.setLineColor(v.lineColor);
      if (v.p1) {
        pCoords.querySelector('#dp-c1-price').value = v.p1.price;
        pCoords.querySelector('#dp-c1-bar').value = v.p1.bar;
      }
      if (v.p2) {
        pCoords.querySelector('#dp-c2-price').value = v.p2.price;
        pCoords.querySelector('#dp-c2-bar').value = v.p2.bar;
      }
    };
    card.getValues = function () {
      var vis = {};
      pVis.querySelectorAll('[data-vis]').forEach(function (c) { vis[c.dataset.vis] = c.checked; });
      var boldBtn = pText.querySelector('#dp-bold');
      var italicBtn = pText.querySelector('#dp-italic');
      var textSwatch = pText.querySelector('#dp-text-swatch');
      return {
        lineStyle: (pStyle.querySelector('#dp-style-line .is-active') || {}).dataset ?
                   pStyle.querySelector('#dp-style-line .is-active').dataset.line : 'solid',
        extend: pStyle.querySelector('#dp-extend').value,
        text: pText.querySelector('#dp-text').value,
        fontSize: pText.querySelector('#dp-font-size').value,
        textColor: (textSwatch && textSwatch.style.background) || '#d1d4dc',
        bold: !!(boldBtn && boldBtn.classList.contains('is-active')),
        italic: !!(italicBtn && italicBtn.classList.contains('is-active')),
        alignV: pText.querySelector('#dp-align-v').value,
        alignH: pText.querySelector('#dp-align-h').value,
        visibility: vis
      };
    };
    return card;
  }

  /* -------------------------------------------------------------------------
     4. ••• menu — the action list (TradingView layout)
     items: [{ id, label, icon, shortcut, danger, separatorBefore, chevron }]
     ---------------------------------------------------------------------- */
  function createMenu(items, opts) {
    opts = opts || {};
    var root = el('div', 'dp-menu');
    root.setAttribute('role', 'menu');

    (items || []).forEach(function (it) {
      if (it.separatorBefore) root.appendChild(el('div', 'dp-menu__sep'));
      var b = el('button', 'dp-menu__item' + (it.danger ? ' dp-menu__item--danger' : ''));
      b.type = 'button';
      b.setAttribute('role', 'menuitem');
      b.innerHTML = (it.icon || '') + '<span>' + it.label + '</span>' +
                    (it.shortcut ? '<kbd>' + it.shortcut + '</kbd>' : '') +
                    (it.chevron ? svg('<path d="M9 6l6 6-6 6"/>', 14).replace('class="', 'class="dp-menu__chev ') : '');
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        if (it.onSelect) it.onSelect();
        if (opts.onClose) opts.onClose();
      });
      root.appendChild(b);
    });

    ['pointerdown', 'mousedown', 'click'].forEach(function (ev) {
      root.addEventListener(ev, function (e) { e.stopPropagation(); });
    });
    return root;
  }

  window.DrawingProps = {
    PALETTE: PALETTE,
    ICON: ICON,
    createBar: createBar,
    createPalette: createPalette,
    createCard: createCard,
    createMenu: createMenu
  };
})();

/**
 * ui-icons.js - the site-wide UI icon set.
 *
 * One consistent icon language for chrome (buttons, tabs, headings, state
 * toggles, form fields), replacing emoji. Same shape as the drawing toolbar's
 * ICONS object: name -> inline SVG string, so it drops into existing innerHTML
 * interpolation without a build step.
 *
 * Style: 24x24 viewBox, stroke-width 2, stroke="currentColor" so every icon
 * inherits the colour of whatever it sits in (active/hover/muted states all
 * work with no per-icon colour rules).
 *
 * Size it with width/height (1em matches surrounding text) and nudge alignment
 * with vertical-align:-0.125em. See .ui-icon in the pages that use it.
 *
 * Deliberately NOT here: the emoji picker's own catalogue (a user-facing
 * feature) and the drawing-tool icons (fib/gann/pitchfork have no generic
 * equivalent and must stay tool-specific).
 */
(function () {
  'use strict';

  function svg(body) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
           'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
  }

  var UIIcons = {
    // ---- actions
    trash: svg('<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/><path d="M10 10v7M14 10v7"/>'),
    pencil: svg('<path d="M4 20h4L18 10l-4-4L4 16v4z"/><path d="M14 6l4 4"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    minus: svg('<path d="M5 12h14"/>'),
    x: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
    check: svg('<path d="M4 12.5l5 5L20 6.5"/>'),
    download: svg('<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M4 20h16"/>'),
    refresh: svg('<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/>'),
    settings: svg('<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M4.2 7.2l2.6 1.5M17.2 15.3l2.6 1.5M4.2 16.8l2.6-1.5M17.2 8.7l2.6-1.5"/>'),
    externalLink: svg('<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),

    // ---- status
    checkCircle: svg('<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>'),
    xCircle: svg('<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>'),
    alertTriangle: svg('<path d="M12 3.5L21.5 20H2.5L12 3.5z"/><path d="M12 10v4"/><path d="M12 17.5h.01"/>'),
    alertCircle: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5h.01"/>'),
    info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>'),
    ban: svg('<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>'),
    stopCircle: svg('<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1"/>'),
    zap: svg('<path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/>'),
    dot: svg('<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/>'),
    star: svg('<path d="M12 3.5l2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 10l6.1-.9z"/>'),
    award: svg('<circle cx="12" cy="9" r="6"/><path d="M8.5 14L7 22l5-3 5 3-1.5-8"/>'),

    // ---- visibility / security
    eye: svg('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>'),
    eyeOff: svg('<path d="M3 3l18 18"/><path d="M10.6 5.1A10.9 10.9 0 0 1 12 5c6.4 0 10 7 10 7a18 18 0 0 1-3.2 4"/><path d="M6.6 6.6A18 18 0 0 0 2 12s3.6 7 10 7a10.9 10.9 0 0 0 4-.8"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>'),
    lock: svg('<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>'),
    shield: svg('<path d="M12 3l8 3v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6l8-3z"/>'),

    // ---- communication / account
    mail: svg('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 7l10 6 10-6"/>'),
    bell: svg('<path d="M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6"/><path d="M10.5 20a2 2 0 0 0 3 0"/>'),
    user: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
    users: svg('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 5.2a3.5 3.5 0 0 1 0 6.6"/><path d="M18 20a6.5 6.5 0 0 0-2.2-4.9"/>'),
    hand: svg('<path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11"/><path d="M12 11V4.5a1.5 1.5 0 0 1 3 0V11"/><path d="M15 11V6.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-1a6 6 0 0 1-6-6v-3a1.5 1.5 0 0 1 3 0"/><path d="M6 12V9.5a1.5 1.5 0 0 1 3 0"/>'),

    // ---- objects / navigation
    clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>'),
    calendar: svg('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
    bookmark: svg('<path d="M6 3h12v18l-6-4-6 4V3z"/>'),
    folderOpen: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v1H3z"/><path d="M3 10h18l-2 9a2 2 0 0 1-2 1.6H5.5A2 2 0 0 1 3.6 19z"/>'),
    fileText: svg('<path d="M6 2h8l4 4v16H6z"/><path d="M14 2v4h4"/><path d="M9 13h6M9 17h6"/>'),
    newspaper: svg('<path d="M4 22h13a2 2 0 0 0 2-2V4H6v16a2 2 0 0 1-2 2z"/><path d="M19 8h2v12a2 2 0 0 1-2 2"/><path d="M9 8h6M9 12h6M9 16h4"/>'),
    list: svg('<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>'),
    briefcase: svg('<rect x="2" y="7" width="20" height="13" rx="2"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/><path d="M2 12h20"/>'),
    cloud: svg('<path d="M7 18a4.5 4.5 0 0 1 0-9 6 6 0 0 1 11.3 1.6A3.8 3.8 0 0 1 17.5 18z"/>'),

    // ---- market
    trendingUp: svg('<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>'),
    trendingDown: svg('<path d="M3 7l6 6 4-4 8 8"/><path d="M15 17h6v-6"/>'),
    barChart: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),

    // ---- theme
    sun: svg('<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>'),
    moon: svg('<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>')
  };

  window.UIIcons = UIIcons;

  /**
   * Convenience: a sized, aligned inline icon for innerHTML use.
   *   UIIcons.render('trash')            -> icon sized to the surrounding text
   *   UIIcons.render('trash', 16)        -> 16px
   *   UIIcons.render('trash', 16, 6)     -> 16px with 6px right margin
   */
  UIIcons.render = function (name, size, gap) {
    var icon = UIIcons[name];
    if (!icon) return '';
    var s = size || '1em';
    var px = (typeof s === 'number') ? s + 'px' : s;
    var style = 'width:' + px + ';height:' + px +
                ';vertical-align:-0.125em;display:inline-block;flex-shrink:0';
    if (gap) style += ';margin-right:' + gap + 'px';
    return icon.replace('<svg ', '<svg style="' + style + '" ');
  };
})();

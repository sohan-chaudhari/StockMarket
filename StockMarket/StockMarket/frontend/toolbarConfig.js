/**
 * toolbarConfig.js - Drawing Toolbar Configuration & State Management
 * Defines tool families, individual tools, SVG icons, and handles persistent favorites & last-selected state.
 */

(function () {
  'use strict';

  // --- SVG Icon Definitions (28x28 viewBox for pixel-perfect clarity) ---
  const ICONS = {
    // Cursors
    cursor: `<svg viewBox="0 0 28 28"><path d="M14 4V24M4 14H24" stroke="currentColor" stroke-width="2"/></svg>`,
    dot: `<svg viewBox="0 0 28 28"><circle cx="14" cy="14" r="4" fill="currentColor"/></svg>`,
    arrow: `<svg viewBox="0 0 28 28"><path d="M5 5L12 23L16 16L23 12L5 5Z" fill="currentColor"/></svg>`,
    eraser: `<svg viewBox="0 0 28 28"><path d="M8 20L20 8L24 12L12 24H8V20Z" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,

    // Lines & Channels
    trendline: `<svg viewBox="0 0 28 28"><path d="M4 24L24 4" stroke="currentColor" stroke-width="2"/></svg>`,
    ray: `<svg viewBox="0 0 28 28"><path d="M4 24L24 4M20 4H24V8" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    infoline: `<svg viewBox="0 0 28 28"><path d="M4 24L24 4" stroke="currentColor" stroke-width="2"/><text x="14" y="10" font-size="8" font-family="Arial" font-weight="bold" fill="currentColor">i</text></svg>`,
    extended: `<svg viewBox="0 0 28 28"><path d="M2 26L26 2" stroke="currentColor" stroke-width="2"/></svg>`,
    trend_angle: `<svg viewBox="0 0 28 28"><path d="M4 24L24 4" stroke="currentColor" stroke-width="2"/><text x="13" y="11" font-size="7" font-family="Arial" fill="currentColor">30°</text></svg>`,
    horizontal_line: `<svg viewBox="0 0 28 28"><path d="M2 14H26" stroke="currentColor" stroke-width="2"/></svg>`,
    horizontal_ray: `<svg viewBox="0 0 28 28"><path d="M14 14H26M22 10L26 14L22 18" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    vertical_line: `<svg viewBox="0 0 28 28"><path d="M14 2V26" stroke="currentColor" stroke-width="2"/></svg>`,
    cross_line: `<svg viewBox="0 0 28 28"><path d="M14 2V26M2 14H26" stroke="currentColor" stroke-width="2"/></svg>`,
    channel: `<svg viewBox="0 0 28 28"><path d="M4 20L20 4M8 24L24 8" stroke="currentColor" stroke-width="2"/></svg>`,
    pitchfork: `<svg viewBox="0 0 28 28"><path d="M2 14H6L10 6M10 22L6 14M10 6H26M10 22H26M6 14H26" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    flat_top_channel: `<svg viewBox="0 0 28 28"><path d="M4 8H24M4 20L24 14" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    flat_bottom_channel: `<svg viewBox="0 0 28 28"><path d="M4 20H24M4 8L24 14" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    disjoint_channel: `<svg viewBox="0 0 28 28"><path d="M4 18L14 8M10 22L24 4M14 8H10M14 8L24 4" stroke="currentColor" stroke-width="2"/></svg>`,
    regression_trend: `<svg viewBox="0 0 28 28"><line x1="4" y1="20" x2="24" y2="8" stroke="currentColor" stroke-width="2" stroke-dasharray="3,2"/><circle cx="4" cy="20" r="2" fill="currentColor"/><circle cx="24" cy="8" r="2" fill="currentColor"/></svg>`,
    schiff_pitchfork: `<svg viewBox="0 0 28 28"><path d="M2 22L14 6M14 6L26 22M10 22L14 14" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    modified_schiff_pitchfork: `<svg viewBox="0 0 28 28"><path d="M2 22L14 8M14 8L26 22M6 22L14 14M22 22L14 14" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    inside_pitchfork: `<svg viewBox="0 0 28 28"><path d="M2 22L14 6M14 6L26 22M6 18L14 10M22 18L14 10" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,

    // Gann & Fibonacci
    fib_retracement: `<svg viewBox="0 0 28 28"><path d="M2 24H26M2 18H26M2 12H26M2 6H26" stroke="currentColor" stroke-width="1.5"/></svg>`,
    fib_trend_ext: `<svg viewBox="0 0 28 28"><path d="M2 24L10 6L18 24M2 24H18" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>`,
    fib_channel: `<svg viewBox="0 0 28 28"><path d="M2 24L24 2M6 26L26 6" stroke="currentColor" stroke-width="1.5"/></svg>`,
    fib_fan: `<svg viewBox="0 0 28 28"><path d="M14 26L14 2M14 26L26 14M14 26L2 14" stroke="currentColor" stroke-width="1.5"/></svg>`,
    fib_time_zone: `<svg viewBox="0 0 28 28"><path d="M4 4V24M10 4V24M16 4V24M22 4V24" stroke="currentColor" stroke-width="1.5"/></svg>`,
    fib_circles: `<svg viewBox="0 0 28 28"><circle cx="14" cy="14" r="4" stroke="currentColor" fill="none" stroke-width="1"/><circle cx="14" cy="14" r="8" stroke="currentColor" fill="none" stroke-width="1"/><circle cx="14" cy="14" r="12" stroke="currentColor" fill="none" stroke-width="1"/></svg>`,
    fib_arcs: `<svg viewBox="0 0 28 28"><path d="M4 14A10 10 0 0 1 24 14" stroke="currentColor" fill="none" stroke-width="1"/><path d="M4 14A6 6 0 0 1 24 14" stroke="currentColor" fill="none" stroke-width="1"/><path d="M4 14A2 2 0 0 1 24 14" stroke="currentColor" fill="none" stroke-width="1"/></svg>`,
    fib_speed_resistance: `<svg viewBox="0 0 28 28"><path d="M2 24L26 2M2 24L26 10M2 24L26 16" stroke="currentColor" stroke-width="1"/></svg>`,
    fib_spiral: `<svg viewBox="0 0 28 28"><path d="M14 14C10 14 8 16 8 18C8 20 10 22 14 22C18 22 22 18 22 14C22 10 18 6 14 6" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    fib_wedge: `<svg viewBox="0 0 28 28"><path d="M4 24L10 6M4 24L24 6M10 6L24 6" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>`,
    pitchfan: `<svg viewBox="0 0 28 28"><path d="M2 24L14 4M14 4L26 24M6 24L14 14M22 24L14 14" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>`,
    gann_box: `<svg viewBox="0 0 28 28"><rect x="4" y="4" width="20" height="20" stroke="currentColor" fill="none"/><path d="M4 4L24 24M4 24L24 4" stroke="currentColor"/></svg>`,
    gann_fan: `<svg viewBox="0 0 28 28"><path d="M4 24L24 4M4 24L24 10M4 24L24 16M4 24L16 4M4 24L10 4" stroke="currentColor" stroke-width="1"/></svg>`,
    gann_square: `<svg viewBox="0 0 28 28"><rect x="4" y="4" width="20" height="20" stroke="currentColor" fill="none"/><path d="M4 4L24 24M4 24L24 4" stroke="currentColor"/></svg>`,
    gann_square_fixed: `<svg viewBox="0 0 28 28"><rect x="6" y="4" width="16" height="20" stroke="currentColor" fill="none"/><path d="M6 4L22 24" stroke="currentColor"/></svg>`,

    // Shapes
    brush: `<svg viewBox="0 0 28 28"><path d="M5 20C5 20 8 15 12 15C16 15 18 20 22 20" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    highlighter: `<svg viewBox="0 0 28 28"><path d="M4 14H24" stroke="currentColor" stroke-width="8" opacity="0.5"/></svg>`,
    rectangle: `<svg viewBox="0 0 28 28"><rect x="4" y="8" width="20" height="12" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    circle: `<svg viewBox="0 0 28 28"><circle cx="14" cy="14" r="10" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    ellipse: `<svg viewBox="0 0 28 28"><ellipse cx="14" cy="14" rx="10" ry="6" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    path: `<svg viewBox="0 0 28 28"><path d="M4 24L8 10L14 20L20 4L24 16" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    triangle: `<svg viewBox="0 0 28 28"><path d="M14 4L4 24H24L14 4Z" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,

    // Text & Annotations
    text: `<svg viewBox="0 0 28 28"><text x="7" y="21" font-size="20" font-family="Arial" font-weight="bold" fill="currentColor">T</text></svg>`,
    anchored_text: `<svg viewBox="0 0 28 28"><path d="M4 24V4H24" stroke="currentColor" stroke-width="2" fill="none"/><text x="8" y="20" font-size="14" font-family="Arial" fill="currentColor">T</text></svg>`,
    note: `<svg viewBox="0 0 28 28"><path d="M4 4H20V16H8L4 20V4Z" stroke="currentColor" fill="none" stroke-width="1.5"/><circle cx="12" cy="10" r="1" fill="currentColor"/></svg>`,
    callout: `<svg viewBox="0 0 28 28"><path d="M4 6H20V18H10L4 24V6Z" stroke="currentColor" fill="none" stroke-width="1.5"/><text x="7" y="14" font-size="9" font-family="Arial" fill="currentColor">Hi</text></svg>`,
    price_label: `<svg viewBox="0 0 28 28"><path d="M4 8H18L22 14L18 20H4V8Z" stroke="currentColor" fill="none" stroke-width="1.5"/><text x="6" y="16" font-size="8" font-family="Arial" fill="currentColor">₹</text></svg>`,
    arrow_marker: `<svg viewBox="0 0 28 28"><path d="M14 24V4 M10 8L14 4L18 8" stroke="currentColor" stroke-width="1.5"/></svg>`,

    // Patterns
    gartley: `<svg viewBox="0 0 28 28"><path d="M4 20L8 10L12 18L16 6L24 16" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    butterfly: `<svg viewBox="0 0 28 28"><path d="M4 20L10 6L16 18L22 4" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    bat: `<svg viewBox="0 0 28 28"><path d="M4 22L10 8L16 16L24 6" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    crab: `<svg viewBox="0 0 28 28"><path d="M4 22L12 6L18 18L26 4" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    deep_crab: `<svg viewBox="0 0 28 28"><path d="M4 22L12 8L18 16L26 4" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    shark: `<svg viewBox="0 0 28 28"><path d="M4 20L10 4L16 16L24 10" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    xabcd_pattern: `<svg viewBox="0 0 28 28"><path d="M4 20L8 10L12 18L16 6L24 16" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    cypher_pattern: `<svg viewBox="0 0 28 28"><path d="M4 22L10 8L14 18L20 6L24 14" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    abcd_pattern: `<svg viewBox="0 0 28 28"><path d="M4 20L10 6L18 20L24 6" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    head_and_shoulders: `<svg viewBox="0 0 28 28"><path d="M2 20L6 10L10 18L14 6L18 18L22 10L26 20" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    inverse_head_and_shoulders: `<svg viewBox="0 0 28 28"><path d="M2 10L6 20L10 12L14 24L18 12L22 20L26 10" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    double_top: `<svg viewBox="0 0 28 28"><path d="M4 8L10 4L16 8L22 4" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M4 18L10 14L16 18L22 14" stroke="currentColor" fill="none" stroke-width="1" opacity="0.5"/></svg>`,
    double_bottom: `<svg viewBox="0 0 28 28"><path d="M4 22L10 18L16 22L22 18" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M4 12L10 8L16 12L22 8" stroke="currentColor" fill="none" stroke-width="1" opacity="0.5"/></svg>`,
    triple_top: `<svg viewBox="0 0 28 28"><path d="M4 8L8 4L12 8L16 4L20 8L24 4" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    triple_bottom: `<svg viewBox="0 0 28 28"><path d="M4 22L8 18L12 22L16 18L20 22L24 18" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    triangle_pattern: `<svg viewBox="0 0 28 28"><path d="M4 20L14 4L24 20H4Z" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    ascending_triangle: `<svg viewBox="0 0 28 28"><path d="M4 20L14 4L24 20H4Z" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M4 20H24" stroke="currentColor" stroke-width="1.5"/></svg>`,
    descending_triangle: `<svg viewBox="0 0 28 28"><path d="M4 4L14 24L24 4V4" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M4 4H24" stroke="currentColor" stroke-width="1.5"/></svg>`,
    symmetrical_triangle: `<svg viewBox="0 0 28 28"><path d="M4 20L14 4L24 20L4 4" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    expanding_triangle: `<svg viewBox="0 0 28 28"><path d="M14 4L4 14L14 24L24 14Z" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    rising_wedge: `<svg viewBox="0 0 28 28"><path d="M4 22L14 4L24 22" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M8 22L14 8L20 22" stroke="currentColor" fill="none" stroke-width="1" opacity="0.5"/></svg>`,
    falling_wedge: `<svg viewBox="0 0 28 28"><path d="M4 6L14 24L24 6" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M8 6L14 20L20 6" stroke="currentColor" fill="none" stroke-width="1" opacity="0.5"/></svg>`,
    ascending_channel: `<svg viewBox="0 0 28 28"><path d="M4 20L24 4M8 24L28 8" stroke="currentColor" stroke-width="1.5"/></svg>`,
    descending_channel: `<svg viewBox="0 0 28 28"><path d="M4 4L24 20M8 8L28 24" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    elliott_impulse: `<svg viewBox="0 0 28 28"><path d="M2 20L6 10L10 18L14 6L18 18" stroke="currentColor" fill="none" stroke-width="1.5"/><text x="20" y="10" font-size="8" font-family="Arial" fill="currentColor">5</text></svg>`,
    elliott_correction: `<svg viewBox="0 0 28 28"><path d="M4 20L12 6L24 16" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    elliott_triangle: `<svg viewBox="0 0 28 28"><path d="M2 20L8 10L14 18L20 12L26 16" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    elliott_double_combo: `<svg viewBox="0 0 28 28"><path d="M2 20L6 10L10 18L14 8L18 16L22 6L26 16" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    elliott_triple_combo: `<svg viewBox="0 0 28 28"><path d="M2 20L6 8L10 18L14 6L18 16L22 6L26 18" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    elliott_flat: `<svg viewBox="0 0 28 28"><path d="M4 20L10 10L16 18L24 6" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    elliott_zigzag: `<svg viewBox="0 0 28 28"><path d="M4 20L12 6L20 18L26 8" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,
    elliott_combination: `<svg viewBox="0 0 28 28"><path d="M4 20L10 8L16 16L22 6" stroke="currentColor" fill="none" stroke-width="1.5"/></svg>`,

    // Prediction & Measure
    long_position: `<svg viewBox="0 0 28 28"><rect x="4" y="4" width="20" height="10" fill="#089981" opacity="0.6"/><rect x="4" y="14" width="20" height="10" fill="#f23645" opacity="0.6"/><line x1="4" y1="14" x2="24" y2="14" stroke="#fff" stroke-width="1.5"/></svg>`,
    short_position: `<svg viewBox="0 0 28 28"><rect x="4" y="4" width="20" height="10" fill="#f23645" opacity="0.6"/><rect x="4" y="14" width="20" height="10" fill="#089981" opacity="0.6"/><line x1="4" y1="14" x2="24" y2="14" stroke="#fff" stroke-width="1.5"/></svg>`,
    date_range: `<svg viewBox="0 0 28 28"><path d="M4 14H24M4 10V18M24 10V18" stroke="currentColor" stroke-width="2"/></svg>`,
    price_range: `<svg viewBox="0 0 28 28"><path d="M14 4V24M10 4H18M10 24H18" stroke="currentColor" stroke-width="2"/></svg>`,
    date_price_range: `<svg viewBox="0 0 28 28"><rect x="4" y="4" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none"/><line x1="4" y1="14" x2="24" y2="14" stroke="currentColor" stroke-dasharray="2,2"/></svg>`,

    // Rich Objects & Stickers
    sticker_buy: `<svg viewBox="0 0 28 28"><text x="7" y="21" font-size="18" font-family="Arial" font-weight="bold" fill="#089981">B</text></svg>`,
    sticker_sell: `<svg viewBox="0 0 28 28"><text x="7" y="21" font-size="18" font-family="Arial" font-weight="bold" fill="#f23645">S</text></svg>`,
    sticker_long: `<svg viewBox="0 0 28 28"><text x="7" y="21" font-size="18" font-family="Arial" font-weight="bold" fill="#2962ff">L</text></svg>`,
    sticker_short: `<svg viewBox="0 0 28 28"><text x="4" y="21" font-size="16" font-family="Arial" font-weight="bold" fill="#e91e63">S↓</text></svg>`,
    sticker_target: `<svg viewBox="0 0 28 28"><circle cx="14" cy="14" r="10" stroke="currentColor" fill="none" stroke-width="1.5"/><circle cx="14" cy="14" r="4" fill="currentColor"/></svg>`,
    sticker_stop: `<svg viewBox="0 0 28 28"><rect x="6" y="6" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.5"/><line x1="10" y1="10" x2="18" y2="18" stroke="currentColor" stroke-width="1.5"/></svg>`,
    sticker_star: `<svg viewBox="0 0 28 28"><path d="M14 2L17 10H26L19 15L22 24L14 19L6 24L9 15L2 10H11Z" fill="currentColor"/></svg>`,
    sticker_pin: `<svg viewBox="0 0 28 28"><path d="M14 2C10 2 6 6 6 10C6 16 14 26 14 26S22 16 22 10C22 6 18 2 14 2Z" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="14" cy="10" r="3" fill="currentColor"/></svg>`,
    sticker_check: `<svg viewBox="0 0 28 28"><circle cx="14" cy="14" r="10" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M8 14L12 18L20 10" stroke="currentColor" fill="none" stroke-width="2"/></svg>`,
    sticker_warning: `<svg viewBox="0 0 28 28"><path d="M14 2L2 26H26L14 2Z" stroke="currentColor" fill="none" stroke-width="1.5"/><line x1="14" y1="10" x2="14" y2="18" stroke="currentColor" stroke-width="2"/><circle cx="14" cy="22" r="1" fill="currentColor"/></svg>`,
    emoji: `<svg viewBox="0 0 28 28"><circle cx="14" cy="14" r="10" stroke="currentColor" fill="none" stroke-width="1.5"/><circle cx="10" cy="11" r="1.5" fill="currentColor"/><circle cx="18" cy="11" r="1.5" fill="currentColor"/><path d="M9 16C10.5 19 17.5 19 19 16" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>`,
    icon: `<svg viewBox="0 0 28 28"><path d="M16 2L6 14H14L12 26L22 14H14L16 2Z" fill="currentColor"/></svg>`,
    symbol: `<svg viewBox="0 0 28 28"><text x="7" y="21" font-size="18" font-family="Arial" font-weight="bold" fill="currentColor">₹</text></svg>`,
    image: `<svg viewBox="0 0 28 28"><rect x="4" y="6" width="20" height="16" stroke="currentColor" fill="none" stroke-width="1.5"/><circle cx="10" cy="12" r="2" fill="currentColor"/><path d="M4 20L10 14L14 18L18 12L24 20" stroke="currentColor" fill="none" stroke-width="1"/></svg>`,
    text_note: `<svg viewBox="0 0 28 28"><path d="M4 4H20V16H8L4 20V4Z" stroke="currentColor" fill="none" stroke-width="1.5"/><text x="8" y="14" font-size="10" font-family="Arial" fill="currentColor">T</text></svg>`,
    balloon: `<svg viewBox="0 0 28 28"><ellipse cx="14" cy="12" rx="10" ry="8" stroke="currentColor" fill="none" stroke-width="1.5"/><path d="M14 20L10 26H18L14 20Z" fill="currentColor"/></svg>`,
    arrow_label: `<svg viewBox="0 0 28 28"><path d="M4 14L18 14M14 10L18 14L14 18" stroke="currentColor" fill="none" stroke-width="1.5"/><rect x="18" y="8" width="8" height="12" stroke="currentColor" fill="none" stroke-width="1"/></svg>`,

    // Utilities & Measure
    measure: `<svg viewBox="0 0 28 28"><path d="M4 24L24 4M4 14L14 4M14 24L24 14" stroke="currentColor" stroke-width="1.5"/></svg>`,
    zoom: `<svg viewBox="0 0 28 28"><circle cx="12" cy="12" r="8" stroke="currentColor" fill="none" stroke-width="1.5"/><line x1="18" y1="18" x2="24" y2="24" stroke="currentColor" stroke-width="2"/></svg>`,

    // Actions & Toggles
    magnet: `<svg viewBox="0 0 28 28"><path d="M4 10C4 6 7 2 14 2C21 2 24 6 24 10V16H20V10C20 8 18 6 14 6C10 6 8 8 8 10V16H4V10Z" fill="currentColor"/></svg>`,
    stay_mode: `<svg viewBox="0 0 28 28"><rect x="6" y="12" width="16" height="12" rx="2" stroke="currentColor" stroke-width="2" fill="none"/><path d="M14 12V6C14 4 15 2 17 2" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    lock_all: `<svg viewBox="0 0 28 28"><rect x="6" y="12" width="16" height="12" rx="2" fill="currentColor"/><path d="M14 12V6C14 4 15 2 17 2H11C13 2 14 4 14 6" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    hide_all: `<svg viewBox="0 0 28 28"><path d="M2 14Q7 4 14 4Q21 4 26 14Q21 24 14 24Q7 24 2 14Z" stroke="currentColor" stroke-width="2" fill="none"/><circle cx="14" cy="14" r="4" fill="currentColor"/></svg>`,
    trash: `<svg viewBox="0 0 28 28"><path d="M6 8H22M10 8V24H18V8M8 8V4H20V8" stroke="currentColor" stroke-width="2" fill="none"/></svg>`,
    star_empty: `<svg viewBox="0 0 24 24" class="star-icon"><path d="M12 17.27L18.18 21L16.54 13.97L22 9.24L14.81 8.63L12 2L9.19 8.63L2 9.24L7.46 13.97L5.82 21L12 17.27Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
    star_filled: `<svg viewBox="0 0 24 24" class="star-icon"><path d="M12 17.27L18.18 21L16.54 13.97L22 9.24L14.81 8.63L12 2L9.19 8.63L2 9.24L7.46 13.97L5.82 21L12 17.27Z" fill="#fbc02d" stroke="#fbc02d" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
    drag_grip: `<svg viewBox="0 0 10 24" class="grip-icon"><circle cx="3" cy="6" r="1.5" fill="currentColor"/><circle cx="7" cy="6" r="1.5" fill="currentColor"/><circle cx="3" cy="12" r="1.5" fill="currentColor"/><circle cx="7" cy="12" r="1.5" fill="currentColor"/><circle cx="3" cy="18" r="1.5" fill="currentColor"/><circle cx="7" cy="18" r="1.5" fill="currentColor"/></svg>`
  };

  // --- Nested Tool Families Configuration ---
  const TOOLBAR_FAMILIES = [
    {
      id: 'cursor',
      title: 'Cursors & Pointer',
      defaultToolId: 'cursor',
      items: [
        { id: 'cursor', name: 'Cross', icon: ICONS.cursor, shortcut: '' },
        { id: 'dot', name: 'Dot', icon: ICONS.dot, shortcut: '' },
        { id: 'arrow', name: 'Arrow', icon: ICONS.arrow, shortcut: '' },
        { id: 'eraser', name: 'Eraser', icon: ICONS.eraser, shortcut: '' }
      ]
    },
    {
      id: 'lines',
      title: 'Trend Line Tools',
      defaultToolId: 'trendline',
      groups: [
        {
          header: 'LINES',
          items: [
            { id: 'trendline', name: 'Trend Line', icon: ICONS.trendline, shortcut: 'Alt+T' },
            { id: 'ray', name: 'Ray', icon: ICONS.ray, shortcut: '' },
            { id: 'infoline', name: 'Info Line', icon: ICONS.infoline, shortcut: '' },
            { id: 'extended', name: 'Extended Line', icon: ICONS.extended, shortcut: 'Alt+E' },
            { id: 'trend_angle', name: 'Trend Angle', icon: ICONS.trend_angle, shortcut: '' },
            { id: 'horizontal_line', name: 'Horizontal Line', icon: ICONS.horizontal_line, shortcut: 'Alt+H' },
            { id: 'horizontal_ray', name: 'Horizontal Ray', icon: ICONS.horizontal_ray, shortcut: 'Alt+J' },
            { id: 'vertical_line', name: 'Vertical Line', icon: ICONS.vertical_line, shortcut: 'Alt+V' },
            { id: 'cross_line', name: 'Cross Line', icon: ICONS.cross_line, shortcut: 'Alt+C' }
          ]
        },
        {
          header: 'CHANNELS & PITCHFORKS',
          items: [
            { id: 'channel', name: 'Parallel Channel', icon: ICONS.channel, shortcut: '' },
            { id: 'pitchfork', name: 'Pitchfork', icon: ICONS.pitchfork, shortcut: '' },
            { id: 'flat_top_channel', name: 'Flat Top Channel', icon: ICONS.flat_top_channel, shortcut: '' },
            { id: 'flat_bottom_channel', name: 'Flat Bottom Channel', icon: ICONS.flat_bottom_channel, shortcut: '' },
            { id: 'disjoint_channel', name: 'Disjoint Channel', icon: ICONS.disjoint_channel, shortcut: '' },
            { id: 'regression_trend', name: 'Regression Trend', icon: ICONS.regression_trend, shortcut: '' },
            { id: 'schiff_pitchfork', name: 'Schiff Pitchfork', icon: ICONS.schiff_pitchfork, shortcut: '' },
            { id: 'modified_schiff_pitchfork', name: 'Modified Schiff Pitchfork', icon: ICONS.modified_schiff_pitchfork, shortcut: '' },
            { id: 'inside_pitchfork', name: 'Inside Pitchfork', icon: ICONS.inside_pitchfork, shortcut: '' }
          ]
        }
      ]
    },
    {
      id: 'fib',
      title: 'Gann and Fibonacci Tools',
      defaultToolId: 'fib_retracement',
      groups: [
        {
          header: 'FIBONACCI',
          items: [
            { id: 'fib_retracement', name: 'Fib Retracement', icon: ICONS.fib_retracement, shortcut: 'Alt+F' },
            { id: 'fib_trend_ext', name: 'Trend-Based Fib Extension', icon: ICONS.fib_trend_ext, shortcut: '' },
            { id: 'fib_channel', name: 'Fib Channel', icon: ICONS.fib_channel, shortcut: '' },
            { id: 'fib_fan', name: 'Fib Fan', icon: ICONS.fib_fan, shortcut: '' },
            { id: 'fib_time_zone', name: 'Fib Time Zone', icon: ICONS.fib_time_zone, shortcut: '' },
            { id: 'fib_circles', name: 'Fib Circles', icon: ICONS.fib_circles, shortcut: '' },
            { id: 'fib_arcs', name: 'Fib Arcs', icon: ICONS.fib_arcs, shortcut: '' },
            { id: 'fib_speed_resistance', name: 'Speed Resistance Fan', icon: ICONS.fib_speed_resistance, shortcut: '' },
            { id: 'fib_spiral', name: 'Fib Spiral', icon: ICONS.fib_spiral, shortcut: '' },
            { id: 'fib_wedge', name: 'Fib Wedge', icon: ICONS.fib_wedge, shortcut: '' },
            { id: 'pitchfan', name: 'Pitchfan', icon: ICONS.pitchfan, shortcut: '' }
          ]
        },
        {
          header: 'GANN',
          items: [
            { id: 'gann_box', name: 'Gann Box', icon: ICONS.gann_box, shortcut: '' },
            { id: 'gann_fan', name: 'Gann Fan', icon: ICONS.gann_fan, shortcut: '' },
            { id: 'gann_square', name: 'Gann Square', icon: ICONS.gann_square, shortcut: '' },
            { id: 'gann_square_fixed', name: 'Gann Square Fixed', icon: ICONS.gann_square_fixed, shortcut: '' }
          ]
        }
      ]
    },
    {
      id: 'shapes',
      title: 'Geometric Shapes',
      defaultToolId: 'rectangle',
      items: [
        { id: 'brush', name: 'Brush', icon: ICONS.brush, shortcut: 'Alt+B' },
        { id: 'highlighter', name: 'Highlighter', icon: ICONS.highlighter, shortcut: '' },
        { id: 'rectangle', name: 'Rectangle', icon: ICONS.rectangle, shortcut: 'Alt+R' },
        { id: 'circle', name: 'Circle', icon: ICONS.circle, shortcut: '' },
        { id: 'ellipse', name: 'Ellipse', icon: ICONS.ellipse, shortcut: '' },
        { id: 'path', name: 'Path', icon: ICONS.path, shortcut: '' },
        { id: 'triangle', name: 'Triangle', icon: ICONS.triangle, shortcut: '' }
      ]
    },
    {
      id: 'text',
      title: 'Annotation Tools',
      defaultToolId: 'text',
      items: [
        { id: 'text', name: 'Text', icon: ICONS.text, shortcut: 'Alt+N' },
        { id: 'anchored_text', name: 'Anchored Text', icon: ICONS.anchored_text, shortcut: '' },
        { id: 'note', name: 'Note', icon: ICONS.note, shortcut: '' },
        { id: 'callout', name: 'Callout', icon: ICONS.callout, shortcut: '' },
        { id: 'price_label', name: 'Price Label', icon: ICONS.price_label, shortcut: '' },
        { id: 'arrow_marker', name: 'Arrow Marker', icon: ICONS.arrow_marker, shortcut: '' }
      ]
    },
    {
      id: 'patterns',
      title: 'Patterns',
      defaultToolId: 'head_and_shoulders',
      groups: [
        {
          header: 'HARMONIC PATTERNS',
          items: [
            { id: 'gartley', name: 'Gartley', icon: ICONS.gartley, shortcut: '' },
            { id: 'butterfly', name: 'Butterfly', icon: ICONS.butterfly, shortcut: '' },
            { id: 'bat', name: 'Bat', icon: ICONS.bat, shortcut: '' },
            { id: 'crab', name: 'Crab', icon: ICONS.crab, shortcut: '' },
            { id: 'deep_crab', name: 'Deep Crab', icon: ICONS.deep_crab, shortcut: '' },
            { id: 'shark', name: 'Shark', icon: ICONS.shark, shortcut: '' },
            { id: 'xabcd_pattern', name: 'XABCD Pattern', icon: ICONS.xabcd_pattern, shortcut: '' },
            { id: 'cypher_pattern', name: 'Cypher Pattern', icon: ICONS.cypher_pattern, shortcut: '' },
            { id: 'abcd_pattern', name: 'ABCD Pattern', icon: ICONS.abcd_pattern, shortcut: '' }
          ]
        },
        {
          header: 'CHART PATTERNS',
          items: [
            { id: 'head_and_shoulders', name: 'Head & Shoulders', icon: ICONS.head_and_shoulders, shortcut: '' },
            { id: 'inverse_head_and_shoulders', name: 'Inverse Head & Shoulders', icon: ICONS.inverse_head_and_shoulders, shortcut: '' },
            { id: 'double_top', name: 'Double Top', icon: ICONS.double_top, shortcut: '' },
            { id: 'double_bottom', name: 'Double Bottom', icon: ICONS.double_bottom, shortcut: '' },
            { id: 'triple_top', name: 'Triple Top', icon: ICONS.triple_top, shortcut: '' },
            { id: 'triple_bottom', name: 'Triple Bottom', icon: ICONS.triple_bottom, shortcut: '' },
            { id: 'triangle_pattern', name: 'Triangle Pattern', icon: ICONS.triangle_pattern, shortcut: '' }
          ]
        },
        {
          header: 'TRIANGLES & WEDGES',
          items: [
            { id: 'ascending_triangle', name: 'Ascending Triangle', icon: ICONS.ascending_triangle, shortcut: '' },
            { id: 'descending_triangle', name: 'Descending Triangle', icon: ICONS.descending_triangle, shortcut: '' },
            { id: 'symmetrical_triangle', name: 'Symmetrical Triangle', icon: ICONS.symmetrical_triangle, shortcut: '' },
            { id: 'expanding_triangle', name: 'Expanding Triangle', icon: ICONS.expanding_triangle, shortcut: '' },
            { id: 'rising_wedge', name: 'Rising Wedge', icon: ICONS.rising_wedge, shortcut: '' },
            { id: 'falling_wedge', name: 'Falling Wedge', icon: ICONS.falling_wedge, shortcut: '' },
            { id: 'ascending_channel', name: 'Ascending Channel', icon: ICONS.ascending_channel, shortcut: '' },
            { id: 'descending_channel', name: 'Descending Channel', icon: ICONS.descending_channel, shortcut: '' }
          ]
        },
        {
          header: 'ELLIOTT WAVES',
          items: [
            { id: 'elliott_impulse', name: 'Elliott Impulse Wave (12345)', icon: ICONS.elliott_impulse, shortcut: '' },
            { id: 'elliott_correction', name: 'Elliott Correction (ABC)', icon: ICONS.elliott_correction, shortcut: '' },
            { id: 'elliott_triangle', name: 'Elliott Triangle (ABCDE)', icon: ICONS.elliott_triangle, shortcut: '' },
            { id: 'elliott_double_combo', name: 'Elliott Double Combo (WXY)', icon: ICONS.elliott_double_combo, shortcut: '' },
            { id: 'elliott_triple_combo', name: 'Elliott Triple Combo', icon: ICONS.elliott_triple_combo, shortcut: '' },
            { id: 'elliott_flat', name: 'Elliott Flat', icon: ICONS.elliott_flat, shortcut: '' },
            { id: 'elliott_zigzag', name: 'Elliott ZigZag', icon: ICONS.elliott_zigzag, shortcut: '' },
            { id: 'elliott_combination', name: 'Elliott Combination', icon: ICONS.elliott_combination, shortcut: '' }
          ]
        }
      ]
    },
    {
      id: 'prediction',
      title: 'Prediction and Measurement',
      defaultToolId: 'long_position',
      items: [
        { id: 'long_position', name: 'Long Position', icon: ICONS.long_position, shortcut: '' },
        { id: 'short_position', name: 'Short Position', icon: ICONS.short_position, shortcut: '' },
        { id: 'price_range', name: 'Price Range', icon: ICONS.price_range, shortcut: 'Shift+Click' },
        { id: 'date_range', name: 'Date Range', icon: ICONS.date_range, shortcut: '' },
        { id: 'date_price_range', name: 'Date & Price Range', icon: ICONS.date_price_range, shortcut: '' }
      ]
    },
    {
      id: 'objects',
      title: 'Stickers & Rich Objects',
      defaultToolId: 'sticker_star',
      groups: [
        {
          header: 'STICKERS',
          items: [
            { id: 'sticker_buy', name: 'Buy Sticker', icon: ICONS.sticker_buy, shortcut: '' },
            { id: 'sticker_sell', name: 'Sell Sticker', icon: ICONS.sticker_sell, shortcut: '' },
            { id: 'sticker_long', name: 'Long Sticker', icon: ICONS.sticker_long, shortcut: '' },
            { id: 'sticker_short', name: 'Short Sticker', icon: ICONS.sticker_short, shortcut: '' },
            { id: 'sticker_target', name: 'Target Sticker', icon: ICONS.sticker_target, shortcut: '' },
            { id: 'sticker_stop', name: 'Stop Sticker', icon: ICONS.sticker_stop, shortcut: '' },
            { id: 'sticker_star', name: 'Star Sticker', icon: ICONS.sticker_star, shortcut: '' },
            { id: 'sticker_pin', name: 'Pin Sticker', icon: ICONS.sticker_pin, shortcut: '' },
            { id: 'sticker_check', name: 'Check Sticker', icon: ICONS.sticker_check, shortcut: '' },
            { id: 'sticker_warning', name: 'Warning Sticker', icon: ICONS.sticker_warning, shortcut: '' }
          ]
        },
        {
          header: 'ICONS & ANNOTATIONS',
          items: [
            { id: 'emoji', name: 'Emoji', icon: ICONS.emoji, shortcut: '' },
            { id: 'icon', name: 'Icon', icon: ICONS.icon, shortcut: '' },
            { id: 'symbol', name: 'Symbol', icon: ICONS.symbol, shortcut: '' },
            { id: 'image', name: 'Image', icon: ICONS.image, shortcut: '' },
            { id: 'text_note', name: 'Text Note', icon: ICONS.text_note, shortcut: '' },
            { id: 'balloon', name: 'Balloon', icon: ICONS.balloon, shortcut: '' },
            { id: 'arrow_label', name: 'Arrow Label', icon: ICONS.arrow_label, shortcut: '' }
          ]
        }
      ]
    },
    {
      id: 'utils',
      title: 'Utility Tools',
      defaultToolId: 'measure',
      items: [
        { id: 'measure', name: 'Measure', icon: ICONS.measure, shortcut: 'Shift+Drag' },
        { id: 'zoom', name: 'Zoom', icon: ICONS.zoom, shortcut: '' }
      ]
    }
  ];

  // Flat lookup dictionary mapping toolId -> { id, name, icon, familyId, shortcut }
  const ALL_TOOLS_MAP = {};
  TOOLBAR_FAMILIES.forEach(family => {
    const list = family.items || (family.groups ? family.groups.flatMap(g => g.items) : []);
    list.forEach(tool => {
      ALL_TOOLS_MAP[tool.id] = {
        ...tool,
        familyId: family.id
      };
    });
  });

  // --- Reactive Global Toolbar State Store ---
  class ToolbarStore {
    constructor() {
      this.STORAGE_KEY_LAST_SELECTED = 'tv_family_last_selected';
      this.STORAGE_KEY_FAVORITES = 'tv_favorited_tools';
      this.STORAGE_KEY_POS = 'tv_floating_toolbar_pos';

      this.activeDrawingTool = null;
      this.familyLastSelectedMap = this._loadLastSelected();
      this.favoritedToolIds = this._loadFavorites();
      this.floatingToolbarPos = this._loadPosition();
      this.listeners = [];
    }

    _loadLastSelected() {
      try {
        const saved = localStorage.getItem(this.STORAGE_KEY_LAST_SELECTED);
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed && typeof parsed === 'object') return parsed;
        }
      } catch (e) {
        console.warn('[ToolbarStore] Failed to load last selected map:', e);
      }
      // Defaults
      const defaults = {};
      TOOLBAR_FAMILIES.forEach(f => {
        defaults[f.id] = f.defaultToolId;
      });
      return defaults;
    }

    _saveLastSelected() {
      try {
        localStorage.setItem(this.STORAGE_KEY_LAST_SELECTED, JSON.stringify(this.familyLastSelectedMap));
      } catch (e) {}
    }

    _loadFavorites() {
      try {
        const saved = localStorage.getItem(this.STORAGE_KEY_FAVORITES);
        if (saved) {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed)) return parsed;
        }
      } catch (e) {}
      // Default initial favorites like TradingView
      return []; // Empty by default: floating toolbar only appears when user stars a tool
    }

    _saveFavorites() {
      try {
        localStorage.setItem(this.STORAGE_KEY_FAVORITES, JSON.stringify(this.favoritedToolIds));
      } catch (e) {}
    }

    _loadPosition() {
      try {
        const saved = localStorage.getItem(this.STORAGE_KEY_POS);
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed && typeof parsed.x === 'number' && typeof parsed.y === 'number') {
            return parsed;
          }
        }
      } catch (e) {}
      return { x: 72, y: 120 }; // Default initial position over the chart
    }

    savePosition(x, y) {
      this.floatingToolbarPos = { x, y };
      try {
        localStorage.setItem(this.STORAGE_KEY_POS, JSON.stringify(this.floatingToolbarPos));
      } catch (e) {}
      this._emit('positionChange', this.floatingToolbarPos);
    }

    setActiveTool(toolId) {
      this.activeDrawingTool = toolId;
      if (toolId && ALL_TOOLS_MAP[toolId]) {
        const familyId = ALL_TOOLS_MAP[toolId].familyId;
        this.setLastSelectedTool(familyId, toolId);
      }
      this._emit('activeToolChange', toolId);
    }

    getLastSelectedToolId(familyId) {
      return this.familyLastSelectedMap[familyId] || (TOOLBAR_FAMILIES.find(f => f.id === familyId)?.defaultToolId) || familyId;
    }

    setLastSelectedTool(familyId, toolId) {
      if (!familyId || !toolId) return;
      this.familyLastSelectedMap[familyId] = toolId;
      this._saveLastSelected();
      this._emit('lastSelectedChange', { familyId, toolId });
    }

    isFavorited(toolId) {
      return this.favoritedToolIds.includes(toolId);
    }

    toggleFavorite(toolId) {
      if (!toolId) return;
      const idx = this.favoritedToolIds.indexOf(toolId);
      if (idx !== -1) {
        this.favoritedToolIds.splice(idx, 1);
      } else {
        this.favoritedToolIds.push(toolId);
      }
      this._saveFavorites();
      this._emit('favoritesChange', this.favoritedToolIds);
    }

    subscribe(callback) {
      this.listeners.push(callback);
      return () => {
        this.listeners = this.listeners.filter(cb => cb !== callback);
      };
    }

    _emit(event, data) {
      this.listeners.forEach(cb => {
        try {
          cb(event, data, this);
        } catch (err) {
          console.error('[ToolbarStore] Listener error:', err);
        }
      });
    }
  }

  // Export to window
  window.ToolbarConfig = {
    ICONS,
    TOOLBAR_FAMILIES,
    ALL_TOOLS_MAP,
    store: new ToolbarStore()
  };

})();

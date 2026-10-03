// drawing-core.js — Phase 1: Core Drawing Engine
// Implements the TV Drawing Tools Specification (Documents 1-3)

console.log("Loading drawing-core.js...");

// =============================================================================
// EventBus — Pub/Sub Event System (Document 2 §7)
// =============================================================================

class EventBus {
    constructor() {
        this._listeners = {};
    }

    on(event, callback) {
        if (!this._listeners[event]) {
            this._listeners[event] = [];
        }
        this._listeners[event].push(callback);
        return () => this.off(event, callback);
    }

    off(event, callback) {
        const list = this._listeners[event];
        if (!list) return;
        const idx = list.indexOf(callback);
        if (idx !== -1) list.splice(idx, 1);
    }

    emit(event, payload) {
        const list = this._listeners[event];
        if (!list) return;
        // BUG-37 fix: snapshot the listener array before iterating so that
        // callbacks that call off() or on() do not corrupt the iterator.
        const snapshot = list.slice();
        for (var _i = 0; _i < snapshot.length; _i++) {
            try { snapshot[_i](payload); } catch (e) { console.error('EventBus error:', event, e); }
        }
    }

    clear() {
        this._listeners = {};
    }
}

// =============================================================================
// CoordinateMapper — World ↔ Pixel coordinate conversion (Document 2 §2)
// =============================================================================

class Viewport {
    constructor() {
        this.logicalFrom = 0;
        this.logicalTo = 0;
        this.priceTop = 0;
        this.priceBottom = 0;
        this.width = 0;
        this.height = 0;
        this.pixelsPerBar = 0;
        this.pixelsPerPrice = 0;
    }

    update(logicalFrom, logicalTo, priceTop, priceBottom, width, height) {
        this.logicalFrom = logicalFrom;
        this.logicalTo = logicalTo;
        this.priceTop = priceTop;
        this.priceBottom = priceBottom;
        this.width = width;
        this.height = height;

        var ro = 40;
        if (typeof window._rightOffset === 'number') ro = window._rightOffset;
        var bars = (logicalTo - logicalFrom) + ro;
        this.pixelsPerBar = bars !== 0 ? width / bars : 0;

        var prices = priceBottom - priceTop;
        this.pixelsPerPrice = prices !== 0 ? height / prices : 0;
    }
}

class CoordinateMapper {
    constructor() {
        this.viewport = new Viewport();
    }

    updateViewport(logicalFrom, logicalTo, priceTop, priceBottom, width, height) {
        this.viewport.update(logicalFrom, logicalTo, priceTop, priceBottom, width, height);
    }

    logicalToX(logical) {
        if (logical === null || logical === undefined) return 0;
        var chart = window.bigChart || window.chart;
        if (chart) {
            var ts = chart.timeScale();
            if (typeof ts.logicalToCoordinate === 'function') {
                var res = ts.logicalToCoordinate(logical);
                if (res !== null && res !== undefined && !isNaN(res)) return res;
            }
        }
        return (logical - this.viewport.logicalFrom) * (this.viewport.pixelsPerBar || 1);
    }

    xToLogical(x) {
        if (x === null || x === undefined) return 0;
        var chart = window.bigChart || window.chart;
        if (chart) {
            var ts = chart.timeScale();
            if (typeof ts.coordinateToLogical === 'function') {
                var res = ts.coordinateToLogical(x);
                if (res !== null && res !== undefined && !isNaN(res)) return res;
            }
        }
        return this.viewport.logicalFrom + (x / (this.viewport.pixelsPerBar || 1));
    }

    priceToY(price) {
        if (price === null || price === undefined) return 0;
        var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
        if (series && typeof series.priceToCoordinate === 'function') {
            var res = series.priceToCoordinate(price);
            if (res !== null && res !== undefined && !isNaN(res)) return res;
        }
        return (price - this.viewport.priceTop) * (this.viewport.pixelsPerPrice || 1);
    }

    yToPrice(y) {
        if (y === null || y === undefined) return 0;
        var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
        if (series && typeof series.coordinateToPrice === 'function') {
            var res = series.coordinateToPrice(y);
            if (res !== null && res !== undefined && !isNaN(res)) return res;
        }
        return this.viewport.priceTop + (y / (this.viewport.pixelsPerPrice || 1));
    }

    coordToPixel(coord) {
        if (!coord) return null;
        if (coord.xRel !== undefined && coord.yRel !== undefined) {
            var w = this.viewport.width || 800;
            var h = this.viewport.height || 500;
            return { x: coord.xRel * w, y: coord.yRel * h };
        }
        // PHASE A (A5/A6): resolve the CANONICAL time to a logical index on
        // every conversion. Because the index is recomputed from the live bar
        // array each time, a timeframe switch or a historical prepend (which
        // shifts every index) automatically lands the drawing back on the same
        // market timestamp -- no per-drawing index fix-ups required.
        var logical;
        if (coord.time !== undefined && coord.time !== null) {
            var resolved = this.timeToLogical(coord.time);
            logical = (resolved !== null && resolved !== undefined && !isNaN(resolved))
                ? resolved
                : (coord.logical !== undefined ? coord.logical : null);
        } else {
            logical = coord.logical !== undefined ? coord.logical : coord.time;
        }
        var price = coord.price;

        var x = null, y = null;

        // --- X: use LightweightCharts native logicalToCoordinate if available ---
        // This is pixel-perfect because it uses the chart's own internal math.
        var chart = window.bigChart || window.chart;
        if (chart && logical !== null && logical !== undefined) {
            var ts = chart.timeScale();
            if (typeof ts.logicalToCoordinate === 'function') {
                x = ts.logicalToCoordinate(logical);
            }
        }
        // Fallback to cached viewport formula
        if (x === null || x === undefined || isNaN(x)) {
            x = (logical !== null && logical !== undefined) ? this.logicalToX(logical) : 0;
        }

        // --- Y: use series.priceToCoordinate (always most accurate) ---
        var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
        if (series && price !== null && price !== undefined) {
            y = series.priceToCoordinate(price);
        }
        // Fallback to cached viewport formula
        if (y === null || y === undefined || isNaN(y)) {
            y = (price !== null && price !== undefined) ? this.priceToY(price) : 0;
        }

        return { x: x, y: y };
    }

    pixelToCoord(x, y) {
        var logical = null, price = null;

        // --- X: use LightweightCharts native coordinateToLogical if available ---
        var chart = window.bigChart || window.chart;
        if (chart) {
            var ts = chart.timeScale();
            if (typeof ts.coordinateToLogical === 'function') {
                logical = ts.coordinateToLogical(x);
            }
        }
        // Fallback to cached viewport formula
        if (logical === null || logical === undefined || isNaN(logical)) {
            logical = this.xToLogical(x);
        }

        // --- Y: use series.coordinateToPrice ---
        var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
        if (series) {
            price = series.coordinateToPrice(y);
        }
        // Fallback to cached viewport formula
        if (price === null || price === undefined || isNaN(price)) {
            price = this.yToPrice(y);
        }

        // PHASE A (BUG-003): this previously returned `time: logical`, i.e. it
        // labelled a BAR INDEX as a timestamp. With time now the canonical
        // anchor, that would have anchored every newly-created drawing to a
        // bar index disguised as a time. Resolve the real market timestamp.
        var realTime = this.logicalToTime(logical);
        return {
            logical: logical,
            time: (realTime !== null && realTime !== undefined && !isNaN(realTime)) ? realTime : null,
            price: price
        };
    }

    isReady() {
        return this.viewport.pixelsPerBar !== 0 && this.viewport.pixelsPerPrice !== 0;
    }

    isOnCanvas(pixel, canvasWidth, canvasHeight) {
        return pixel.x >= 0 && pixel.x <= canvasWidth &&
               pixel.y >= 0 && pixel.y <= canvasHeight;
    }

    setMappers(mappers) {
        // Kept for backward compatibility bridge
    }

    // =====================================================================
    // PHASE A — EXACT time <-> logical mapping (BUG-004 / BUG-003)
    // =====================================================================
    // The previous implementation derived seconds-per-bar from the VISIBLE
    // RANGE duration and used it for canonical conversion. That is wrong
    // wherever bars are not uniformly spaced in time -- i.e. across weekends,
    // exchange holidays, and data gaps, which is precisely this market's
    // profile. It also made xToTime() return a bar index despite its name.
    //
    // window._chartCandles is the authoritative loaded bar array (set in
    // dashboard.js alongside every setData() call). Its ARRAY INDEX is the
    // Lightweight Charts logical index, so an exact lookup is available and
    // must be preferred over any arithmetic estimate.

    _bars() {
        var b = window._chartCandles;
        return (b && Array.isArray(b) && b.length) ? b : null;
    }

    // Convert any bar time value to epoch seconds.
    // Handles: Unix number, "YYYY-MM-DD" string (1D business-day mode), BusinessDay object.
    _barTimeSec(t) {
        if (t === null || t === undefined) return 0;
        if (typeof t === 'number') return t;
        if (typeof t === 'object' && t.year) return Date.UTC(t.year, t.month - 1, t.day) / 1000;
        if (typeof t === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(t))
            return new Date(t + 'T00:00:00Z').getTime() / 1000;
        var n = Number(t);
        return isNaN(n) ? 0 : n;
    }

    // Median interval of the last N bars. Used ONLY to project BEYOND the
    // loaded data, where by definition no exact bar exists to map against.
    // Median (not mean) so a single weekend/holiday gap cannot skew it.
    _edgeBarInterval(bars) {
        var n = Math.min(20, bars.length - 1);
        if (n < 1) return 86400; // default to 1 day for daily charts
        var deltas = [];
        for (var i = bars.length - n; i < bars.length; i++) {
            var d = this._barTimeSec(bars[i].time) - this._barTimeSec(bars[i - 1].time);
            if (d > 0) deltas.push(d);
        }
        if (!deltas.length) return 86400;
        deltas.sort(function (a, b) { return a - b; });
        return deltas[Math.floor(deltas.length / 2)];
    }

    // logical (bar index) -> exact market timestamp (always returned as epoch seconds).
    logicalToTime(logical) {
        if (logical === null || logical === undefined || isNaN(logical)) return null;
        var bars = this._bars();
        if (!bars) return null;
        var i = Math.round(logical);
        if (i >= 0 && i < bars.length) return this._barTimeSec(bars[i].time); // EXACT
        var step = this._edgeBarInterval(bars);
        if (i < 0) return this._barTimeSec(bars[0].time) + i * step;          // projected past
        return this._barTimeSec(bars[bars.length - 1].time) + (i - (bars.length - 1)) * step; // projected future
    }

    // exact market timestamp (epoch seconds) -> logical (bar index).
    // Binary search over the real bar array; interpolates within gaps
    // (weekends/holidays) so drawings land proportionally, not snapped.
    timeToLogical(time) {
        if (time === null || time === undefined) return null;
        // Accept epoch seconds (number) or "YYYY-MM-DD" string (1D mode).
        var t = (typeof time === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(time))
            ? new Date(time + 'T00:00:00Z').getTime() / 1000
            : Number(time);
        if (isNaN(t)) return null;
        var bars = this._bars();
        if (!bars) return null;
        var lo = 0, hi = bars.length - 1;
        var t0 = this._barTimeSec(bars[0].time);
        var tN = this._barTimeSec(bars[hi].time);
        if (t <= t0) {
            if (t === t0) return 0;
            return -((t0 - t) / this._edgeBarInterval(bars));
        }
        if (t >= tN) {
            if (t === tN) return hi;
            return hi + (t - tN) / this._edgeBarInterval(bars);
        }
        while (lo <= hi) {
            var mid = (lo + hi) >> 1;
            var mt = this._barTimeSec(bars[mid].time);
            if (mt === t) return mid;
            if (mt < t) lo = mid + 1; else hi = mid - 1;
        }
        // t sits between bars[hi] and bars[lo]; interpolate within that gap so
        // drawings anchored inside a weekend land proportionally, not snapped.
        var a = this._barTimeSec(bars[hi].time), b = this._barTimeSec(bars[lo].time);
        if (b === a) return hi;
        return hi + (t - a) / (b - a);
    }

    // --- Public API: these now mean exactly what their names say ---------

    xToTime(x) {
        return this.logicalToTime(this.xToLogical(x));
    }

    timeToX(time) {
        var logical = this.timeToLogical(time);
        return (logical === null) ? null : this.logicalToX(logical);
    }

    // Legacy internal aliases retained so existing callers keep working.
    // They now delegate to the exact implementations above.
    _logicalToTime(logical) {
        var t = this.logicalToTime(logical);
        return (t === null) ? logical : t;
    }

    _timeToLogical(time) {
        var l = this.timeToLogical(time);
        return (l === null) ? time : l;
    }

    getMagnetPoint(x, y) {
        return this.pixelToCoord(x, y);
    }

    getBarSpacing() {
        return Math.abs(this.viewport.pixelsPerBar) || 20;
    }

    getRightOffset() {
        return window._rightOffset || 40;
    }

    scrollLogical(deltaBars) {
        if (!window.bigChart) return;
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        if (!lr) return;
        window.bigChart.timeScale().setVisibleLogicalRange({
            from: lr.from + deltaBars,
            to: lr.to + deltaBars
        });
    }
}

// =============================================================================
// GeometryUtils — Reusable geometry utilities (Phase 2)
// Canvas-edge intersection, midpoint, perpendicular, point-in-polygon, etc.
// =============================================================================

class GeometryUtils {
    // Compute intersection of an infinite line (through p1 and p2) with canvas boundary.
    // Returns [{x,y}, {x,y}] of the two intersection points with the viewport rect.
    static lineCanvasIntersection(p1, p2, cw, ch) {
        if (!p1 || !p2) return null;
        var dx = p2.x - p1.x;
        var dy = p2.y - p1.y;
        if (dx === 0 && dy === 0) return [{ x: p1.x, y: p1.y }, { x: p1.x, y: p1.y }];

        var t = [];
        var eps = 1e-9;
        // Check if a point at (x,y) lies on the canvas boundary
        function onEdge(x, y) {
            return (x >= -eps && x <= cw + eps && y >= -eps && y <= ch + eps);
        }
        // Intersection with left edge (x=0)
        if (Math.abs(dx) > eps) { var ly = p1.y + (-p1.x / dx) * dy; if (onEdge(0, ly)) t.push({ t: -p1.x / dx, x: 0, y: ly }); }
        // Right edge (x=cw)
        if (Math.abs(dx) > eps) { var ry = p1.y + ((cw - p1.x) / dx) * dy; if (onEdge(cw, ry)) t.push({ t: (cw - p1.x) / dx, x: cw, y: ry }); }
        // Top edge (y=0)
        if (Math.abs(dy) > eps) { var tx = p1.x + (-p1.y / dy) * dx; if (onEdge(tx, 0)) t.push({ t: -p1.y / dy, x: tx, y: 0 }); }
        // Bottom edge (y=ch)
        if (Math.abs(dy) > eps) { var bx = p1.x + ((ch - p1.y) / dy) * dx; if (onEdge(bx, ch)) t.push({ t: (ch - p1.y) / dy, x: bx, y: ch }); }

        t.sort(function(a, b) { return a.t - b.t; });
        // If no valid canvas intersection, return original points (clamped to canvas bounds)
        if (t.length < 2) {
            return [
                { x: Math.max(0, Math.min(cw, p1.x)), y: Math.max(0, Math.min(ch, p1.y)) },
                { x: Math.max(0, Math.min(cw, p2.x)), y: Math.max(0, Math.min(ch, p2.y)) }
            ];
        }
        // Clamp to canvas bounds to avoid off-canvas artifacts
        return [
            { x: Math.max(0, Math.min(cw, t[0].x)), y: Math.max(0, Math.min(ch, t[0].y)) },
            { x: Math.max(0, Math.min(cw, t[t.length - 1].x)), y: Math.max(0, Math.min(ch, t[t.length - 1].y)) }
        ];
    }

    // For a Ray: intersection from p1 through p2 to the canvas right/bottom/right edges
    static rayCanvasIntersection(p1, p2, cw, ch) {
        var pts = GeometryUtils.lineCanvasIntersection(p1, p2, cw, ch);
        if (!pts) return null;
        // Filter to only points in the direction from p1 through p2
        var dx = p2.x - p1.x;
        var dy = p2.y - p1.y;
        var result = [];
        for (var i = 0; i < pts.length; i++) {
            var ddx = pts[i].x - p1.x;
            var ddy = pts[i].y - p1.y;
            // Must be in the same direction as (dx, dy)
            if ((dx >= 0 && ddx >= -1) || (dx < 0 && ddx <= 1)) {
                if ((dy >= 0 && ddy >= -1) || (dy < 0 && ddy <= 1)) {
                    result.push(pts[i]);
                }
            }
        }
        if (result.length === 0) return { x: p1.x + dx * 10000, y: p1.y + dy * 10000 };
        // Return the farthest point from p1 (the ray endpoint)
        // BUG-01 fix: missing parentheses caused wrong distance comparison.
        // The subtraction must wrap both full squared-distance expressions.
        result.sort(function(a, b) {
            var distB = (b.x - p1.x) * (b.x - p1.x) + (b.y - p1.y) * (b.y - p1.y);
            var distA = (a.x - p1.x) * (a.x - p1.x) + (a.y - p1.y) * (a.y - p1.y);
            return distB - distA;
        });
        return result[0];
    }

    // For Horizontal Ray: intersection from p1 rightward to canvas edge
    static horizontalRayEnd(p1, cw) {
        return { x: cw, y: p1.y };
    }

    // Midpoint between two points
    static distance(p1, p2) {
        var dx = p1.x - p2.x, dy = p1.y - p2.y;
        return Math.sqrt(dx * dx + dy * dy);
    }

    static midpoint(p1, p2) {
        return { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
    }

    // Distance from point to infinite line defined by (lineA, lineB)
    static pointToLine(point, lineA, lineB) {
        var dx = lineB.x - lineA.x;
        var dy = lineB.y - lineA.y;
        var lenSq = dx * dx + dy * dy;
        if (lenSq === 0) return Math.sqrt((point.x - lineA.x) * (point.x - lineA.x) + (point.y - lineA.y) * (point.y - lineA.y));
        var num = Math.abs(dy * point.x - dx * point.y + lineB.x * lineA.y - lineB.y * lineA.x);
        return num / Math.sqrt(lenSq);
    }

    // Distance from point to line SEGMENT (p1, p2) and the closest point on segment
    static pointToLineSegment(point, p1, p2) {
        var A = point.x - p1.x;
        var B = point.y - p1.y;
        var C = p2.x - p1.x;
        var D = p2.y - p1.y;
        var dot = A * C + B * D;
        var lenSq = C * C + D * D;
        var param = lenSq !== 0 ? dot / lenSq : -1;
        var xx, yy;
        if (param < 0) { xx = p1.x; yy = p1.y; }
        else if (param > 1) { xx = p2.x; yy = p2.y; }
        else { xx = p1.x + param * C; yy = p1.y + param * D; }
        var dx = point.x - xx;
        var dy = point.y - yy;
        return { dist: Math.sqrt(dx * dx + dy * dy), closest: { x: xx, y: yy } };
    }

    // Point-in-polygon (ray casting)
    static pointInPolygon(point, vertices) {
        if (!vertices || vertices.length < 3) return false;
        var x = point.x, y = point.y;
        var inside = false;
        for (var i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
            var xi = vertices[i].x, yi = vertices[i].y;
            var xj = vertices[j].x, yj = vertices[j].y;
            if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) {
                inside = !inside;
            }
        }
        return inside;
    }

    // Point-in-ellipse
    static pointInEllipse(point, cx, cy, rx, ry) {
        if (rx <= 0 || ry <= 0) return false;
        return ((point.x - cx) * (point.x - cx)) / (rx * rx) + ((point.y - cy) * (point.y - cy)) / (ry * ry) <= 1;
    }

    // Constrain a point relative to origin to the nearest angle in snapAngles (degrees)
    static constrainToAngle(point, origin, snapAngles) {
        var dx = point.x - origin.x;
        var dy = point.y - origin.y;
        if (dx === 0 && dy === 0) return { x: origin.x, y: origin.y };
        var angle = Math.atan2(dy, dx) * 180 / Math.PI;
        var bestAngle = snapAngles[0];
        var bestDiff = Infinity;
        for (var i = 0; i < snapAngles.length; i++) {
            var diff = Math.abs(angle - snapAngles[i]);
            if (diff > 180) diff = 360 - diff;
            if (diff < bestDiff) { bestDiff = diff; bestAngle = snapAngles[i]; }
        }
        var rad = bestAngle * Math.PI / 180;
        var len = Math.sqrt(dx * dx + dy * dy);
        return { x: origin.x + len * Math.cos(rad), y: origin.y + len * Math.sin(rad) };
    }

    // Constrain to horizontal movement (preserve y)
    static constrainHorizontal(point, origin) {
        return { x: point.x, y: origin.y };
    }

    // Constrain to vertical movement (preserve x)
    static constrainVertical(point, origin) {
        return { x: origin.x, y: point.y };
    }

    // Bounding box of a set of pixel points
    static boundingBox(pixels) {
        if (!pixels || pixels.length === 0) return null;
        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (var i = 0; i < pixels.length; i++) {
            var p = pixels[i];
            if (p.x < minX) minX = p.x;
            if (p.y < minY) minY = p.y;
            if (p.x > maxX) maxX = p.x;
            if (p.y > maxY) maxY = p.y;
        }
        return { left: minX, top: minY, right: maxX, bottom: maxY };
    }

    // Check if two bounding boxes intersect
    static boxIntersects(a, b) {
        // BUG-38 fix: null bbox should NOT be treated as intersecting everything;
        // a drawing with no pixels should not be selected by marquee.
        if (!a || !b) return false;
        return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
    }
}

class GeometryClipper {
    constructor(viewport) {
        this.viewport = viewport || (window.coordinateMapper && window.coordinateMapper.viewport);
    }
    
    get width() {
        if (window.toolManager && window.toolManager.canvas) {
            var dpr = window.devicePixelRatio || 1;
            return window.toolManager.canvas.width / dpr;
        }
        return this.viewport ? this.viewport.width : 800;
    }
    
    get height() {
        if (window.toolManager && window.toolManager.canvas) {
            var dpr = window.devicePixelRatio || 1;
            return window.toolManager.canvas.height / dpr;
        }
        return this.viewport ? this.viewport.height : 500;
    }

    clipSegment(p1, p2) {
        var INSIDE = 0; // 0000
        var LEFT = 1;   // 0001
        var RIGHT = 2;  // 0010
        var BOTTOM = 4; // 0100
        var TOP = 8;    // 1000

        var xmin = 0, xmax = this.width, ymin = 0, ymax = this.height;

        function computeOutCode(x, y) {
            var code = INSIDE;
            if (x < xmin) code |= LEFT;
            else if (x > xmax) code |= RIGHT;
            if (y < ymin) code |= TOP;
            else if (y > ymax) code |= BOTTOM;
            return code;
        }

        var x0 = p1.x, y0 = p1.y, x1 = p2.x, y1 = p2.y;
        var outcode0 = computeOutCode(x0, y0);
        var outcode1 = computeOutCode(x1, y1);
        var accept = false;

        while (true) {
            if (!(outcode0 | outcode1)) {
                accept = true;
                break;
            } else if (outcode0 & outcode1) {
                break;
            } else {
                var x, y;
                var outcodeOut = outcode0 ? outcode0 : outcode1;

                if (outcodeOut & BOTTOM) {
                    x = x0 + (x1 - x0) * (ymax - y0) / (y1 - y0);
                    y = ymax;
                } else if (outcodeOut & TOP) {
                    x = x0 + (x1 - x0) * (ymin - y0) / (y1 - y0);
                    y = ymin;
                } else if (outcodeOut & RIGHT) {
                    y = y0 + (y1 - y0) * (xmax - x0) / (x1 - x0);
                    x = xmax;
                } else if (outcodeOut & LEFT) {
                    y = y0 + (y1 - y0) * (xmin - x0) / (x1 - x0);
                    x = xmin;
                }

                if (outcodeOut === outcode0) {
                    x0 = x;
                    y0 = y;
                    outcode0 = computeOutCode(x0, y0);
                } else {
                    x1 = x;
                    y1 = y;
                    outcode1 = computeOutCode(x1, y1);
                }
            }
        }

        return accept ? { p1: { x: x0, y: y0 }, p2: { x: x1, y: y1 } } : null;
    }

    clipRay(p1, p2) {
        var pts = GeometryUtils.rayCanvasIntersection(p1, p2, this.width, this.height);
        if (!pts) return null;
        return { p1: p1, p2: pts };
    }

    clipInfiniteLine(p1, p2) {
        var pts = GeometryUtils.lineCanvasIntersection(p1, p2, this.width, this.height);
        if (!pts || pts.length < 2) return null;
        return { p1: pts[0], p2: pts[1] };
    }

    clipPolygon(vertices) {
        var xmin = 0, xmax = this.width, ymin = 0, ymax = this.height;
        var outputList = vertices;

        // Clip against left edge (x >= xmin)
        var inputList = outputList;
        outputList = [];
        var s = inputList[inputList.length - 1];
        for (var i = 0; i < inputList.length; i++) {
            var p = inputList[i];
            if (p.x >= xmin) {
                if (s.x < xmin) {
                    var t = (xmin - s.x) / (p.x - s.x);
                    outputList.push({ x: xmin, y: s.y + t * (p.y - s.y) });
                }
                outputList.push(p);
            } else if (s.x >= xmin) {
                var t = (xmin - s.x) / (p.x - s.x);
                outputList.push({ x: xmin, y: s.y + t * (p.y - s.y) });
            }
            s = p;
        }

        // Clip against right edge (x <= xmax)
        if (outputList.length === 0) return [];
        inputList = outputList;
        outputList = [];
        s = inputList[inputList.length - 1];
        for (var i = 0; i < inputList.length; i++) {
            var p = inputList[i];
            if (p.x <= xmax) {
                if (s.x > xmax) {
                    var t = (xmax - s.x) / (p.x - s.x);
                    outputList.push({ x: xmax, y: s.y + t * (p.y - s.y) });
                }
                outputList.push(p);
            } else if (s.x <= xmax) {
                var t = (xmax - s.x) / (p.x - s.x);
                outputList.push({ x: xmax, y: s.y + t * (p.y - s.y) });
            }
            s = p;
        }

        // Clip against top edge (y >= ymin)
        if (outputList.length === 0) return [];
        inputList = outputList;
        outputList = [];
        s = inputList[inputList.length - 1];
        for (var i = 0; i < inputList.length; i++) {
            var p = inputList[i];
            if (p.y >= ymin) {
                if (s.y < ymin) {
                    var t = (ymin - s.y) / (p.y - s.y);
                    outputList.push({ x: s.x + t * (p.x - s.x), y: ymin });
                }
                outputList.push(p);
            } else if (s.y >= ymin) {
                var t = (ymin - s.y) / (p.y - s.y);
                outputList.push({ x: s.x + t * (p.x - s.x), y: ymin });
            }
            s = p;
        }

        // Clip against bottom edge (y <= ymax)
        if (outputList.length === 0) return [];
        inputList = outputList;
        outputList = [];
        s = inputList[inputList.length - 1];
        for (var i = 0; i < inputList.length; i++) {
            var p = inputList[i];
            if (p.y <= ymax) {
                if (s.y > ymax) {
                    var t = (ymax - s.y) / (p.y - s.y);
                    outputList.push({ x: s.x + t * (p.x - s.x), y: ymax });
                }
                outputList.push(p);
            } else if (s.y <= ymax) {
                var t = (ymax - s.y) / (p.y - s.y);
                outputList.push({ x: s.x + t * (p.x - s.x), y: ymax });
            }
            s = p;
        }

        return outputList;
    }

    clipCircle(center, radius) {
        var xmin = 0, xmax = this.width, ymin = 0, ymax = this.height;
        var closestX = Math.max(xmin, Math.min(center.x, xmax));
        var closestY = Math.max(ymin, Math.min(center.y, ymax));
        var dx = center.x - closestX;
        var dy = center.y - closestY;
        return (dx * dx + dy * dy) <= (radius * radius);
    }
}

window.GeometryClipper = GeometryClipper;

// =============================================================================
// Layer — render layer enum (Phase 3.8)
// Numeric IDs for fast comparison; string names for serialization.
// Order defines render sequence: lowest → rendered first (behind).
// =============================================================================

var Layer = {
    BACKGROUND:      0,
    GRID:            1,
    CHART:           2,
    INDICATORS:      3,
    DRAWINGS_BEHIND: 4,
    CANDLES:         5,
    DRAWINGS_ABOVE:  6,
    TEXT:            7,
    OVERLAYS:        8,
    SELECTION:       9,
    CROSSHAIR:       10,
    TOOLTIPS:        11,

    // Reverse lookup: numeric ID → string key
    nameOf: function(id) {
        for (var k in this) {
            if (typeof this[k] === 'number' && this[k] === id) return k;
        }
        return 'DRAWINGS_ABOVE';
    },

    // Parse a user-supplied value to a numeric layer ID
    parse: function(v) {
        if (typeof v === 'number') return v;
        if (typeof v === 'string' && this[v] !== undefined) return this[v];
        return Layer.DRAWINGS_ABOVE;
    }
};

// =============================================================================
// RenderLayerManager — centralized layer sort + z-index allocator (Phase 3.8)
// =============================================================================

class RenderLayerManager {
    constructor() {
        this._nextZ = 1000;
        this._zReserved = { min: 0, max: 0 };
    }

    // Group drawings by layer, sort each layer by zIndex, flatten in layer order.
    // Returns a new array; does not mutate the input.
    sortDrawings(drawings) {
        if (!drawings || !drawings.length) return [];

        // Bucket by layer
        var buckets = {};
        for (var i = 0; i < drawings.length; i++) {
            var d = drawings[i];
            var model = d.model || d;
            var layerId = Layer.parse(model.layer);
            if (!buckets[layerId]) buckets[layerId] = [];
            buckets[layerId].push(d);
        }

        // Sort each bucket by zIndex ascending
        var keys = Object.keys(buckets).map(Number).sort(function(a, b) { return a - b; });
        var result = [];
        for (var ki = 0; ki < keys.length; ki++) {
            var bucket = buckets[keys[ki]];
            bucket.sort(function(a, b) {
                var za = (a.model || a).zIndex || 0;
                var zb = (b.model || b).zIndex || 0;
                return za - zb;
            });
            for (var bi = 0; bi < bucket.length; bi++) {
                result.push(bucket[bi]);
            }
        }
        return result;
    }

    // Allocate next available zIndex
    nextZ() {
        var z = this._nextZ;
        this._nextZ += 10;
        return z;
    }

    // Reserve a range of z-index values for a specific layer
    reserveRange(layer, start, count) {
        // Just records; actual allocation is done via nextZ
        this._zReserved = { min: Math.min(this._zReserved.min, start), max: Math.max(this._zReserved.max, start + count) };
    }

    // Normalize all zIndex values across drawings to compact them
    // after many bring-to-front / send-to-back operations
    normalizeZ(drawings) {
        if (!drawings || !drawings.length) return;
        var sorted = this.sortDrawings(drawings);
        var step = 10;
        for (var i = 0; i < sorted.length; i++) {
            var d = sorted[i];
            var model = d.model || d;
            model.zIndex = (i + 1) * step;
        }
        this._nextZ = (sorted.length + 1) * step;
    }

    // Get the maximum zIndex currently in use
    maxZ(drawings) {
        var max = 0;
        for (var i = 0; i < drawings.length; i++) {
            var d = drawings[i];
            var z = (d.model || d).zIndex || 0;
            if (z > max) max = z;
        }
        return max;
    }

    // Get the minimum zIndex currently in use
    minZ(drawings) {
        var min = Infinity;
        for (var i = 0; i < drawings.length; i++) {
            var d = drawings[i];
            var z = (d.model || d).zIndex || 0;
            if (z < min) min = z;
        }
        return min === Infinity ? 0 : min;
    }
}

// =============================================================================
// SnappingEngine — OHLC snapping with weak/strong modes (Phase 2)
// =============================================================================

function getSnappedAnchor(cursorX, cursorY, chartState, options) {
    if (!chartState) return null;

    options = options || {};
    var priceSnapEnabled = options.priceSnapEnabled;
    if (priceSnapEnabled === undefined) {
        priceSnapEnabled = (window.toolManager && window.toolManager.engine && window.toolManager.engine.snapping) ? window.toolManager.engine.snapping.isActive : false;
    }

    // Convert cursor pixels to logical coordinate and price
    var logical = null;
    var price = null;

    if (chartState.pixelToCoord) {
        var coord = chartState.pixelToCoord(cursorX, cursorY);
        if (coord) {
            logical = coord.logical !== undefined ? coord.logical : coord.time;
            price = coord.price;
        }
    }
    if (logical == null) {
        logical = chartState.xToLogical ? chartState.xToLogical(cursorX) : (chartState.xToTime ? chartState.xToTime(cursorX) : cursorX);
    }
    if (price == null) {
        price = chartState.yToPrice ? chartState.yToPrice(cursorY) : cursorY;
    }

    if (logical == null || price == null) return null;

    var idx = Math.round(logical);
    var candle = null;
    if (window._chartCandles && Array.isArray(window._chartCandles)) {
        if (idx >= 0 && idx < window._chartCandles.length) {
            candle = window._chartCandles[idx];
        }
    }

    // Case 6 & 7: Cursor is in future space or before first candle (no candle exists) -> DO NOT snap
    if (!candle) {
        return {
            logical: logical,
            price: price,
            snappedLogical: logical,
            snappedPrice: price,
            candleIndex: null
        };
    }

    // Candidate OHLC values for price snapping (TradingView-parity snaps to wicks & body)
    var ohlc = [candle.open, candle.high, candle.low, candle.close];
    var bestPrice = ohlc[0];
    var minPriceDiff = Math.abs(ohlc[0] - price);

    for (var i = 1; i < ohlc.length; i++) {
        if (ohlc[i] == null) continue;
        var diff = Math.abs(ohlc[i] - price);
        if (diff < minPriceDiff) {
            minPriceDiff = diff;
            bestPrice = ohlc[i];
        }
    }

    var snappingMode = (window.toolManager && window.toolManager.engine && window.toolManager.engine.snapping) ? window.toolManager.engine.snapping.mode : 'strong';
    
    var finalPrice = price;
    var _cursorPriceUsable = (price !== null && price !== undefined && !isNaN(price) && price !== 0);
    
    if (priceSnapEnabled && snappingMode !== 'disabled') {
        var snappedX = null;
        if (window.bigChart && typeof window.bigChart.timeScale === 'function') {
            snappedX = window.bigChart.timeScale().logicalToCoordinate(idx);
        }
        if (snappedX == null) {
            snappedX = chartState.logicalToX ? chartState.logicalToX(idx) : (chartState.timeToX ? chartState.timeToX(idx) : cursorX);
        }
        if (snappedX == null) snappedX = cursorX;

        var snappedY = null;
        if (window.bigCandleSeries && typeof window.bigCandleSeries.priceToCoordinate === 'function') {
            snappedY = window.bigCandleSeries.priceToCoordinate(bestPrice);
        }
        if (snappedY == null) {
            snappedY = chartState.priceToY ? chartState.priceToY(bestPrice) : cursorY;
        }

        var dx = cursorX - snappedX;
        var dy = cursorY - snappedY;
        var dist = Math.sqrt(dx * dx + dy * dy);

        if (snappingMode === 'strong' || dist <= 50) {
            finalPrice = bestPrice;
        } else {
            finalPrice = _cursorPriceUsable ? price : bestPrice;
        }
    } else {
        finalPrice = _cursorPriceUsable ? price : bestPrice;
    }
    var finalLogical = idx; // Logical Snap: Always enabled (nearest candle logical index)

    // PHASE A (BUG-001): every anchor MUST carry the canonical market time.
    // BaseDrawing.constructor and commitPoint build their coords straight from
    // this return value, so omitting `time` here meant that in the real app
    // (where getSnappedAnchor is defined) every point created by clicking was
    // still anchored to a bar index only -- silently defeating the canonical
    // anchoring everywhere else. Prefer the snapped candle's own timestamp,
    // which is exact by construction.
    var finalTime = null;
    if (candle && candle.time !== undefined && candle.time !== null) {
        // Use coordinateMapper._barTimeSec if available so date-string times (1D mode) work.
        finalTime = (window.coordinateMapper && typeof window.coordinateMapper._barTimeSec === 'function')
            ? window.coordinateMapper._barTimeSec(candle.time)
            : Number(candle.time);
    } else if (window.coordinateMapper && typeof window.coordinateMapper.logicalToTime === 'function') {
        var _t = window.coordinateMapper.logicalToTime(finalLogical);
        if (_t !== null && _t !== undefined && !isNaN(_t)) finalTime = _t;
    }

    return {
        logical: finalLogical,
        time: finalTime,
        price: finalPrice,
        snappedLogical: finalLogical,
        snappedPrice: bestPrice,
        candleIndex: idx
    };
}
window.getSnappedAnchor = getSnappedAnchor;


class SnappingEngine {
    constructor() {
        this.mode = 'disabled'; // Default to disabled magnet on startup (manual toggle only)
        this.threshold = 10;    // CSS pixels — snap radius
        this._dataProvider = null;
    }

    // Set the data provider function: (x, chartState) => { open, high, low, close } at that x
    setDataProvider(fn) {
        this._dataProvider = fn;
    }

    setMode(mode) {
        if (mode === 'weak') mode = 'strong';
        if (['disabled', 'strong'].indexOf(mode) === -1) return;
        this.mode = mode;
    }

    toggleMode() {
        if (this.mode === 'disabled') this.mode = 'strong';
        else this.mode = 'disabled';
    }

    get isActive() { return this.mode !== 'disabled'; }

    // Get snapped (x, y) for a given pixel position
    snap(x, y, chartState) {
        if (!this.isActive) return { x: x, y: y, snapped: false };
        if (!chartState) return { x: x, y: y, snapped: false };

        var logical = null;
        var mousePrice = null;

        // Try extracting absolute coordinate mapping directly from Lightweight Charts instance for max accuracy
        if (window.bigChart && window.bigCandleSeries) {
            logical = window.bigChart.timeScale().coordinateToLogical(x);
            mousePrice = window.bigCandleSeries.coordinateToPrice(y);
        }

        if (logical == null || mousePrice == null) {
            if (chartState.pixelToCoord) {
                var coord = chartState.pixelToCoord(x, y);
                if (coord) {
                    logical = coord.logical !== undefined ? coord.logical : coord.time;
                    mousePrice = coord.price;
                }
            }
        }
        
        if (logical == null) {
            logical = chartState.xToLogical ? chartState.xToLogical(x) : (chartState.xToTime ? chartState.xToTime(x) : x);
        }
        if (mousePrice == null) {
            mousePrice = chartState.yToPrice ? chartState.yToPrice(y) : y;
        }

        if (logical == null || mousePrice == null) return { x: x, y: y };

        var idx = Math.round(logical);
        var candle = null;
        if (window._chartCandles && Array.isArray(window._chartCandles)) {
            if (idx >= 0 && idx < window._chartCandles.length) {
                candle = window._chartCandles[idx];
            }
        }

        if (!candle) return { x: x, y: y };

        // Build snap candidates: High (top wick), Low (bottom wick), Open (body), Close (body)
        var isBullish = candle.close >= candle.open;
        var candidates = [
            { price: candle.high, label: 'Wick High (H)', type: 'wick_high' },
            { price: candle.low, label: 'Wick Low (L)', type: 'wick_low' },
            { price: candle.open, label: isBullish ? 'Body Open (O)' : 'Body Open (O)', type: 'body_open' },
            { price: candle.close, label: isBullish ? 'Body Close (C)' : 'Body Close (C)', type: 'body_close' }
        ];

        var bestPrice = null;
        var minPriceDiff = Infinity;
        var bestLabel = '';
        var bestCandidateType = '';

        for (var i = 0; i < candidates.length; i++) {
            var val = candidates[i].price;
            if (val == null) continue;
            var diff = Math.abs(val - mousePrice);
            if (diff < minPriceDiff) {
                minPriceDiff = diff;
                bestPrice = val;
                bestLabel = candidates[i].label;
                bestCandidateType = candidates[i].type;
            }
        }

        if (bestPrice == null) return { x: x, y: y, snapped: false };

        var snappedY = null;
        if (window.bigCandleSeries && typeof window.bigCandleSeries.priceToCoordinate === 'function') {
            snappedY = window.bigCandleSeries.priceToCoordinate(bestPrice);
        }
        if (snappedY == null) {
            snappedY = chartState.priceToY ? chartState.priceToY(bestPrice) : y;
        }

        var snappedX = null;
        if (window.bigChart && typeof window.bigChart.timeScale === 'function') {
            snappedX = window.bigChart.timeScale().logicalToCoordinate(idx);
        }
        if (snappedX == null) {
            snappedX = chartState.logicalToX ? chartState.logicalToX(idx) : (chartState.timeToX ? chartState.timeToX(idx) : x);
        }
        if (snappedX == null) snappedX = x;

        // If in 'weak' snapping mode, only snap if the mouse cursor is close to the candidate candle price (within 50 pixels)
        if (this.mode === 'weak') {
            var dx = x - snappedX;
            var dy = y - snappedY;
            var dist = Math.sqrt(dx * dx + dy * dy);
            if (dist > 50) {
                return { x: x, y: y, snapped: false };
            }
        }

        return { x: snappedX, y: snappedY, snapped: true, value: bestPrice, label: bestLabel, candidateType: bestCandidateType, candle: candle, logical: idx };
    }

    // Get snap indicator data for rendering (shows the snap circle)
    getSnapIndicator(x, y, chartState) {
        if (this.mode === 'disabled') return { x: x, y: y };
        return this.snap(x, y, chartState);
    }
}

// =============================================================================
// HitTestService — Spec-compliant hit testing (Document 2 §4, Phase 2)
// Priority: anchor handles → midpoint handles → edge → fill → body
// =============================================================================

class HitTestService {
    constructor(options) {
        this.anchorRadius = (options && options.anchorRadius) || 8;
        this.midpointRadius = (options && options.midpointRadius) || 8;
        this.edgeThreshold = (options && options.edgeThreshold) || 10;
        this.bodyThreshold = (options && options.bodyThreshold) || 10;
        this.fillThreshold = (options && options.fillThreshold) || 10;
    }

    // Main entry: test a pixel position against all drawings.
    // Returns { drawing, hitType: 'anchor'|'midpoint'|'edge'|'fill'|'body', handleIndex?, handleLabel? }
    hitTest(pos, drawings, chartState) {
        if (!chartState || !drawings) return null;

        // Iterate in reverse order (top-most first)
        for (var i = drawings.length - 1; i >= 0; i--) {
            var d = drawings[i];
            if (d.hidden) continue;
            if (d.locked) continue;

            var pixels = this._getPixels(d, chartState);
            if (!pixels || pixels.length === 0) continue;

            var result;

            // BUG-07 fix: correct TradingView hit-test priority order:
            // Anchor → Midpoint → Edge → Fill → Body
            // Previously Body (12px) fired before Midpoint (6px), making midpoint
            // handles impossible to hit on any non-zero-width drawing.

            // 0. Custom handles (e.g. Emoji corner resize handles, Position handles)
            if (d.hitTestHandle) {
                var handleHit = d.hitTestHandle(pos, chartState);
                if (handleHit) {
                    return { drawing: d, hitType: 'edge', handleLabel: handleHit };
                }
            }

            // 1. Anchor handles (highest priority)
            result = this._hitAnchorHandles(pos, d, pixels, chartState);
            if (result) return result;

            // 2. Midpoint handles (second priority)
            result = this._hitMidpointHandles(pos, d, pixels, chartState);
            if (result) return result;

            // 3. Edge hit (line segments — tight threshold)
            result = this._hitEdge(pos, d, pixels, chartState);
            if (result) return result;

            // 4. Fill hit (for filled shapes)
            result = this._hitFill(pos, d, pixels, chartState);
            if (result) return result;

            // 5. Body hit (wide threshold — last resort)
            result = this._hitBody(pos, d, pixels, chartState);
            if (result) return result;
        }

        return null;
    }

    // Check if a specific anchor handle of a drawing is hit
    hitTestAnchor(pos, drawing, chartState) {
        var pixels = this._getPixels(drawing, chartState);
        if (!pixels) return null;
        return this._hitAnchorHandles(pos, drawing, pixels, chartState);
    }

    // Check if any handle (anchor or midpoint or custom) is hit
    hitTestAnyHandle(pos, drawing, chartState) {
        if (drawing.hitTestHandle) {
            var customHandle = drawing.hitTestHandle(pos, chartState);
            if (customHandle) {
                return { drawing: drawing, hitType: 'edge', handleLabel: customHandle };
            }
        }
        var pixels = this._getPixels(drawing, chartState);
        if (!pixels) return null;
        var result = this._hitAnchorHandles(pos, drawing, pixels, chartState);
        if (result) return result;
        result = this._hitMidpointHandles(pos, drawing, pixels, chartState);
        if (result) return result;
        
        var edgeResult = this._hitEdge(pos, drawing, pixels, chartState);
        if (edgeResult && edgeResult.handleLabel) {
            return edgeResult;
        }
        return null;
    }

    _getPixels(d, chartState) {
        if (d.getPixels) return d.getPixels(chartState);
        if (d.coords && chartState.coordToPixel) {
            return d.coords.map(function(c) { return chartState.coordToPixel ? chartState.coordToPixel(c) : null; }).filter(Boolean);
        }
        return [];
    }

    _hitAnchorHandles(pos, d, pixels, chartState) {
        var anchors = d.getAnchorPoints ? d.getAnchorPoints(pixels) : pixels;
        for (var i = 0; i < anchors.length; i++) {
            var p = anchors[i];
            if (!p) continue;
            var dist = Math.sqrt((pos.x - p.x) * (pos.x - p.x) + (pos.y - p.y) * (pos.y - p.y));
            if (dist <= this.anchorRadius) {
                return { drawing: d, hitType: 'anchor', handleIndex: i, pixel: p };
            }
        }
        return null;
    }

    _hitMidpointHandles(pos, d, pixels, chartState) {
        var mids = d.getMidpoints ? d.getMidpoints(pixels) : [];
        for (var i = 0; i < mids.length; i++) {
            var m = mids[i];
            if (!m) continue;
            var dist = Math.sqrt((pos.x - m.x) * (pos.x - m.x) + (pos.y - m.y) * (pos.y - m.y));
            if (dist <= this.midpointRadius) {
                return { drawing: d, hitType: 'midpoint', handleIndex: i, pixel: m };
            }
        }
        return null;
    }

    _hitEdge(pos, d, pixels, chartState) {
        // Use per-drawing custom hit test if available (e.g., LongPosition)
        if (d.hitTestHandle) {
            var handleHit = d.hitTestHandle(pos, chartState);
            if (handleHit) {
                return { drawing: d, hitType: 'edge', handleLabel: handleHit };
            }
        }
        
        var segments = d.getEdgeSegments ? d.getEdgeSegments(pixels, chartState) : null;
        if (segments) {
            for (var i = 0; i < segments.length; i++) {
                if (!segments[i].p1 || !segments[i].p2) continue;
                var seg = GeometryUtils.pointToLineSegment(pos, segments[i].p1, segments[i].p2);
                if (seg.dist <= this.edgeThreshold) {
                    return { drawing: d, hitType: 'edge', handleIndex: i, pixel: seg.closest };
                }
            }
        } else {
            // Check each segment
            for (var i = 0; i < pixels.length - 1; i++) {
                var p1 = pixels[i], p2 = pixels[i + 1];
                if (!p1 || !p2) continue;
                var seg = GeometryUtils.pointToLineSegment(pos, p1, p2);
                if (seg.dist <= this.edgeThreshold) {
                    return { drawing: d, hitType: 'edge', handleIndex: i, pixel: seg.closest };
                }
            }
        }
        return null;
    }

    _hitFill(pos, d, pixels, chartState) {
        if (!d.getFillShape) return null;
        var verts = d.getFillShape(pixels);
        if (!verts || verts.length < 3) return null;
        if (GeometryUtils.pointInPolygon(pos, verts)) {
            return { drawing: d, hitType: 'fill' };
        }
        return null;
    }

    _hitBody(pos, d, pixels, chartState) {
        var segments = d.getEdgeSegments ? d.getEdgeSegments(pixels, chartState) : null;
        if (segments) {
            for (var i = 0; i < segments.length; i++) {
                if (!segments[i].p1 || !segments[i].p2) continue;
                var seg = GeometryUtils.pointToLineSegment(pos, segments[i].p1, segments[i].p2);
                if (seg.dist <= this.bodyThreshold) {
                    return { drawing: d, hitType: 'body', handleIndex: i, pixel: seg.closest };
                }
            }
        } else {
            // Check each segment with wider threshold
            for (var i = 0; i < pixels.length - 1; i++) {
                var p1 = pixels[i], p2 = pixels[i + 1];
                if (!p1 || !p2) continue;
                var seg = GeometryUtils.pointToLineSegment(pos, p1, p2);
                if (seg.dist <= this.bodyThreshold) {
                    return { drawing: d, hitType: 'body', handleIndex: i, pixel: seg.closest };
                }
            }
        }
        // For single-point drawings, check distance to point (dynamic radius matching drawing size)
        if (pixels.length === 1 && pixels[0]) {
            var p = pixels[0];
            var size = (d.style && (d.style.fontSize || d.style.size)) || (d.getObjectDef && d.getObjectDef() && d.getObjectDef().defaults && (d.getObjectDef().defaults.fontSize || d.getObjectDef().defaults.size)) || 28;
            var radius = Math.max(this.bodyThreshold, size / 2 + 8);
            var dist = Math.sqrt((pos.x - p.x) * (pos.x - p.x) + (pos.y - p.y) * (pos.y - p.y));
            if (dist <= radius) {
                return { drawing: d, hitType: 'body', handleIndex: 0, pixel: p };
            }
        }
        return null;
    }
}

// =============================================================================
// DrawingModel — Pure data model (Document 2 §1)
// =============================================================================

let _drawingIdCounter = 0;
function _generateId() {
    return 'd_' + (_drawingIdCounter++).toString(16) + '_' + Date.now().toString(36);
}

class DrawingModel {
    constructor(data) {
        const now = new Date().toISOString();
        this.id = data.id || _generateId();
        this.type = data.type || 'unknown';
        this.schemaVersion = data.schemaVersion || 3;  // v3 = {time,price} canonical (v2 = {logical,price})
        this.name = data.name || null;
        // ---------------------------------------------------------------
        // PHASE A — CANONICAL ANCHOR = { time, price }   (BUG-001)
        // ---------------------------------------------------------------
        // `logical` (bar index) is DERIVED and may be recomputed at any
        // moment; `time` is the persisted source of truth. Bar indices are
        // array-relative, so they silently change on timeframe switch and
        // whenever older history is prepended (HC-001/HC-002). A market
        // timestamp does not.
        //
        // Legacy records store only { logical, price }. They are upgraded
        // here by resolving the index against the currently loaded bars. If
        // that cannot be done safely (no bars loaded yet), the original
        // logical value is PRESERVED and the point is flagged
        // `_needsTimeResolve` so a later render can upgrade it -- legacy
        // drawings are never discarded or silently guessed at (A2).
        this.points = (data.points || []).map(p => {
            // Use _barTimeSec so that "YYYY-MM-DD" date strings (1D business-day mode)
            // are correctly converted to epoch seconds rather than NaN via Number().
            var cm0 = window.coordinateMapper;
            var _toSec = (cm0 && typeof cm0._barTimeSec === 'function')
                ? function(v) { return cm0._barTimeSec(v); }
                : function(v) { return Number(v); };
            var time = (p.time !== undefined && p.time !== null) ? _toSec(p.time) : null;
            if (time !== null && isNaN(time)) time = null;
            var logical = p.logical !== undefined ? p.logical
                        : (p.logicalIndex !== undefined ? p.logicalIndex : null);
            var needsResolve = false;

            if (time === null && logical !== null && logical !== undefined) {
                var cm = window.coordinateMapper;
                var resolved = (cm && typeof cm.logicalToTime === 'function')
                    ? cm.logicalToTime(logical) : null;
                if (resolved !== null && resolved !== undefined && !isNaN(resolved)) {
                    time = Number(resolved);
                } else {
                    needsResolve = true;   // keep legacy logical, retry later
                    if (window.console && console.debug) {
                        console.debug('[DrawingModel] legacy point kept for later time-resolve', p);
                    }
                }
            }
            if (logical === null && time !== null) {
                var cm2 = window.coordinateMapper;
                if (cm2 && typeof cm2.timeToLogical === 'function') {
                    var lg = cm2.timeToLogical(time);
                    if (lg !== null && lg !== undefined && !isNaN(lg)) logical = lg;
                }
            }
            var pt = {
                time: time,
                logical: (logical !== undefined) ? logical : null,
                price: p.price !== undefined ? p.price : null
            };
            if (needsResolve) pt._needsTimeResolve = true;
            return pt;
        });
        this.style = Object.assign({
            color: '#3366FF',
            opacity: 0.8,
            thickness: 2,
            lineStyle: 'solid',
            fillColor: null,
            fillOpacity: 0,
            extendLeft: false,
            extendRight: false,
            arrowStart: false,
            arrowEnd: false,
            background: null
        }, data.style || {});
        this.text = Object.assign({
            content: '',
            fontSize: 12,
            color: '#FFFFFF',
            bold: false,
            italic: false,
            align: 'center',
            rotation: 0,
            background: null
        }, data.text || {});
        this.visibility = Object.assign({
            timeframes: 'all',
            hidden: false
        }, data.visibility || {});
        this.locked = !!data.locked;
        this.zIndex = data.zIndex || 0;
        this.groupId = data.groupId || null;
        this.createdAt = data.createdAt || now;
        this.updatedAt = data.updatedAt || now;
        this.owner = data.owner || null;
        this.alerts = (data.alerts || []).map(a => ({
            id: a.id || _generateId(),
            condition: a.condition || 'crossing',
            enabled: a.enabled !== false,
            notifyVia: a.notifyVia || ['popup'],
            message: a.message || ''
        }));
        this.meta = data.meta ? JSON.parse(JSON.stringify(data.meta)) : {};
        // Phase 3.8: Layer & Object System
        this.layer = Layer.parse(data.layer);
        // Phase 3.9: capabilities object (replaces individual booleans)
        this.capabilities = Object.assign({
            select: true,  drag: true,  rotate: false,
            resize: false, edit: true,  layer: true,
            crop: false,   flip: false
        }, data.capabilities || {});
        // Backward compat: read old individual booleans if capabilities absent
        if (data.selectable !== undefined && !data.capabilities) this.capabilities.select = data.selectable;
        if (data.draggable !== undefined && !data.capabilities) this.capabilities.drag = data.draggable;
        if (data.rotatable !== undefined && !data.capabilities) this.capabilities.rotate = data.rotatable;
        if (data.resizable !== undefined && !data.capabilities) this.capabilities.resize = data.resizable;
        if (data.editable !== undefined && !data.capabilities) this.capabilities.edit = data.editable;
        if (data.layerable !== undefined && !data.capabilities) this.capabilities.layer = data.layerable;
        // Phase 3.9: Structured content model
        this.content = this._normalizeContent(data.content, data.text);
        // Phase 3.9: Asset reference (for images, icons, emoji)
        this.assetId = data.assetId || null;
        this.assetUrl = data.assetUrl || null;
        // Phase 3.9: Rotation (degrees)
        this.rotation = data.rotation || 0;
        this.templateId = data.templateId || null;
        this.templateVersion = data.templateVersion || null;
        this.tags = data.tags ? data.tags.slice() : [];
    }

    // Update a subset of properties, updating the timestamp
    update(changes) {
        if (changes.points !== undefined) {
            this.points = changes.points.map(p => {
                var logical = p.logical !== undefined ? p.logical : (p.logicalIndex !== undefined ? p.logicalIndex : p.time);
                return {
                    logical: logical !== undefined ? logical : null,
                    price: p.price !== undefined ? p.price : null
                };
            });
        }
        if (changes.style !== undefined) {
            Object.assign(this.style, changes.style);
        }
        if (changes.text !== undefined) {
            Object.assign(this.text, changes.text);
        }
        if (changes.visibility !== undefined) {
            Object.assign(this.visibility, changes.visibility);
        }
        if (changes.locked !== undefined) this.locked = !!changes.locked;
        if (changes.zIndex !== undefined) this.zIndex = changes.zIndex;
        if (changes.groupId !== undefined) this.groupId = changes.groupId;
        if (changes.name !== undefined) this.name = changes.name;
        if (changes.meta !== undefined) {
            this.meta = JSON.parse(JSON.stringify(changes.meta));
        }
        if (changes.alerts !== undefined) {
            this.alerts = changes.alerts.map(a => ({
                id: a.id || _generateId(),
                condition: a.condition || 'crossing',
                enabled: a.enabled !== false,
                notifyVia: a.notifyVia || ['popup'],
                message: a.message || ''
            }));
        }
        if (changes.layer !== undefined) this.layer = Layer.parse(changes.layer);
        if (changes.capabilities !== undefined) {
            Object.assign(this.capabilities, changes.capabilities);
        }
        if (changes.content !== undefined) {
            this.content = this._normalizeContent(changes.content, null);
        }
        if (changes.rotation !== undefined) this.rotation = changes.rotation;
        if (changes.assetId !== undefined) this.assetId = changes.assetId;
        if (changes.assetUrl !== undefined) this.assetUrl = changes.assetUrl;
        if (changes.templateId !== undefined) this.templateId = changes.templateId;
        if (changes.templateVersion !== undefined) this.templateVersion = changes.templateVersion;
        if (changes.tags !== undefined) this.tags = changes.tags.slice();
        this.updatedAt = new Date().toISOString();
    }

    // Check for degenerate geometry (Document 2 §7 validation)
    validate() {
        const valid = { valid: true, message: '' };
        if (this.points.length < 1) {
            return { valid: false, message: 'Drawing must have at least 1 point.' };
        }
        // Check for zero-length lines (2+ points where all have same time and price)
        if (this.points.length >= 2) {
            const allSame = this.points.every(p =>
                p.logical === this.points[0].logical && p.price === this.points[0].price
            );
            if (allSame && this.points.length > 1) {
                return { valid: false, message: 'Cannot place a zero-length drawing. Move the cursor further from the starting point.' };
            }
        }
        return valid;
    }

    // Deep clone
    clone() {
        return new DrawingModel({
            id: this.id,
            type: this.type,
            schemaVersion: this.schemaVersion,
            name: this.name,
            // time is canonical; logical persisted only as a rendering hint
            // and is recomputed from time on load (PHASE A / BUG-001).
            points: this.points.map(p => {
                var out = { time: p.time, price: p.price, logical: p.logical };
                if (p._needsTimeResolve) out._needsTimeResolve = true;
                return out;
            }),
            style: JSON.parse(JSON.stringify(this.style)),
            text: JSON.parse(JSON.stringify(this.text)),
            visibility: JSON.parse(JSON.stringify(this.visibility)),
            locked: this.locked,
            zIndex: this.zIndex,
            groupId: this.groupId,
            createdAt: this.createdAt,
            updatedAt: this.updatedAt,
            owner: this.owner,
            alerts: JSON.parse(JSON.stringify(this.alerts)),
            meta: JSON.parse(JSON.stringify(this.meta)),
            layer: this.layer,
            capabilities: Object.assign({}, this.capabilities),
            content: { plain: this.content.plain, rich: this.content.rich, markdown: this.content.markdown },
            rotation: this.rotation,
            assetId: this.assetId,
            assetUrl: this.assetUrl,
            templateId: this.templateId,
            templateVersion: this.templateVersion,
            tags: this.tags.slice()
        });
    }

    // Serialize to plain JSON object (Document 2 §9)
    toJSON() {
        return {
            schemaVersion: this.schemaVersion,
            id: this.id,
            type: this.type,
            name: this.name,
            // PHASE A (BUG-001): `time` is the CANONICAL persisted anchor.
            // This is the real serializer -- an identical points line also
            // exists in clone(), and patching that one left THIS one
            // dropping time, so saved drawings were still bar-index only.
            points: this.points.map(p => {
                var out = { time: p.time, price: p.price, logical: p.logical };
                if (p._needsTimeResolve) out._needsTimeResolve = true;
                return out;
            }),
            style: JSON.parse(JSON.stringify(this.style)),
            text: JSON.parse(JSON.stringify(this.text)),
            visibility: Object.assign({}, this.visibility),
            locked: this.locked,
            zIndex: this.zIndex,
            groupId: this.groupId,
            createdAt: this.createdAt,
            updatedAt: this.updatedAt,
            owner: this.owner,
            alerts: JSON.parse(JSON.stringify(this.alerts)),
            meta: JSON.parse(JSON.stringify(this.meta)),
            layer: Layer.nameOf(this.layer),
            capabilities: Object.assign({}, this.capabilities),
            content: { plain: this.content.plain, rich: this.content.rich, markdown: this.content.markdown },
            rotation: this.rotation,
            assetId: this.assetId,
            assetUrl: this.assetUrl,
            templateId: this.templateId,
            templateVersion: this.templateVersion,
            tags: this.tags.slice()
        };
    }

    // Deserialize from plain JSON object
    static fromJSON(json) {
        return new DrawingModel(json);
    }

    // Normalize content field (supports string or structured {plain, rich, markdown})
    _normalizeContent(content, oldText) {
        if (content && typeof content === 'object' && content.plain !== undefined) {
            return { plain: content.plain || '', rich: content.rich || null, markdown: content.markdown || null };
        }
        if (typeof content === 'string') {
            return { plain: content, rich: null, markdown: null };
        }
        // Backward compat: migrate from old text.content
        if (oldText && oldText.content) {
            return { plain: oldText.content, rich: null, markdown: null };
        }
        return { plain: '', rich: null, markdown: null };
    }

    // Auto-generate name like "Trend Line 1"
    static generateName(type, existingNames) {
        const base = type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        let n = 1;
        while (existingNames.indexOf(base + ' ' + n) !== -1) { n++; }
        return base + ' ' + n;
    }
}

// =============================================================================
// SelectionManager — Selection model (Document 2 §5)
// =============================================================================

class SelectionManager {
    constructor(eventBus) {
        this._selected = new Set();   // Set<drawingId>
        this._hoveredId = null;       // string | null
        this._eventBus = eventBus || null;
    }

    // Select a single drawing (replaces selection)
    select(drawingId) {
        if (this._selected.size === 1 && this._selected.has(drawingId)) return;
        const prev = Array.from(this._selected);
        this._selected.clear();
        this._selected.add(drawingId);
        this._emitSelect();
        this._emitDeselect(prev.filter(id => id !== drawingId));
    }

    // Toggle a drawing in/out of the selection set (Ctrl+Click)
    toggle(drawingId) {
        const wasSelected = this._selected.has(drawingId);
        if (wasSelected) {
            this._selected.delete(drawingId);
            this._emitDeselect([drawingId]);
        } else {
            this._selected.add(drawingId);
            this._emitSelect();
        }
    }

    // Add to selection (for multi-select)
    add(drawingId) {
        if (!this._selected.has(drawingId)) {
            this._selected.add(drawingId);
            this._emitSelect();
        }
    }

    // Clear selection
    clear() {
        const prev = Array.from(this._selected);
        if (prev.length === 0) return;
        this._selected.clear();
        this._emitDeselect(prev);
    }

    // Replace entire selection set
    setSelection(ids) {
        const prev = Array.from(this._selected);
        this._selected = new Set(ids);
        const newSelected = Array.from(this._selected);
        const deselect = prev.filter(id => !this._selected.has(id));
        if (deselect.length > 0) this._emitDeselect(deselect);
        if (newSelected.length > 0) this._emitSelect();
    }

    // Check if a drawing is selected
    isSelected(drawingId) {
        return this._selected.has(drawingId);
    }

    // Get all selected IDs
    getSelected() {
        return Array.from(this._selected);
    }

    // Get the first/primary selected ID (for single-select operations)
    getPrimary() {
        if (this._selected.size === 0) return null;
        return this._selected.values().next().value;
    }

    // Get selection count
    get count() { return this._selected.size; }

    // Hover handling
    set hoveredId(id) {
        if (this._hoveredId === id) return;
        const prev = this._hoveredId;
        this._hoveredId = id;
        if (this._eventBus) {
            this._eventBus.emit('drawing:hover', { id: id });
        }
    }

    get hoveredId() { return this._hoveredId; }

    // Remove deleted drawing from selection
    onDrawingDeleted(drawingId) {
        if (this._selected.has(drawingId)) {
            this._selected.delete(drawingId);
            this._emitDeselect([drawingId]);
        }
        if (this._hoveredId === drawingId) {
            this._hoveredId = null;
        }
    }

    _emitSelect() {
        if (this._eventBus) {
            this._eventBus.emit('drawing:select', { ids: Array.from(this._selected) });
        }
    }

    _emitDeselect(ids) {
        if (this._eventBus && ids.length > 0) {
            this._eventBus.emit('drawing:deselect', { ids: ids });
        }
    }
}

// =============================================================================
// UndoRedoManager — Command pattern (Document 2 §8)
// =============================================================================

class UndoRedoManager {
    constructor(options) {
        this._maxStack = options && options.maxStack ? options.maxStack : 100;
        this._stack = [];
        this._cursor = -1; // Points to the current state (-1 = initial empty)
        this._isRestoring = false;
        this._isBatching = false;
        this._batchCommand = null;
    }

    // Begin a batched operation (e.g., drag that spans multiple frames)
    beginBatch() {
        this._isBatching = true;
    }

    // End batch and push the accumulated command
    endBatch() {
        if (!this._isBatching) return;
        this._isBatching = false;
        if (this._batchCommand) {
            this._pushCommand(this._batchCommand);
            this._batchCommand = null;
        }
    }

    // Push a command (or buffer it if batching)
    pushCommand(type, before, after) {
        const cmd = { type, before: this._deepClone(before), after: this._deepClone(after) };
        if (this._isBatching) {
            // Update the batch command to reflect the latest state
            if (!this._batchCommand) {
                this._batchCommand = cmd;
            } else {
                // Keep the original 'before' from the first frame, update 'after' with latest
                this._batchCommand.type = cmd.type;
                this._batchCommand.after = this._deepClone(after);
            }
            return;
        }
        this._pushCommand(cmd);
    }

    _pushCommand(cmd) {
        // BUG-11 fix: commands pushed during restore corrupt the stack.
        // When _applyCommandSnapshot restores state it may trigger syncToModel
        // which can call markDirty → autosave → pushCommand. Gate it here.
        if (this._isRestoring) return;

        // Clear any redo entries beyond cursor
        this._stack.length = this._cursor + 1;
        this._stack.push(cmd);
        // BUG-02 fix: when the stack overflows and we shift() an entry,
        // _cursor must stay aligned to the new top. Previously cursor was
        // only incremented in the else branch, so after the first overflow
        // all subsequent undo/redo commands were off by one.
        if (this._stack.length > this._maxStack) {
            this._stack.shift();
            // cursor stays the same — it now points at the new top element
        } else {
            this._cursor++;
        }
    }

    // Execute undo: returns the command to apply, or null
    undo() {
        if (this._cursor < 0) return null;
        const cmd = this._stack[this._cursor];
        this._cursor--;
        this._isRestoring = true;
        // Return the 'before' snapshot so the caller can restore it
        return cmd;
    }

    // Execute redo: returns the command to apply, or null
    redo() {
        if (this._cursor >= this._stack.length - 1) return null;
        this._cursor++;
        const cmd = this._stack[this._cursor];
        this._isRestoring = true;
        // Return the 'after' snapshot so the caller can restore it
        return cmd;
    }

    // Finish restoring (called after the state has been restored)
    finishRestore() {
        this._isRestoring = false;
    }

    get isRestoring() { return this._isRestoring; }

    // Clear the entire undo stack
    clear() {
        this._stack = [];
        this._cursor = -1;
    }

    // After a new drawing is created, clear the redo stack (per §8 rules)
    onNewDrawing() {
        if (this._cursor < this._stack.length - 1) {
            this._stack.length = this._cursor + 1;
        }
    }

    _deepClone(obj) {
        return JSON.parse(JSON.stringify(obj));
    }
}

// =============================================================================
// BaseCommand — abstract command for ObjectManager (Phase 3.8)
// =============================================================================

class BaseCommand {
    constructor(type, drawingIds) {
        this.type = type || 'unknown';
        this.drawingIds = drawingIds || [];
        this.before = null;
        this.after = null;
    }
    // Capture the current state of all involved drawings
    capture(engine) {
        var state = {};
        for (var i = 0; i < this.drawingIds.length; i++) {
            var d = engine.getDrawing(this.drawingIds[i]);
            if (d) {
                var model = d.model || d;
                state[this.drawingIds[i]] = model.toJSON();
            }
        }
        return state;
    }
    // Override in subclasses
    execute(engine) {}
}

// Concrete commands
class BringToFrontCommand extends BaseCommand {
    constructor(drawingId) { super('bring_to_front', [drawingId]); this.drawingId = drawingId; }
    execute(engine) {
        var d = engine.getDrawing(this.drawingId);
        if (!d) return;
        var model = d.model || d;
        model.zIndex = engine.layerManager.nextZ();
        model.updatedAt = new Date().toISOString();
    }
}

class SendToBackCommand extends BaseCommand {
    constructor(drawingId) { super('send_to_back', [drawingId]); this.drawingId = drawingId; }
    execute(engine) {
        var d = engine.getDrawing(this.drawingId);
        if (!d) return;
        var model = d.model || d;
        var minZ = engine.layerManager.minZ(engine.drawings);
        model.zIndex = Math.max(0, minZ - 10);
        model.updatedAt = new Date().toISOString();
    }
}

class SetLayerCommand extends BaseCommand {
    constructor(drawingId, layer) { super('set_layer', [drawingId]); this.drawingId = drawingId; this.layer = layer; }
    execute(engine) {
        var d = engine.getDrawing(this.drawingId);
        if (!d) return;
        var model = d.model || d;
        model.layer = this.layer;
        model.updatedAt = new Date().toISOString();
    }
}

class ToggleLockCommand extends BaseCommand {
    constructor(drawingId, locked) { super(locked ? 'lock' : 'unlock', [drawingId]); this.drawingId = drawingId; this.locked = locked; }
    execute(engine) {
        var d = engine.getDrawing(this.drawingId);
        if (!d) return;
        var model = d.model || d;
        model.locked = this.locked;
        if (d !== model) d.locked = this.locked;
        model.updatedAt = new Date().toISOString();
    }
}

class ToggleVisibilityCommand extends BaseCommand {
    constructor(drawingId, hidden) { super(hidden ? 'hide' : 'show', [drawingId]); this.drawingId = drawingId; this.hidden = hidden; }
    execute(engine) {
        var d = engine.getDrawing(this.drawingId);
        if (!d) return;
        var model = d.model || d;
        if (!model.visibility) model.visibility = { timeframes: 'all', hidden: false };
        model.visibility.hidden = this.hidden;
        model.updatedAt = new Date().toISOString();
        if (d !== model) d.hidden = this.hidden;
    }
}

class GroupCommand extends BaseCommand {
    constructor(drawingIds, groupId) { super('group', drawingIds); this.groupId = groupId || 'g_' + Date.now().toString(36); }
    execute(engine) {
        for (var i = 0; i < this.drawingIds.length; i++) {
            var d = engine.getDrawing(this.drawingIds[i]);
            if (!d) continue;
            var model = d.model || d;
            model.groupId = this.groupId;
            model.updatedAt = new Date().toISOString();
            if (d !== model) d.groupId = this.groupId;
        }
    }
}

class UngroupCommand extends BaseCommand {
    constructor(groupId) { super('ungroup', []); this.groupId = groupId; }
    execute(engine) {
        for (var i = 0; i < engine.drawings.length; i++) {
            var d = engine.drawings[i];
            var model = d.model || d;
            if (model.groupId === this.groupId) {
                model.groupId = null;
                model.updatedAt = new Date().toISOString();
                if (d !== model) d.groupId = null;
            }
        }
    }
}

class DeleteCommand extends BaseCommand {
    constructor(drawingId) { super('delete', [drawingId]); this.drawingId = drawingId; }
    execute(engine) {
        var idx = engine.drawings.findIndex(function(d) { return (d.model ? d.model.id : d.id) === this.drawingId; }.bind(this));
        if (idx === -1) return false;
        engine.drawings.splice(idx, 1);
        engine.selection.onDrawingDeleted(this.drawingId);
        return true;
    }
}

class DuplicateCommand extends BaseCommand {
    constructor(drawingId) { super('duplicate', [drawingId]); this.drawingId = drawingId; this.newId = null; }
    execute(engine) {
        var drawing = engine.getDrawing(this.drawingId);
        if (!drawing) return null;
        var sourceModel = drawing.model || drawing;
        var existingNames = engine.drawings
            .map(function(d) { return (d.model ? d.model.name : d.name) || ''; })
            .filter(function(n) { return n; });
        var name = DrawingModel.generateName(sourceModel.type, existingNames);
        var clonedModel = sourceModel.clone();
        clonedModel.id = 'd_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,6);
        clonedModel.name = name;
        clonedModel.zIndex = engine.layerManager.nextZ();
        clonedModel.createdAt = new Date().toISOString();
        clonedModel.updatedAt = new Date().toISOString();
        var Cls = drawing.constructor;
        var renderer = Object.create(Cls.prototype);
        renderer.coords = clonedModel.points.map(function(p) { return { logical: p.logical, price: p.price }; });
        renderer.style = JSON.parse(JSON.stringify(clonedModel.style));
        renderer.locked = !!clonedModel.locked;
        renderer.hidden = !!(clonedModel.visibility && clonedModel.visibility.hidden);
        renderer.model = clonedModel;
        renderer.currentPos = renderer.coords.length > 0 ? { x: 0, y: 0 } : null;
        if (renderer.normalizeStyle) renderer.normalizeStyle();
        engine.drawings.push(renderer);
        this.newId = clonedModel.id;
        return renderer;
    }
}

// =============================================================================
// ObjectManager — high-level drawing lifecycle using command execution (Phase 3.8)
// =============================================================================

class ObjectManager {
    constructor(engine) {
        this.engine = engine;
    }

    // Execute a command, capturing before/after state for undo
    execute(command) {
        var before = command.capture(this.engine);
        var result = command.execute(this.engine);
        var after = command.capture(this.engine);
        // Push snapshot-based undo via the existing UndoRedoManager
        this.engine.undoManager.pushCommand(command.type, before, after);
        this.engine.markDirty();
        this.engine.eventBus.emit('drawing:command', { type: command.type, ids: command.drawingIds });
        return result;
    }

    // Convenience wrappers
    bringToFront(drawingId) { return this.execute(new BringToFrontCommand(drawingId)); }
    sendToBack(drawingId)   { return this.execute(new SendToBackCommand(drawingId)); }
    setLayer(drawingId, layer) { return this.execute(new SetLayerCommand(drawingId, layer)); }
    lock(drawingId)   { return this.execute(new ToggleLockCommand(drawingId, true)); }
    unlock(drawingId) { return this.execute(new ToggleLockCommand(drawingId, false)); }
    hide(drawingId)   { return this.execute(new ToggleVisibilityCommand(drawingId, true)); }
    show(drawingId)   { return this.execute(new ToggleVisibilityCommand(drawingId, false)); }
    group(drawingIds, groupId) { return this.execute(new GroupCommand(drawingIds, groupId)); }
    ungroup(groupId)  { return this.execute(new UngroupCommand(groupId)); }
    remove(drawingId) { return this.execute(new DeleteCommand(drawingId)); }
    duplicate(drawingId) { return this.execute(new DuplicateCommand(drawingId)); }
}

// =============================================================================
// Serializer — Spec-compliant serialization (Document 2 §9)
// =============================================================================

class Serializer {
    constructor(options) {
        this._storagePrefix = options && options.storagePrefix ? options.storagePrefix : 'tv_drawings_';
        this._storage = options && options.storage ? options.storage : localStorage;
    }

    // Serialize an array of DrawingModel objects to JSON
    toJSON(drawings) {
        return drawings.map(d => d.toJSON());
    }

    // Deserialize JSON to DrawingModel objects
    fromJSON(jsonArray) {
        if (!Array.isArray(jsonArray)) return [];
        return jsonArray
            .map(item => {
                try {
                    return DrawingModel.fromJSON(item);
                } catch (e) {
                    console.error('Serializer: failed to parse drawing', e);
                    return null;
                }
            })
            .filter(d => d !== null);
    }

    // Save to storage, keyed by (user, symbol, layout)
    save(drawings, opts) {
        const key = this._storageKey(opts);
        try {
            const data = this.toJSON(drawings);
            this._storage.setItem(key, JSON.stringify(data));
            return true;
        } catch (e) {
            console.error('Serializer: save failed', e);
            return false;
        }
    }

    // Load from storage
    load(opts) {
        const key = this._storageKey(opts);
        try {
            const raw = this._storage.getItem(key);
            if (!raw) return [];
            const data = JSON.parse(raw);
            return this.fromJSON(data);
        } catch (e) {
            console.error('Serializer: load failed', e);
            return [];
        }
    }

    // Remove from storage
    remove(opts) {
        const key = this._storageKey(opts);
        try {
            this._storage.removeItem(key);
            return true;
        } catch (e) {
            console.error('Serializer: remove failed', e);
            return false;
        }
    }

    // List all stored keys matching the prefix
    listKeys() {
        const keys = [];
        for (let i = 0; i < this._storage.length; i++) {
            const key = this._storage.key(i);
            if (key && key.startsWith(this._storagePrefix)) {
                keys.push(key);
            }
        }
        return keys;
    }

    _storageKey(opts) {
        const user = (opts && opts.user) || 'default';
        const symbol = (opts && opts.symbol) || window.currentTicker || 'default';
        const layout = (opts && opts.layout) || 'default';
        return this._storagePrefix + user + '_' + symbol + '_' + layout;
    }
}

// =============================================================================
// DrawingTemplate — versioned style template (Phase 3.8)
// =============================================================================

class DrawingTemplate {
    constructor(data) {
        this.id = data.id || 'tpl_' + Date.now().toString(36);
        this.version = data.version || 1;
        this.name = data.name || 'Untitled Template';
        this.style = data.style ? JSON.parse(JSON.stringify(data.style)) : {};
        this.createdAt = data.createdAt || new Date().toISOString();
        this.updatedAt = data.updatedAt || new Date().toISOString();
    }

    // Apply template styles to a renderer
    apply(renderer) {
        if (!renderer || !renderer.style) return;
        Object.assign(renderer.style, JSON.parse(JSON.stringify(this.style)));
        if (renderer.normalizeStyle) renderer.normalizeStyle();
        if (renderer.model) {
            Object.assign(renderer.model.style, JSON.parse(JSON.stringify(this.style)));
            renderer.model.updatedAt = new Date().toISOString();
        }
    }

    toJSON() {
        return {
            id: this.id,
            version: this.version,
            name: this.name,
            style: JSON.parse(JSON.stringify(this.style)),
            createdAt: this.createdAt,
            updatedAt: this.updatedAt
        };
    }

    static fromJSON(json) { return new DrawingTemplate(json); }
}

// =============================================================================
// TemplateManager — template lifecycle (Phase 3.8)
// Storage-agnostic: uses localStorage by default, but can be swapped.
// =============================================================================

class TemplateManager {
    constructor(options) {
        this._storageKey = (options && options.storageKey) || 'tv_drawing_templates';
        this._storage = (options && options.storage) || localStorage;
        this._templates = [];
        this._load();
    }

    // Save current style as a new template
    save(name, style) {
        var tpl = new DrawingTemplate({
            name: name,
            style: style
        });
        this._templates.push(tpl);
        this._persist();
        return tpl;
    }

    // Apply a template by ID to a renderer
    apply(templateId, renderer) {
        var tpl = this.get(templateId);
        if (!tpl) return false;
        tpl.apply(renderer);
        return true;
    }

    // Get a template by ID
    get(templateId) {
        for (var i = 0; i < this._templates.length; i++) {
            if (this._templates[i].id === templateId) return this._templates[i];
        }
        return null;
    }

    // List all templates
    list() {
        return this._templates.slice();
    }

    // Delete a template by ID
    delete(templateId) {
        var idx = -1;
        for (var i = 0; i < this._templates.length; i++) {
            if (this._templates[i].id === templateId) { idx = i; break; }
        }
        if (idx === -1) return false;
        this._templates.splice(idx, 1);
        this._persist();
        return true;
    }

    // Update an existing template
    update(templateId, changes) {
        var tpl = this.get(templateId);
        if (!tpl) return false;
        if (changes.name !== undefined) tpl.name = changes.name;
        if (changes.style !== undefined) tpl.style = JSON.parse(JSON.stringify(changes.style));
        tpl.version++;
        tpl.updatedAt = new Date().toISOString();
        this._persist();
        return true;
    }

    _load() {
        try {
            var raw = this._storage.getItem(this._storageKey);
            if (!raw) return;
            var data = JSON.parse(raw);
            this._templates = data.map(function(d) { return DrawingTemplate.fromJSON(d); });
        } catch (e) {
            console.warn('TemplateManager: load failed', e);
            this._templates = [];
        }
    }

    _persist() {
        try {
            this._storage.setItem(this._storageKey, JSON.stringify(this._templates.map(function(t) { return t.toJSON(); })));
        } catch (e) {
            console.warn('TemplateManager: persist failed', e);
        }
    }

    clear() {
        this._templates = [];
        this._persist();
    }
}

// =============================================================================
// AssetManager — image/emoji/icon cache with lazy loading (Phase 3.9)
// Storage-agnostic: currently in-memory + basic localStorage cache.
// =============================================================================

class AssetManager {
    constructor(options) {
        this._images = new Map();     // assetId → HTMLImageElement
        this._pending = new Map();    // assetId → Promise<HTMLImageElement>
        this._iconPack = new Map();   // iconName → { path, viewBox }
        this._maxCache = (options && options.maxCache) || 200;
        this._onError = (options && options.onError) || function(assetId) { console.warn('AssetManager: failed to load', assetId); };
        this._registerDefaultIcons();
    }

    // Load an image by assetId + URL (lazy, cached, deduped)
    loadImage(assetId, url) {
        if (this._images.has(assetId)) return Promise.resolve(this._images.get(assetId));
        if (this._pending.has(assetId)) return this._pending.get(assetId);
        var self = this;
        var promise = new Promise(function(resolve, reject) {
            var img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = function() {
                self._images.set(assetId, img);
                self._pending.delete(assetId);
                self._evictIfNeeded();
                resolve(img);
            };
            img.onerror = function() {
                self._pending.delete(assetId);
                self._onError(assetId);
                reject(new Error('Failed to load: ' + url));
            };
            img.src = url;
        });
        this._pending.set(assetId, promise);
        return promise;
    }

    // Synchronous access (returns null if not loaded yet)
    getImage(assetId) {
        return this._images.get(assetId) || null;
    }

    // Preload multiple images
    preload(assets) {
        return Promise.all(assets.map(function(a) { return this.loadImage(a.id, a.url); }.bind(this)));
    }

    // Release a specific asset from cache
    release(assetId) {
        this._images.delete(assetId);
        this._pending.delete(assetId);
    }

    // Clear entire cache
    clear() {
        this._images.clear();
        this._pending.clear();
    }

    // ---- Icon management ----

    // Register an icon by name with SVG path data
    registerIcon(name, pathData, viewBox) {
        this._iconPack.set(name, { path: pathData, viewBox: viewBox || '0 0 24 24' });
    }

    // Get icon path data by name
    getIcon(name) {
        return this._iconPack.get(name) || null;
    }

    // Get all registered icon names
    getIconNames() {
        return Array.from(this._iconPack.keys());
    }

    // ---- Emoji helpers ----

    // Check if a string is a single emoji character
    isEmoji(str) {
        if (!str || str.length === 0) return false;
        // Simple heuristic: check if it's in the Unicode emoji ranges
        var code = str.codePointAt(0);
        return (code >= 0x1F300 && code <= 0x1F9FF) ||  // Misc symbols, emoticons, supplements
               (code >= 0x2600 && code <= 0x27BF)  ||  // Misc symbols, dingbats
               (code >= 0xFE00 && code <= 0xFE0F)  ||  // Variation selectors
               (code >= 0x200D) ||                     // ZWJ sequences
               code === 0x00A9 || code === 0x00AE;      // Copyright, registered
    }

    _evictIfNeeded() {
        if (this._images.size <= this._maxCache) return;
        // Simple eviction: delete first entries (oldest)
        var keys = Array.from(this._images.keys());
        var toDelete = keys.slice(0, this._images.size - this._maxCache);
        for (var i = 0; i < toDelete.length; i++) this._images.delete(toDelete[i]);
    }

    _registerDefaultIcons() {
        // Buy/Sell arrows
        this.registerIcon('buy', 'M12,2 L22,22 L2,22 Z', '0 0 24 24');
        this.registerIcon('sell', 'M2,2 L22,2 L12,22 Z', '0 0 24 24');
        // FIX (found by real-browser render sweep): 'long' and 'short' were
        // (a) an OPEN path -- "M2,12 L22,12 M12,2 L12,22" is a stroke-shaped
        // cross with no closed subpath, and coordIcon renders via ctx.fill(),
        // so both stickers filled to NOTHING and were invisible on the chart;
        // and (b) byte-for-byte IDENTICAL to each other, so even once visible a
        // user could not tell a long from a short. Both are now closed,
        // fillable, directional arrows: long = up (bullish), short = down
        // (bearish), matching the buy/sell triangles' visual language.
        this.registerIcon('long', 'M12,2 L20,12 L15,12 L15,22 L9,22 L9,12 L4,12 Z', '0 0 24 24');
        this.registerIcon('short', 'M12,22 L4,12 L9,12 L9,2 L15,2 L15,12 L20,12 Z', '0 0 24 24');
        this.registerIcon('target', 'M12,2 C6.48,2 2,6.48 2,12 C2,17.52 6.48,22 12,22 C17.52,22 22,17.52 22,12 C22,6.48 17.52,2 12,2 Z M12,20 C7.58,20 4,16.42 4,12 C4,7.58 7.58,4 12,4 C16.42,4 20,7.58 20,12 C20,16.42 16.42,20 12,20 Z', '0 0 24 24');
        this.registerIcon('stop', 'M2,2 L22,2 L22,22 L2,22 Z', '0 0 24 24');
        this.registerIcon('star', 'M12,2 L15.09,8.26 L22,9.27 L17,14.14 L18.18,21.02 L12,17.77 L5.82,21.02 L7,14.14 L2,9.27 L8.91,8.26 Z', '0 0 24 24');
        this.registerIcon('pin', 'M16,12V4H17V2H7V4H8V12L6,14V16H11.2V22H12.8V16H18V14Z', '0 0 24 24');
        this.registerIcon('check', 'M9,16.17L4.83,12L3.41,13.41L9,19L21,7L19.59,5.59Z', '0 0 24 24');
        this.registerIcon('warning', 'M1,21H23L12,2M12,18H12.01V18H12M11,16H13V10H11', '0 0 24 24');
        // General icons
        this.registerIcon('note', 'M4,6H20V8H4M4,10H20V12H4M4,14H14V16H4M4,18H12V20H4Z', '0 0 24 24');
        this.registerIcon('info', 'M12,2C6.48,2 2,6.48 2,12C2,17.52 6.48,22 12,22C17.52,22 22,17.52 22,12C22,6.48 17.52,2 12,2Z M13,17H11V11H13V17Z M13,9H11V7H13V9Z', '0 0 24 24');
    }
}

// =============================================================================
// TextEditorManager — DOM overlay for inline text editing (Phase 3.9)
// Manages a hidden textarea that appears on double-click for text objects.
// =============================================================================

class TextEditorManager {
    constructor(options) {
        this._activeId = null;
        this._textarea = null;
        this._container = (options && options.container) || document.body;
        this._onCommit = (options && options.onCommit) || null;
        this._init();
    }

    _init() {
        var ta = document.createElement('textarea');
        ta.style.position = 'absolute';
        ta.style.display = 'none';
        ta.style.zIndex = 99999;
        ta.style.border = '2px solid #2962ff';
        ta.style.borderRadius = '4px';
        ta.style.padding = '6px 8px';
        ta.style.fontFamily = '-apple-system, Roboto, sans-serif';
        ta.style.fontSize = '13px';
        ta.style.lineHeight = '1.4';
        ta.style.outline = 'none';
        ta.style.resize = 'none';
        ta.style.overflow = 'hidden';
        ta.style.background = '#1e222d';
        ta.style.color = '#d1d4dc';
        ta.style.minWidth = '60px';
        ta.style.minHeight = '28px';
        this._textarea = ta;
        this._container.appendChild(ta);

        var self = this;
        ta.addEventListener('blur', function() { self._commit(); });
        ta.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') { self._cancel(); return; }
            if (e.key === 'Enter' && !e.shiftKey) { self._commit(); return; }
            // Auto-resize
            setTimeout(function() {
                ta.style.height = 'auto';
                ta.style.height = Math.max(28, ta.scrollHeight) + 'px';
                ta.style.width = Math.max(60, Math.min(400, ta.scrollWidth)) + 'px';
            }, 0);
        });
    }

    // Start editing a drawing
    edit(drawingId, renderer, pixelPos) {
        this._activeId = drawingId;
        var model = renderer.model || renderer;
        var text = (model.content && model.content.plain) || '';
        this._textarea.value = text;
        this._textarea.style.display = 'block';
        this._textarea.style.left = (pixelPos.x + 8) + 'px';
        this._textarea.style.top = (pixelPos.y - 10) + 'px';
        this._textarea.focus();
        this._textarea.select();
    }

    // Commit the current edit
    _commit() {
        if (!this._activeId) return;
        var text = this._textarea.value;
        this._textarea.style.display = 'none';
        if (this._onCommit) this._onCommit(this._activeId, text);
        this._activeId = null;
    }

    // Cancel the current edit (no commit)
    _cancel() {
        this._textarea.style.display = 'none';
        this._activeId = null;
    }

    // Check if editor is active
    get isActive() { return this._activeId !== null; }

    get activeDrawingId() { return this._activeId; }

    // Clean up
    destroy() {
        if (this._textarea && this._textarea.parentNode) {
            this._textarea.parentNode.removeChild(this._textarea);
        }
        this._textarea = null;
        this._activeId = null;
    }
}

class AutoScrollController {
    constructor(engine) {
        this.engine = engine;
        this.active = false;
        this.raf = null;
    }

    check(pointerX, pointerY, chartState) {
        if (!chartState) return;
        var edgeZone = 30; // pixels
        var deltaBars = 0;
        
        if (pointerX < edgeZone) {
            deltaBars = -1;
        } else if (pointerX > (this.engine._canvasWidth || 800) - edgeZone) {
            deltaBars = 1;
        }

        if (deltaBars === 0) {
            this.stop();
            return;
        }

        if (!this.active) {
            this.active = true;
            var self = this;
            var fn = function() {
                if (!self.active) return;
                if (typeof chartState.scrollLogical === 'function') {
                    chartState.scrollLogical(deltaBars);
                } else if (window.bigChart) {
                    var lr = window.bigChart.timeScale().getVisibleLogicalRange();
                    if (lr) {
                        window.bigChart.timeScale().setVisibleLogicalRange({
                            from: lr.from + deltaBars,
                            to: lr.to + deltaBars
                        });
                    }
                }
                self.raf = requestAnimationFrame(fn);
            };
            this.raf = requestAnimationFrame(fn);
        }
    }

    stop() {
        this.active = false;
        if (this.raf) {
            cancelAnimationFrame(this.raf);
            this.raf = null;
        }
    }
}

class DrawingEngine {
    constructor(options) {
        // Core subsystems
        this.drawings = [];           // DrawingModel[]
        this.eventBus = new EventBus();
        this.selection = new SelectionManager(this.eventBus);
        this.undoManager = new UndoRedoManager({ maxStack: 100 });
        this.serializer = new Serializer();
        this.coordMapper = new CoordinateMapper();
        window.coordinateMapper = this.coordMapper;
        window.getChartCoordinateAPI = function() {
            return window.coordinateMapper || null;
        };

        // Counting for auto-naming
        this._nameCounters = {};

        // Autosave (Document 2 §10)
        this._autosaveTimer = null;
        this._dirty = false;
        this._autosaveDelay = options && options.autosaveDelay ? options.autosaveDelay : 1000;

        // Phase 2: Hit testing service
        this.hitTest = new HitTestService({
            anchorRadius: options && options.anchorRadius ? options.anchorRadius : 8,
            midpointRadius: options && options.midpointRadius ? options.midpointRadius : 8,
            edgeThreshold: options && options.edgeThreshold ? options.edgeThreshold : 6,
            bodyThreshold: options && options.bodyThreshold ? options.bodyThreshold : 9
        });

        // Phase 2: Snapping engine
        this.snapping = new SnappingEngine();

        // Phase 2: Render state
        this._needsRender = false;
        this._renderFrame = null;
        this._canvasWidth = 0;
        this._canvasHeight = 0;
        this._dpr = 1;

        // Phase 2: Auto-scroll controller
        this.autoScroll = new AutoScrollController(this);

        // Phase 2: Marquee state
        this._marqueeStart = null;
        this._marqueeEnd = null;
        this._marqueeActive = false;

        // Phase 3.8: Layer & Object System
        this.layerManager = new RenderLayerManager();
        this.objectManager = new ObjectManager(this);
        this.templateManager = new TemplateManager();
        // Phase 3.9: Rich Objects
        this.assetManager = new AssetManager();
        this.textEditor = new TextEditorManager({
            onCommit: function(drawingId, text) {
                // Update DrawingModel content when editing finishes
                var d = this.getDrawing(drawingId);
                if (!d) return;
                var model = d.model || d;
                model.content = { plain: text, rich: null, markdown: null };
                d.text = text;
                d.content = { plain: text, rich: null, markdown: null };
                model.updatedAt = new Date().toISOString();
                if (d !== model && d.syncToModel) d.syncToModel();
                this.markDirty();
                this.eventBus.emit('drawing:update', { id: drawingId, changes: { content: model.content } });
            }.bind(this)
        });
    }

    // Set the coordinate mapper functions from the chart
    setCoordMapper(mappers) {
        this.coordMapper.setMappers(mappers);
    }

    // Mark dirty for autosave (Document 2 §10)
    markDirty() {
        if (this._dirty) return;
        this._dirty = true;
        if (this._autosaveTimer) clearTimeout(this._autosaveTimer);
        this._autosaveTimer = setTimeout(() => {
            if (this._dirty) {
                this._dirty = false;
                this._autosave();
            }
        }, this._autosaveDelay);
    }

    _autosave() {
        this._doSave();
    }

    // Save immediately (flush autosave)
    saveNow() {
        if (this._autosaveTimer) clearTimeout(this._autosaveTimer);
        this._dirty = false;
        this._doSave();
    }

    // Phase 1.5: extract DrawingModel data from renderer+model array
    _doSave() {
        var models = [];
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            if (d && d.model) {
                // Sync renderer → model first
                if (d.syncToModel) d.syncToModel();
                models.push(d.model);
            } else if (d && d.toJSON) {
                models.push(d);
            }
        }
        this.serializer.save(models);
    }

    // Load from storage — returns DrawingModel[] for the caller to wrap in renderers
    load() {
        const loaded = this.serializer.load();
        this._rebuildNameCounters();
        // Per Document 2 §8: loading does NOT clear undo stack
        return loaded;
    }

    // Create a new drawing (Document 2 §7 lifecycle: preview → validate → finalized)
    // Returns both the DrawingModel and a renderer wrapper.
    createDrawing(type, points, options) {
        // Auto-generate name if not provided
        const existingNames = this.drawings
            .map(function(d) { return (d.model ? d.model.name : d.name) || ''; })
            .filter(function(n) { return n; });
        const name = DrawingModel.generateName(type, existingNames);

        const model = new DrawingModel({
            type: type,
            points: points,
            name: name,
            zIndex: this.drawings.length + 1,
            ...(options || {})
        });

        // Validate (Document 2 §7)
        const validation = model.validate();
        if (!validation.valid) {
            return { drawing: null, renderer: null, error: validation.message };
        }

        // Create a generic renderer wrapper
        var renderer = {
            model: model,
            // PHASE A: carry the canonical time into the renderer's coords, and
            // derive logical when only time was supplied by the caller.
            coords: points.map(function(p) {
                var lg = p.logical;
                if ((lg === undefined || lg === null) && p.time !== undefined && p.time !== null
                    && window.coordinateMapper && typeof window.coordinateMapper.timeToLogical === 'function') {
                    var r = window.coordinateMapper.timeToLogical(p.time);
                    if (r !== null && r !== undefined && !isNaN(r)) lg = r;
                }
                return { logical: lg, price: p.price, time: (p.time !== undefined ? p.time : null) };
            }),
            style: JSON.parse(JSON.stringify(model.style)),
            locked: !!model.locked,
            hidden: !!(model.visibility && model.visibility.hidden),
            draw: function(ctx, chartState, isSelected, isHovered) {
                var pixels = this.getPixels ? this.getPixels(chartState) : [];
                if (pixels.length === 0) return;
                ctx.strokeStyle = this.style.color || '#2962ff';
                ctx.lineWidth = this.style.width || 2;
                ctx.globalAlpha = this.style.opacity || 1;
                ctx.setLineDash(this.style.lineDash || []);
                for (var pi = 1; pi < pixels.length; pi++) {
                    ctx.beginPath();
                    ctx.moveTo(pixels[pi-1].x, pixels[pi-1].y);
                    ctx.lineTo(pixels[pi].x, pixels[pi].y);
                    ctx.stroke();
                }
                ctx.setLineDash([]);
            },
            getPixels: function(chartState) {
                if (!chartState || !this.coords) return [];
                return this.coords.map(function(c) { return chartState.coordToPixel ? chartState.coordToPixel(c) : null; }).filter(Boolean);
            },
            normalizeStyle: function() {},
            syncToModel: function() {
                if (this.model) {
                    // PHASE A: keep the canonical time anchor (BUG-001).
                    var cm = window.coordinateMapper;
                    this.model.points = (this.coords || []).map(function(p) {
                        var t = (p.time !== undefined && p.time !== null) ? p.time : null;
                        if (t === null && cm && typeof cm.logicalToTime === 'function') {
                            var r = cm.logicalToTime(p.logical);
                            if (r !== null && r !== undefined && !isNaN(r)) t = r;
                        }
                        return { time: t, price: p.price, logical: p.logical };
                    });
                    this.model.locked = !!this.locked;
                }
            }
        };

        // PHASE B (BUG-002): record CREATE on the undo stack.
        // Move/resize/delete/restyle/visibility/lock/clear were already
        // recorded (move & resize via engine.syncAndCapture on pointer-up);
        // creation was the one gap -- onNewDrawing() only truncated the redo
        // branch and pushed nothing, so Ctrl+Z after drawing did nothing and
        // could apply an unrelated older command instead.
        //
        // Uses the same FULL-LIST array snapshot semantics as 'delete'
        // (before = list without the new drawing, after = list with it), so
        // _applyCommandSnapshot restores either direction exactly.
        var _beforeCreate = this.drawings
            .map(function (d) { return d.model ? d.model.toJSON() : null; })
            .filter(Boolean);

        // Add to collection
        this.drawings.push(renderer);

        var _afterCreate = this.drawings
            .map(function (d) { return d.model ? d.model.toJSON() : null; })
            .filter(Boolean);

        // Update undo stack (truncates the redo branch), then record CREATE.
        this.undoManager.onNewDrawing();
        this.undoManager.pushCommand('create', _beforeCreate, _afterCreate);
        this.markDirty();

        // Emit event (Document 2 §7)
        this.eventBus.emit('drawing:create', {
            id: model.id,
            type: model.type,
            points: model.points
        });

        return { drawing: model, renderer: renderer, error: null };
    }

    // Delete a drawing
    deleteDrawing(drawingId) {
        const idx = this.drawings.findIndex(d => (d.model ? d.model.id : d.id) === drawingId);
        if (idx === -1) return false;
        const drawing = this.drawings[idx];
        if (drawing.locked) return false;

        // BUG-03 fix: snapshot was double-wrapped [[...]] and called d.toJSON() on
        // renderers which don't have that method. Now correctly captures model JSON
        // as a flat array matching _applyCommandSnapshot's expected format.
        var beforeSnapshot = this.drawings
            .map(function(d) { return d.model ? d.model.toJSON() : null; })
            .filter(Boolean);
        this.undoManager.pushCommand('delete', beforeSnapshot, []);

        this.drawings.splice(idx, 1);
        this.selection.onDrawingDeleted(drawingId);
        this.markDirty();

        this.eventBus.emit('drawing:delete', { id: drawingId });
        return true;
    }

    // Update a drawing's properties
    updateDrawing(drawingId, changes) {
        const drawing = this.drawings.find(d => (d.model ? d.model.id : d.id) === drawingId);
        if (!drawing) return false;
        const model = drawing.model || drawing;
        if (model.locked && changes.locked === undefined) return false;

        // Capture before state for undo
        const before = model.toJSON();
        model.update(changes);
        // Sync to renderer if model is embedded
        if (drawing !== model) {
            this._syncRendererFromModel(drawing);
        }
        this.undoManager.pushCommand('restyle', before, model.toJSON());
        this.markDirty();

        this.eventBus.emit('drawing:update', { id: drawingId, changes: changes });
        return true;
    }

    // Move a drawing (translate all points by delta)
    moveDrawing(drawingId, deltaBars, deltaPrice) {
        const drawing = this.drawings.find(d => (d.model ? d.model.id : d.id) === drawingId);
        if (!drawing) return false;
        const model = drawing.model || drawing;

        const before = model.toJSON();
        model.points = model.points.map(function(p) {
            return {
                logical: p.logical !== null ? p.logical + deltaBars : null,
                price: p.price !== null ? p.price + deltaPrice : null
            };
        });
        model.updatedAt = new Date().toISOString();

        // Sync to renderer
        if (drawing !== model) {
            this._syncRendererFromModel(drawing);
        }

        // For move, we batch the undo (multiple frames during drag)
        // The caller should call beginBatch/endBatch on the undoManager
        this.markDirty();

        this.eventBus.emit('drawing:move', {
            id: drawingId,
            deltaBars: deltaBars,
            deltaPrice: deltaPrice
        });
        return true;
    }

    // Duplicate a drawing
    duplicateDrawing(drawingId) {
        const drawing = this.drawings.find(d => (d.model ? d.model.id : d.id) === drawingId);
        if (!drawing) return null;
        const sourceModel = drawing.model || drawing;

        const existingNames = this.drawings
            .map(function(d) { return (d.model ? d.model.name : d.name) || ''; })
            .filter(function(n) { return n; });
        const name = DrawingModel.generateName(sourceModel.type, existingNames);

        const clonedModel = sourceModel.clone();
        clonedModel.id = _generateId();
        clonedModel.name = name;
        clonedModel.zIndex = this.drawings.length + 1;
        clonedModel.createdAt = new Date().toISOString();
        clonedModel.updatedAt = new Date().toISOString();

        // Create a new renderer from cloned model
        var Cls = drawing.constructor;
        var renderer = Object.create(Cls.prototype);
        renderer.coords = clonedModel.points.map(function(p) { return { logical: p.logical, price: p.price }; });
        renderer.style = JSON.parse(JSON.stringify(clonedModel.style));
        renderer.locked = !!clonedModel.locked;
        renderer.hidden = !!(clonedModel.visibility && clonedModel.visibility.hidden);
        renderer.model = clonedModel;
        renderer.currentPos = renderer.coords.length > 0 ? { x: 0, y: 0 } : null;
        if (renderer.normalizeStyle) renderer.normalizeStyle();

        this.drawings.push(renderer);
        this.undoManager.onNewDrawing();
        this.markDirty();

        this.eventBus.emit('drawing:duplicate', { sourceId: drawingId, newId: clonedModel.id });
        return renderer;
    }

    // Lock/unlock
    setLock(drawingId, locked) {
        const drawing = this.drawings.find(d => (d.model ? d.model.id : d.id) === drawingId);
        if (!drawing) return false;
        const model = drawing.model || drawing;

        const before = model.toJSON();
        model.locked = !!locked;
        model.updatedAt = new Date().toISOString();
        // Sync to renderer
        if (drawing !== model) {
            drawing.locked = model.locked;
        }
        this.undoManager.pushCommand(locked ? 'lock' : 'unlock', before, model.toJSON());
        this.markDirty();

        this.eventBus.emit(locked ? 'drawing:lock' : 'drawing:unlock', { id: drawingId });
        return true;
    }

    // Toggle visibility
    setVisibility(drawingId, hidden) {
        const drawing = this.drawings.find(d => (d.model ? d.model.id : d.id) === drawingId);
        if (!drawing) return false;
        const model = drawing.model || drawing;

        const before = model.toJSON();
        model.visibility = Object.assign({}, model.visibility, { hidden: !!hidden });
        model.updatedAt = new Date().toISOString();
        // Sync to renderer
        if (drawing !== model) {
            drawing.hidden = !!hidden;
        }
        this.undoManager.pushCommand('visibility', before, model.toJSON());
        this.markDirty();

        this.eventBus.emit('drawing:visibility', { id: drawingId, hidden: !!hidden });
        return true;
    }

    // Get a drawing by ID
    getDrawing(drawingId) {
        return this.drawings.find(d => (d.model ? d.model.id : d.id) === drawingId) || null;
    }

    // Get drawings by type
    getDrawingsByType(type) {
        return this.drawings.filter(function(d) { return (d.model ? d.model.type : d.type) === type; });
    }

    // Get all non-hidden drawings for rendering
    getVisibleDrawings() {
        return this.drawings.filter(function(d) {
            var model = d.model || d;
            var vis = model.visibility || {};
            return !vis.hidden;
        });
    }

    // Check if any drawing is locked
    hasLockedDrawings() {
        return this.drawings.some(function(d) { return d.model ? d.model.locked : d.locked; });
    }

    // Lock/unlock all drawings
    setLockAll(locked) {
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            var model = d.model || d;
            var wasLocked = model.locked;
            if (wasLocked !== !!locked) {
                model.locked = !!locked;
                model.updatedAt = new Date().toISOString();
                if (d !== model) d.locked = !!locked;
                this.eventBus.emit(locked ? 'drawing:lock' : 'drawing:unlock', { id: model.id });
            }
        }
        this.markDirty();
    }

    // Hide/show all drawings
    setVisibilityAll(hidden) {
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            var model = d.model || d;
            var vis = model.visibility || {};
            var wasHidden = !!vis.hidden;
            if (wasHidden !== !!hidden) {
                model.visibility = Object.assign({}, vis, { hidden: !!hidden });
                model.updatedAt = new Date().toISOString();
                if (d !== model) {
                    d.hidden = !!hidden;
                }
                this.eventBus.emit('drawing:visibility', { id: model.id, hidden: !!hidden });
            }
        }
        this.markDirty();
    }

    // Group operations
    groupDrawings(drawingIds, groupId) {
        const gid = groupId || 'g_' + Date.now().toString(36);
        for (var i = 0; i < drawingIds.length; i++) {
            var id = drawingIds[i];
            var d = this.drawings.find(function(dr) { return (dr.model ? dr.model.id : dr.id) === id; });
            if (d) {
                var model = d.model || d;
                model.groupId = gid;
                model.updatedAt = new Date().toISOString();
                if (d !== model) d.groupId = gid;
            }
        }
        this.eventBus.emit('drawing:group', { groupId: gid, drawingIds: drawingIds });
        this.markDirty();
    }

    ungroupDrawings(groupId) {
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            var model = d.model || d;
            if (model.groupId === groupId) {
                model.groupId = null;
                model.updatedAt = new Date().toISOString();
                if (d !== model) d.groupId = null;
            }
        }
        this.eventBus.emit('drawing:ungroup', { groupId: groupId });
        this.markDirty();
    }

    // Undo/Redo integration
    undo() {
        const cmd = this.undoManager.undo();
        if (!cmd) return false;
        // BUG-04 fix: _applyCommandSnapshot handles both Array (multi-drawing
        // snapshots from delete/clearAll) and Object (single-model snapshots
        // from restyle/lock/visibility). Route accordingly.
        this._applySnapshot(cmd.before);
        this._cleanupSelection();
        this.eventBus.emit('drawing:select', { ids: this.selection.getSelected() });
        this.undoManager.finishRestore();
        this.markDirty();
        return true;
    }

    redo() {
        const cmd = this.undoManager.redo();
        if (!cmd) return false;
        this._applySnapshot(cmd.after);
        this._cleanupSelection();
        this.eventBus.emit('drawing:select', { ids: this.selection.getSelected() });
        this.undoManager.finishRestore();
        this.markDirty();
        return true;
    }

    // Route snapshot to correct apply method based on type (BUG-04 fix)
    _applySnapshot(snapshot) {
        if (!snapshot) return;
        if (Array.isArray(snapshot)) {
            // Multi-drawing state (delete, clearAll) — restore full drawing list
            this._applyCommandSnapshot(snapshot);
        } else if (snapshot && typeof snapshot === 'object' && snapshot.id) {
            // Single-model snapshot (restyle, lock, visibility)
            this._applySingleModelSnapshot(snapshot);
        }
    }

    // Apply a single-model JSON snapshot (for restyle/lock/visibility undo)
    _applySingleModelSnapshot(json) {
        var model = DrawingModel.fromJSON(json);
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            if (d.model && d.model.id === model.id) {
                d.model = model;
                this._syncRendererFromModel(d);
                return;
            }
        }
    }

    _cleanupSelection() {
        var validIds = {};
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            if (d && d.model && d.model.id) validIds[d.model.id] = true;
        }
        var toRemove = this.selection.getSelected().filter(function(id) { return !validIds[id]; });
        for (var r = 0; r < toRemove.length; r++) {
            this.selection.onDrawingDeleted(toRemove[r]);
        }
    }

    // Phase 1.5: Reconciling undo/redo with hybrid renderer+model array.
    // The snapshot contains DrawingModel JSON. We must update in-place
    // so that renderer objects (which own the draw() methods) are preserved.
    _applyCommandSnapshot(snapshot) {
        if (!snapshot) return;
        if (!Array.isArray(snapshot)) return;

        var models = snapshot.map(function(s) { return DrawingModel.fromJSON(s); });

        // Build a lookup by id for the renderers that have models
        var rendererById = {};
        for (var i = 0; i < this.drawings.length; i++) {
            var r = this.drawings[i];
            if (r && r.model && r.model.id) {
                rendererById[r.model.id] = r;
            }
        }

        var newDrawings = [];
        for (var m = 0; m < models.length; m++) {
            var model = models[m];
            var existing = rendererById[model.id];
            if (existing) {
                // Update existing renderer's model and sync data to it
                existing.model = model;
                this._syncRendererFromModel(existing);
                newDrawings.push(existing);
            } else {
                // Need to create a renderer wrapper — use a generic base renderer
                var dummy = {
                    model: model,
                    coords: model.points.map(function(p) { return { logical: p.logical, price: p.price }; }),
                    style: JSON.parse(JSON.stringify(model.style)),
                    locked: !!model.locked,
                    hidden: !!(model.visibility && model.visibility.hidden),
                    draw: function(ctx, chartState, isSelected, isHovered) {
                        // Minimal fallback render: just draw handles
                        var pixels = this.getPixels ? this.getPixels(chartState) : [];
                        if (pixels.length === 0) return;
                        ctx.strokeStyle = this.style.color || '#2962ff';
                        ctx.lineWidth = this.style.width || 2;
                        ctx.globalAlpha = this.style.opacity || 1;
                        for (var pi = 1; pi < pixels.length; pi++) {
                            ctx.beginPath();
                            ctx.moveTo(pixels[pi-1].x, pixels[pi-1].y);
                            ctx.lineTo(pixels[pi].x, pixels[pi].y);
                            ctx.stroke();
                        }
                    },
                    getPixels: function(chartState) {
                        if (!chartState || !this.coords) return [];
                        return this.coords.map(function(c) { return chartState.coordToPixel ? chartState.coordToPixel(c) : null; }).filter(Boolean);
                    },
                    normalizeStyle: function() {},
                    syncToModel: function() {}
                };
                // Try to look up the proper class
                var tm = window.toolManager;
                var className = this._typeToClassName(model.type);
                if (tm && window.DrawingClasses && className && window.DrawingClasses[className]) {
                    var Cls = window.DrawingClasses[className];
                    var r2 = Object.create(Cls.prototype);
                    r2.coords = model.points.map(function(p) { return { logical: p.logical, price: p.price }; });
                    r2.style = JSON.parse(JSON.stringify(model.style));
                    r2.locked = !!model.locked;
                    r2.hidden = !!(model.visibility && model.visibility.hidden);
                    r2.model = model;
                    r2.currentPos = r2.coords.length > 0 ? { x: 0, y: 0 } : null;
                    r2.normalizeStyle();
                    newDrawings.push(r2);
                } else {
                    newDrawings.push(dummy);
                }
            }
        }

        // Mutate in-place so aliased references (ToolManager.drawings) stay valid
        this.drawings.length = 0;
        for (var di = 0; di < newDrawings.length; di++) {
            this.drawings.push(newDrawings[di]);
        }
        this._rebuildNameCounters();
    }

    // Sync model data back to a renderer object
    _syncRendererFromModel(renderer) {
        if (!renderer || !renderer.model) return;
        renderer.coords = renderer.model.points.map(function(p) {
            var t = p.logical !== undefined ? p.logical : (p.logicalIndex != null ? p.logicalIndex : p.time);
            // Backward compat: if time > 100M it's a Unix timestamp → convert using visible range
            if (t > 100000000) {
                var api = typeof window.getChartCoordinateAPI === 'function' ? window.getChartCoordinateAPI() : null;
                if (api && typeof api._timeToLogical === 'function') {
                    t = api._timeToLogical(t);
                }
            }
            return { logical: t, price: p.price };
        });
        renderer.style = JSON.parse(JSON.stringify(renderer.model.style));
        renderer.locked = !!renderer.model.locked;
        renderer.hidden = !!(renderer.model.visibility || {}).hidden;
        if (renderer.normalizeStyle) renderer.normalizeStyle();
        if (typeof renderer._invalidateCache === 'function') renderer._invalidateCache();
    }

    // Map DrawingModel type back to legacy class name
    _typeToClassName(type) {
        var map = {
            trendline: 'TrendLine', ray: 'Ray', extended: 'ExtendedLine',
            infoline: 'InfoLine', trend_angle: 'TrendAngle',
            horizontal_line: 'HorizontalLine', vertical_line: 'VerticalLine',
            horizontal_ray: 'HorizontalRay', cross_line: 'CrossLine',
            channel: 'ParallelChannel', flat_top_channel: 'FlatTopChannel',
            flat_bottom_channel: 'FlatBottomChannel', disjoint_channel: 'DisjointChannel',
            regression_trend: 'RegressionTrend', pitchfork: 'Pitchfork',
            schiff_pitchfork: 'SchiffPitchfork',
            modified_schiff_pitchfork: 'ModifiedSchiffPitchfork',
            inside_pitchfork: 'InsidePitchfork',
            rectangle: 'Rectangle', circle: 'Circle', ellipse: 'Ellipse',
            triangle: 'TriangleShape', path: 'PathDrawing',
            brush: 'BrushDrawing', highlighter: 'HighlighterDrawing',
            text: 'TextDrawing', price_label: 'PriceLabel',
            arrow_marker: 'ArrowMarker',
            fib_retracement: 'FibRetracement', fib_trend_ext: 'FibExtension',
            fib_channel: 'FibChannel', fib_fan: 'FibFan',
            fib_time_zone: 'FibTimeZone', fib_circles: 'FibCircles',
            fib_arcs: 'FibArcs', fib_speed_resistance: 'FibSpeedResistance',
            fib_spiral: 'FibSpiral', fib_wedge: 'FibWedge',
            pitchfan: 'Pitchfan',
            gann_box: 'GannBox', gann_square: 'GannSquare',
            gann_square_fixed: 'GannSquareFixed',
            gann_fan: 'GannFan', long_position: 'LongPosition',
            short_position: 'ShortPosition', price_range: 'PriceRange',
            date_range: 'DateRange', measure: 'Measure',
            // Elliott Wave (Phase 3.6)
            elliott_impulse: 'ImpulseWave', elliott_correction: 'CorrectiveWave',
            elliott_triangle: 'ElliottTriangle', elliott_double_combo: 'ElliottDoubleCombo',
            elliott_triple_combo: 'ElliottTripleCombo', elliott_flat: 'ElliottFlat',
            elliott_zigzag: 'ElliottZigZag', elliott_combination: 'ElliottCombination',
            // Pattern Tools (Phase 3.7)
            gartley: 'Gartley', butterfly: 'Butterfly', bat: 'Bat', crab: 'Crab',
            deep_crab: 'DeepCrab', shark: 'Shark', cypher: 'Cypher', abcd: 'Abcd',
            head_and_shoulders: 'HeadAndShoulders', inverse_head_and_shoulders: 'InverseHeadAndShoulders',
            double_top: 'DoubleTop', double_bottom: 'DoubleBottom',
            triple_top: 'TripleTop', triple_bottom: 'TripleBottom',
            ascending_triangle: 'AscendingTriangle', descending_triangle: 'DescendingTriangle',
            symmetrical_triangle: 'SymmetricalTriangle', expanding_triangle: 'ExpandingTriangle',
            rising_wedge: 'RisingWedge', falling_wedge: 'FallingWedge',
            ascending_channel: 'AscendingChannel', descending_channel: 'DescendingChannel',
            // Phase 3.9: Rich Objects
            text_note: 'TextNote', rich_anchored_text: 'AnchoredText', rich_callout: 'Callout', balloon: 'Balloon', arrow_label: 'ArrowLabel',
            emoji: 'EmojiDrawing', icon: 'IconDrawing', symbol: 'SymbolDrawing',
            image: 'ImageDrawing', watermark: 'WatermarkDrawing', logo: 'LogoDrawing',
            sticker_buy: 'StickerBuy', sticker_sell: 'StickerSell', sticker_long: 'StickerLong', sticker_short: 'StickerShort',
            sticker_target: 'StickerTarget', sticker_stop: 'StickerStop', sticker_star: 'StickerStar', sticker_pin: 'StickerPin',
            sticker_check: 'StickerCheck', sticker_warning: 'StickerWarning'
        };
        return map[type] || null;
    }

    _rebuildNameCounters() {
        this._nameCounters = {};
        for (const d of this.drawings) {
            if (d.name) {
                this._nameCounters[d.type] = (this._nameCounters[d.type] || 0) + 1;
            }
        }
    }

    // Clear all drawings
    clearAll() {
        // BUG-10 / BUG-18 fix: d.toJSON() called on renderers (not models);
        // also the before snapshot was never pushed to undoManager.
        var before = this.drawings
            .map(function(d) { return d.model ? d.model.toJSON() : null; })
            .filter(Boolean);
        this.drawings = [];
        this.selection.clear();
        // Push undo-able clear command so the user can restore with Ctrl+Z
        if (before.length > 0) {
            this.undoManager.pushCommand('clear', before, []);
        }
        this.markDirty();
    }

    // Get the number of drawings
    get count() { return this.drawings.length; }

    // ---- Phase 1.5 Integration: Legacy renderer bridge ----

    // Map legacy class names to DrawingModel types
    get classNameToType() {
        return {
            TrendLine: 'trendline',
            Ray: 'ray',
            ExtendedLine: 'extended',
            InfoLine: 'infoline',
            TrendAngle: 'trend_angle',
            HorizontalLine: 'horizontal_line',
            VerticalLine: 'vertical_line',
            HorizontalRay: 'horizontal_ray',
            CrossLine: 'cross_line',
            ParallelChannel: 'channel',
            FlatTopChannel: 'flat_top_channel',
            FlatBottomChannel: 'flat_bottom_channel',
            DisjointChannel: 'disjoint_channel',
            RegressionTrend: 'regression_trend',
            Pitchfork: 'pitchfork',
            SchiffPitchfork: 'schiff_pitchfork',
            ModifiedSchiffPitchfork: 'modified_schiff_pitchfork',
            InsidePitchfork: 'inside_pitchfork',
            Rectangle: 'rectangle',
            Circle: 'circle',
            Ellipse: 'ellipse',
            TriangleShape: 'triangle',
            PathDrawing: 'path',
            BrushDrawing: 'brush',
            HighlighterDrawing: 'highlighter',
            TextDrawing: 'text',
            PriceLabel: 'price_label',
            ArrowMarker: 'arrow_marker',
            FibRetracement: 'fib_retracement',
            FibExtension: 'fib_trend_ext',
            FibChannel: 'fib_channel',
            FibFan: 'fib_fan',
            FibTimeZone: 'fib_time_zone',
            FibCircles: 'fib_circles',
            FibArcs: 'fib_arcs',
            FibSpeedResistance: 'fib_speed_resistance',
            FibSpiral: 'fib_spiral',
            FibWedge: 'fib_wedge',
            Pitchfan: 'pitchfan',
            GannBox: 'gann_box',
            GannSquare: 'gann_square',
            GannSquareFixed: 'gann_square_fixed',
            GannFan: 'gann_fan',
            // Elliott Wave (Phase 3.6)
            ImpulseWave: 'elliott_impulse',
            CorrectiveWave: 'elliott_correction',
            ElliottTriangle: 'elliott_triangle',
            ElliottDoubleCombo: 'elliott_double_combo',
            ElliottTripleCombo: 'elliott_triple_combo',
            ElliottFlat: 'elliott_flat',
            ElliottZigZag: 'elliott_zigzag',
            ElliottCombination: 'elliott_combination',
            // Pattern Tools (Phase 3.7)
            Gartley: 'gartley', Butterfly: 'butterfly', Bat: 'bat', Crab: 'crab',
            DeepCrab: 'deep_crab', Shark: 'shark', Cypher: 'cypher', Abcd: 'abcd',
            HeadAndShoulders: 'head_and_shoulders', InverseHeadAndShoulders: 'inverse_head_and_shoulders',
            DoubleTop: 'double_top', DoubleBottom: 'double_bottom',
            TripleTop: 'triple_top', TripleBottom: 'triple_bottom',
            AscendingTriangle: 'ascending_triangle', DescendingTriangle: 'descending_triangle',
            SymmetricalTriangle: 'symmetrical_triangle', ExpandingTriangle: 'expanding_triangle',
            RisingWedge: 'rising_wedge', FallingWedge: 'falling_wedge',
            AscendingChannel: 'ascending_channel', DescendingChannel: 'descending_channel',
            // Phase 3.9: Rich Objects
            TextNote: 'text_note', AnchoredText: 'rich_anchored_text', Callout: 'rich_callout', Balloon: 'balloon', ArrowLabel: 'arrow_label',
            EmojiDrawing: 'emoji', IconDrawing: 'icon', SymbolDrawing: 'symbol',
            ImageDrawing: 'image', WatermarkDrawing: 'watermark', LogoDrawing: 'logo',
            StickerBuy: 'sticker_buy', StickerSell: 'sticker_sell', StickerLong: 'sticker_long', StickerShort: 'sticker_short',
            StickerTarget: 'sticker_target', StickerStop: 'sticker_stop', StickerStar: 'sticker_star', StickerPin: 'sticker_pin',
            StickerCheck: 'sticker_check', StickerWarning: 'sticker_warning',
            LongPosition: 'long_position',
            ShortPosition: 'short_position',
            PriceRange: 'price_range',
            DateRange: 'date_range',
            Measure: 'measure',
            TwoPointDrawing: 'trendline'
        };
    }

    // Create a DrawingModel from a legacy renderer's current data
    createModelFromRenderer(renderer) {
        const cname = renderer.constructor && renderer.constructor.name;
        const type = this.classNameToType[cname] || 'trendline';
        const existingNames = this.drawings
            .filter(function(d) { return d !== renderer; })
            .map(function(d) { return (d.model && d.model.name) || ''; })
            .filter(function(n) { return n; });
        const name = DrawingModel.generateName(type, existingNames);
        var style = JSON.parse(JSON.stringify(renderer.style || {}));
        // Ensure lineStyle and thickness for spec compliance
        if (style.lineDash && !style.lineStyle) {
            style.lineStyle = style.lineDash.length > 0 ? (style.lineDash[0] >= 4 ? 'dashed' : 'dotted') : 'solid';
        }
        var hidden = !!renderer.hidden;
        var modelData = {
            type: type,
            points: (renderer.coords || []).map(function(p) { return { logical: p.logical, price: p.price }; }),
            style: style,
            text: renderer.text ? { content: renderer.text || '', fontSize: style.fontSize || 12, color: style.color || '#FFFFFF', bold: false, italic: false, align: 'center' } : undefined,
            locked: !!renderer.locked,
            name: name,
            zIndex: this.drawings.length + 1,
            visibility: { timeframes: 'all', hidden: !!hidden }
        };
        if (renderer.content !== undefined) {
            modelData.content = renderer.content;
        }
        if (renderer.rotation !== undefined) {
            modelData.rotation = renderer.rotation;
        }
        return new DrawingModel(modelData);
    }

    // Finalize a legacy renderer: create model, link, add to collection, emit events
    finalizeDrawing(renderer) {
        if (!renderer) return { drawing: null, error: 'No renderer provided' };

        const model = this.createModelFromRenderer(renderer);
        const validation = model.validate();
        if (!validation.valid) {
            return { drawing: null, error: validation.message };
        }

        renderer.model = model;

        // PHASE B (BUG-002, corrected): finalizeDrawing -- NOT createDrawing --
        // is the path every mouse-driven tool actually goes through
        // (drawings.js finishDrawing() -> engine.finalizeDrawing()). The
        // earlier create-undo fix targeted createDrawing(), a separate,
        // rarely-used programmatic path (paste/template-apply); it never
        // covered interactive drawing at all. A real-browser QA pass on
        // drag-based tools (rectangle) caught this: undo stack stayed at 0
        // immediately after drawing. Same full-list snapshot semantics as
        // delete/create elsewhere.
        const _beforeFinalize = this.drawings
            .map(function (d) { return d.model ? d.model.toJSON() : null; })
            .filter(Boolean);

        this.drawings.push(renderer);

        // Lifecycle finish hook (e.g., PathDrawing marks itself done on finalize)
        if (typeof renderer.finish === 'function') {
            renderer.finish();
        }

        const _afterFinalize = this.drawings
            .map(function (d) { return d.model ? d.model.toJSON() : null; })
            .filter(Boolean);

        this.undoManager.onNewDrawing();
        this.undoManager.pushCommand('create', _beforeFinalize, _afterFinalize);
        this.markDirty();

        this.eventBus.emit('drawing:create', {
            id: model.id,
            type: model.type,
            points: model.points
        });

        return { drawing: model, error: null };
    }

    // Sync a legacy renderer's data back to its model
    syncModel(renderer) {
        if (!renderer || !renderer.model) return false;
        const model = renderer.model;
        if (renderer.coords) {
            // PHASE A (BUG-001): the model is canonical and must carry `time`.
            // This is the PRIMARY renderer->model path (the inline version in
            // drawings.js is only a fallback), so dropping time here would have
            // silently reverted every drawing to bar-index anchoring on save.
            model.points = renderer.coords.map(p => {
                var t = (p.time !== undefined && p.time !== null) ? p.time : null;
                if (t === null && typeof this.coordinateMapper === 'object' && this.coordinateMapper
                    && typeof this.coordinateMapper.logicalToTime === 'function') {
                    var r = this.coordinateMapper.logicalToTime(p.logical);
                    if (r !== null && r !== undefined && !isNaN(r)) t = r;
                } else if (t === null && window.coordinateMapper
                           && typeof window.coordinateMapper.logicalToTime === 'function') {
                    var r2 = window.coordinateMapper.logicalToTime(p.logical);
                    if (r2 !== null && r2 !== undefined && !isNaN(r2)) t = r2;
                }
                return { time: t, price: p.price, logical: p.logical };
            });
        }
        if (renderer.style) {
            Object.assign(model.style, renderer.style);
        }
        if (renderer.text !== undefined) {
            model.content = { plain: renderer.text, rich: null, markdown: null };
            model.text = { content: renderer.text, fontSize: model.style.fontSize || 12, color: model.style.color || '#FFFFFF', bold: false, italic: false, align: 'center' };
        }
        if (renderer.content !== undefined) {
            model.content = renderer.content;
        }
        if (renderer.rotation !== undefined) {
            model.rotation = renderer.rotation;
        }
        model.locked = !!renderer.locked;
        var hiddenVal = !!renderer.hidden;
        if (!model.visibility) model.visibility = { timeframes: 'all', hidden: false };
        model.visibility.hidden = !!hiddenVal;
        model.updatedAt = new Date().toISOString();
        return true;
    }

    // Sync renderer → model and push an undo command
    syncAndCapture(renderer, commandType) {
        if (!renderer || !renderer.model) return false;
        const before = renderer.model.toJSON();
        this.syncModel(renderer);
        const after = renderer.model.toJSON();
        this.undoManager.pushCommand(commandType || 'update', before, after);
        this.markDirty();
        this.eventBus.emit('drawing:update', { id: renderer.model.id, changes: { points: renderer.model.points } });
        return true;
    }

    // ===== Phase 2: Rendering Pipeline (Document 2 §3) =====

    // Set canvas dimensions (call on resize)
    setCanvas(canvas, dpr) {
        this._canvasWidth = canvas.width / (dpr || 1);
        this._canvasHeight = canvas.height / (dpr || 1);
        this._dpr = dpr || 1;
    }

    // Request a render on the next frame
    requestRender() {
        if (this._needsRender) return;
        this._needsRender = true;
        var self = this;
        if (this._renderFrame) cancelAnimationFrame(this._renderFrame);
        this._renderFrame = requestAnimationFrame(function() {
            self._needsRender = false;
            self._renderFrame = null;
        });
    }

    // Main render: sort by zIndex, filter visible, draw in layer order
    // NOTE: caller (ToolManager.redraw) is responsible for DPR scaling.
    // This method does NOT scale — it renders in the caller's transform space.
    render(ctx, chartState, selectedId, hoveredId) {
        if (!ctx || !chartState) return;

        // E01 fix: DPR scaling was applied here AND in ToolManager.redraw(),
        // causing every drawing to render at DPR² thickness. Removed from here.
        ctx.save();

        // 1. Filter visible drawings
        var visible = this._getVisibleDrawings(chartState);

        // 2. Sort by layer then zIndex using RenderLayerManager (Phase 3.8)
        var sorted = this.layerManager.sortDrawings(visible);

        // BUG-13 fix: collect the full selected set from SelectionManager,
        // not just the single selectedId passed from ToolManager. This makes
        // multi-selection via marquee render all selected drawings highlighted.
        var selectedSet = {};
        if (selectedId) selectedSet[selectedId] = true;
        var selIds = this.selection.getSelected();
        for (var si2 = 0; si2 < selIds.length; si2++) selectedSet[selIds[si2]] = true;

        // 3. Draw each drawing in layer + z-order with candle overlay support
        var belowDrawings = [];
        var aboveDrawings = [];
        for (var i = 0; i < sorted.length; i++) {
            var d = sorted[i];
            var isBelow = (d instanceof (window.EmojiDrawing || Object)) || 
                          (d.getObjectDef && d.getObjectDef() && d.getObjectDef().geometry === 3) ||
                          (d.model && (d.model.layer === 'drawings_below' || d.model.layer === 'background')) ||
                          (d.style && d.style.behindCandles);
            if (isBelow) {
                belowDrawings.push(d);
            } else {
                aboveDrawings.push(d);
            }
        }

        // Draw below-candle drawings (e.g. Emojis, Watermarks)
        for (var i = 0; i < belowDrawings.length; i++) {
            var d = belowDrawings[i];
            var dId = d.model ? d.model.id : null;
            var isSelected = !!(dId && selectedSet[dId]);
            var isHovered = !!(dId && dId === hoveredId);
            if (d.draw) {
                ctx.save();
                d.draw(ctx, chartState, isSelected, isHovered);
                ctx.restore();
            }
        }

        // Render candle overlay over below drawings so candles are drawn on top of emojis
        if (belowDrawings.length > 0 && typeof window.drawVisibleCandlesOverlay === 'function') {
            window.drawVisibleCandlesOverlay(ctx, chartState);
        }

        // Draw standard / above-candle drawings
        for (var i = 0; i < aboveDrawings.length; i++) {
            var d = aboveDrawings[i];
            var dId = d.model ? d.model.id : null;
            var isSelected = !!(dId && selectedSet[dId]);
            var isHovered = !!(dId && dId === hoveredId);
            if (d.draw) {
                ctx.save();
                d.draw(ctx, chartState, isSelected, isHovered);
                ctx.restore();
            }
        }

        // 4. Post-render pass: selection handles (always on top, for all selected drawings)
        var selDrawingIds = Object.keys(selectedSet);
        for (var si = 0; si < selDrawingIds.length; si++) {
            var selId = selDrawingIds[si];
            var selDrawing = null;
            for (var sj = 0; sj < sorted.length; sj++) {
                if (sorted[sj].model && sorted[sj].model.id === selId) { selDrawing = sorted[sj]; break; }
            }
            if (selDrawing && selDrawing.drawHandles) {
                ctx.save();
                var pixels = selDrawing.getPixels ? selDrawing.getPixels(chartState) : [];
                selDrawing.drawHandles(ctx, pixels, true, chartState);
                ctx.restore();
            }
        }

        ctx.restore();
    }

    // Get drawings visible in the current viewport (viewport culling)
    _getVisibleDrawings(chartState) {
        var result = [];
        var cw = this._canvasWidth;
        var ch = this._canvasHeight;
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            if (d.hidden) continue;

            // Use GeometryUtils for viewport culling when possible
            if (d.getPixels && chartState.coordToPixel) {
                var pixels = d.getPixels(chartState);
                if (!pixels || pixels.length === 0) { result.push(d); continue; }

                // For infinite geometry (Ray, ExtendedLine, etc.), check line-canvas intersection
                if (pixels.length >= 2 && d.constructor.name !== 'TrendLine' && d.constructor.name !== 'InfoLine') {
                    var p1 = pixels[0], p2 = pixels[1];
                    if (p1 && p2 && GeometryUtils.lineCanvasIntersection) {
                        var inter = GeometryUtils.lineCanvasIntersection(p1, p2, cw, ch);
                        if (inter && inter.length >= 1) { result.push(d); continue; }
                        if (!inter) { result.push(d); continue; }
                    } else {
                        result.push(d); continue;
                    }
                }

                // For finite drawings, check if any pixel is on screen
                var onScreen = false;
                for (var pi = 0; pi < pixels.length; pi++) {
                    var p = pixels[pi];
                    if (p && p.x >= -cw && p.x <= cw * 2 && p.y >= -ch && p.y <= ch * 2) {
                        onScreen = true; break;
                    }
                }
                if (onScreen) result.push(d);
            } else {
                result.push(d);
            }
        }
        return result;
    }

    // ===== Phase 2: Marquee Selection (Document 2 §5, Document 3 §6) =====

    // Start marquee at pixel position
    startMarquee(pos) {
        this._marqueeStart = { x: pos.x, y: pos.y };
        this._marqueeEnd = { x: pos.x, y: pos.y };
        this._marqueeActive = true;
    }

    // Update marquee during drag
    updateMarquee(pos) {
        if (!this._marqueeActive) return;
        this._marqueeEnd = { x: pos.x, y: pos.y };
    }

    // End marquee: select drawings whose bounding box intersects the marquee rect
    endMarquee(chartState) {
        if (!this._marqueeActive) return;
        this._marqueeActive = false;
        if (!this._marqueeStart || !this._marqueeEnd) return;

        var marquee = {
            left: Math.min(this._marqueeStart.x, this._marqueeEnd.x),
            top: Math.min(this._marqueeStart.y, this._marqueeEnd.y),
            right: Math.max(this._marqueeStart.x, this._marqueeEnd.x),
            bottom: Math.max(this._marqueeStart.y, this._marqueeEnd.y)
        };

        // Ignore tiny clicks (less than 5px drag)
        if (marquee.right - marquee.left < 5 && marquee.bottom - marquee.top < 5) {
            this._marqueeStart = null;
            this._marqueeEnd = null;
            return;
        }

        var selected = [];
        for (var i = 0; i < this.drawings.length; i++) {
            var d = this.drawings[i];
            if (d.hidden) continue;
            if (d.locked) continue;
            var pixels = d.getPixels ? d.getPixels(chartState) : [];
            if (pixels.length === 0) continue;
            var box = GeometryUtils.boundingBox(pixels);
            if (box && GeometryUtils.boxIntersects(marquee, box)) {
                var id = d.model ? d.model.id : null;
                if (id) selected.push(id);
            }
        }

        if (selected.length > 0) {
            this.selection.setSelection(selected);
            this.eventBus.emit('drawing:select', { ids: selected });
        } else {
            this.selection.clear();
        }

        this._marqueeStart = null;
        this._marqueeEnd = null;
    }

    // Get the current marquee rectangle for rendering (or null if inactive)
    getMarqueeRect() {
        if (!this._marqueeActive || !this._marqueeStart || !this._marqueeEnd) return null;
        return {
            left: Math.min(this._marqueeStart.x, this._marqueeEnd.x),
            top: Math.min(this._marqueeStart.y, this._marqueeEnd.y),
            right: Math.max(this._marqueeStart.x, this._marqueeEnd.x),
            bottom: Math.max(this._marqueeStart.y, this._marqueeEnd.y)
        };
    }

    // ===== Phase 2: Auto-Scroll (Document 1 §0, Document 3 §7) =====

    // Check if pointer is near viewport edge and start auto-scroll
    checkAutoScroll(pointerX, pointerY, chartApi) {
        this.autoScroll.check(pointerX, pointerY, chartApi);
    }

    stopAutoScroll() {
        this.autoScroll.stop();
    }

    // ===== Phase 2: Shift Constraint (Document 1 §2.1, Document 3 §11) =====

    // Apply shift constraint during placement/drag
    // shiftKey: whether shift is held
    // mode: 'placement' | 'drag'
    applyConstraint(point, origin, shiftKey, mode) {
        if (!shiftKey) return point;
        if (mode === 'placement') {
            // Constrain to 45° angles during placement
            return GeometryUtils.constrainToAngle(point, origin, [0, 45, 90, 135, 180, 225, 270, 315]);
        } else {
            // During drag, prefer horizontal or vertical based on dominant axis
            var dx = Math.abs(point.x - origin.x);
            var dy = Math.abs(point.y - origin.y);
            if (dx > dy) {
                return GeometryUtils.constrainHorizontal(point, origin);
            } else {
                return GeometryUtils.constrainVertical(point, origin);
            }
        }
    }
}

// =============================================================================
// Module exports
// =============================================================================

window.DrawingEngine = DrawingEngine;
window.DrawingModel = DrawingModel;
window.EventBus = EventBus;
window.SelectionManager = SelectionManager;
window.UndoRedoManager = UndoRedoManager;
window.Serializer = Serializer;
window.CoordinateMapper = CoordinateMapper;
window.GeometryUtils = GeometryUtils;
window.SnappingEngine = SnappingEngine;
window.HitTestService = HitTestService;
// Phase 3.8: Layer & Object System
window.Layer = Layer;
window.RenderLayerManager = RenderLayerManager;
window.ObjectManager = ObjectManager;
window.BaseCommand = BaseCommand;
window.BringToFrontCommand = BringToFrontCommand;
window.SendToBackCommand = SendToBackCommand;
window.SetLayerCommand = SetLayerCommand;
window.ToggleLockCommand = ToggleLockCommand;
window.ToggleVisibilityCommand = ToggleVisibilityCommand;
window.GroupCommand = GroupCommand;
window.UngroupCommand = UngroupCommand;
window.DeleteCommand = DeleteCommand;
window.DuplicateCommand = DuplicateCommand;
window.DrawingTemplate = DrawingTemplate;
window.TemplateManager = TemplateManager;
// Phase 3.9: Rich Objects
window.AssetManager = AssetManager;
window.TextEditorManager = TextEditorManager;

console.log("drawing-core.js loaded successfully.");

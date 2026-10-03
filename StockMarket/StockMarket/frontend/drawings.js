// drawings.js
console.log("Loading drawings.js...");

// Polyfill roundRect for older browsers (Chrome <99, Safari <15.4)
if (!CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function(x, y, w, h, r) {
    if (r > w/2) r = w/2;
    if (r > h/2) r = h/2;
    this.moveTo(x + r, y);
    this.arcTo(x + w, y, x + w, y + h, r);
    this.arcTo(x + w, y + h, x, y + h, r);
    this.arcTo(x, y + h, x, y, r);
    this.arcTo(x, y, x + w, y, r);
    return this;
  };
}

try {

    // =========================================================================
    // PHASE A — model<->renderer coordinate bridge (BUG-001)
    // =========================================================================
    // Tool classes work in { logical, price } for rendering, which is correct:
    // logical is a live screen-space value. The MODEL, however, is canonical
    // and must carry { time, price }. These two helpers are the only places
    // that conversion happens, so all 108 tools inherit correct anchoring
    // without any of them being modified individually.
    function _coordsToModelPoints(coords) {
        var cm = window.coordinateMapper;
        return (coords || []).map(function (p) {
            var t = (p.time !== undefined && p.time !== null) ? p.time : null;
            if (t === null && cm && typeof cm.logicalToTime === 'function') {
                var r = cm.logicalToTime(p.logical);
                if (r !== null && r !== undefined && !isNaN(r)) t = r;
            }
            return { time: t, price: p.price, logical: p.logical };
        });
    }

    function _modelPointsToCoords(points) {
        var cm = window.coordinateMapper;
        return (points || []).map(function (p) {
            // time is canonical -> always re-resolve logical against the bars
            // currently loaded. This is what makes timeframe switches and
            // historical prepends self-correcting (A5/A6).
            var lg = p.logical;
            if (p.time !== undefined && p.time !== null && cm && typeof cm.timeToLogical === 'function') {
                var r = cm.timeToLogical(p.time);
                if (r !== null && r !== undefined && !isNaN(r)) lg = r;
            }
            if (lg === undefined || lg === null) lg = p.logical !== undefined ? p.logical : p.time;
            return { logical: lg, price: p.price, time: p.time !== undefined ? p.time : null };
        });
    }
    window._coordsToModelPoints = _coordsToModelPoints;
    window._modelPointsToCoords = _modelPointsToCoords;

    var Layer = window.Layer || {
        BACKGROUND: 'background',
        DRAWINGS_BELOW: 'drawings_below',
        DRAWINGS: 'drawings',
        DRAWINGS_ABOVE: 'drawings_above',
        TEXT: 'text',
        FOREGROUND: 'foreground',
        parse: function(l) { return l; },
        nameOf: function(l) { return l; }
    };


    /**
     * ToolManager: Orchestrates the drawing interactions.
     */
    class ToolManager {
        constructor(canvasContainerId, canvasId) {
            this.container = document.getElementById(canvasContainerId);
            this.canvas = document.getElementById(canvasId);
            this.canvas.style.touchAction = 'none';
            this.ctx = this.canvas.getContext('2d');

            // Phase 1: DrawingEngine integration (drawing-core.js)
            this.engine = new DrawingEngine({
                timeToX: null,   // set by chart coordinate bridge
                priceToY: null,
                xToTime: null,
                yToPrice: null
            });

            // Phase 1.5: Single source of truth — delegate to engine.drawings
            Object.defineProperty(this, 'drawings', {
                get: function() { return this.engine.drawings; },
                set: function(val) { this.engine.drawings = val; },
                configurable: true
            });
            window._drawingEngine = this.engine;

            this.activeTool = null;
            this.isDrawing = false;
            this.currentDrawing = null;
            this.activeGroup = null;

            this.magnetEnabled = false;

            // Phase 2: Wire magnet toggle to SnappingEngine mode
            Object.defineProperty(this, 'magnetEnabled', {
                get: function() { return this.engine.snapping.mode !== 'disabled'; },
                set: function(val) {
                    this.engine.snapping.setMode(val ? 'strong' : 'disabled');
                },
                configurable: true
            });
            this.magnetEnabled = false; // Default to false (manual toggle only)
            this.stayMode = false;
            this.locked = false;
            this.hidden = false;

            // Dragging state
            this.isDraggingHandle = false;
            this.draggedHandle = null;
            this.dragDrawing = null;
            this.isDraggingDrawing = false;
            this.dragStartCoords = null;
            this.mouseDownPos = null;
            this._lastDragPos = null;
            this.isDraggingChart = false;

            // Mobile Drawing Buffer State (Angel One / TradingView Parity)
            this.mobileReticle = {
                visible: false,
                x: 0,
                y: 0,
                snapped: false,
                snapLabel: '',
                price: null,
                logical: null
            };
            this._isTouchMovingReticle = false;
            this._mobileDrawingBarEl = null;

            this.stylePanel = null;

            // Wire engine events to redraw
            this.engine.eventBus.on('drawing:create', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:update', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:delete', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:move', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:lock', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:unlock', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:duplicate', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:visibility', () => this._needsRedraw = true);
            this.engine.eventBus.on('drawing:command', () => this._needsRedraw = true);
            var self = this;
            this.engine.eventBus.on('viewport:changed', function() {
                self._needsRedraw = true;
                // During auto-scroll, the chart scrolls but mouse doesn't move.
                // Recompute drag position so handles/drawings advance with scroll.
                if (self.isDraggingHandle && self.dragDrawing && self._lastDragPos) {
                    self._updateDragFromPos(self._lastDragPos);
                }
                if (self.isDraggingDrawing && self.selectedDrawing && self._lastDragPos) {
                    self._updateBodyDragFromPos(self._lastDragPos);
                }
                // Invalidate Fib drawing caches on viewport changes (P0-5)
                for (var i = 0; i < self.drawings.length; i++) {
                    var d = self.drawings[i];
                    if (d.onViewportChange) d.onViewportChange();
                }
            });

            this.init();
        }

        // Phase 1.5: Selection derived from engine.selection (single source of truth)
        get selectedDrawing() {
            if (this.engine.selection.count === 0) return null;
            var id = this.engine.selection.getPrimary();
            if (!id) return null;
            return this.engine.getDrawing(id);
        }

        get hoveredDrawing() {
            var id = this.engine.selection.hoveredId;
            if (!id) return null;
            return this.engine.getDrawing(id);
        }

        set hoveredDrawing(d) {
            this.engine.selection.hoveredId = d ? d.model.id : null;
        }

        init() {
            this.resizeCanvas();
            window.addEventListener('resize', () => this.resizeCanvas());

            this.stylePanel = new DrawingStylePanel(this);
            this.loadDrawings();

            // Phase 2: Set up SnappingEngine data provider
            var self = this;
            this.engine.snapping.setDataProvider(function(x, chartState) {
                if (!chartState || !chartState.pixelToCoord) return null;
                var coord = chartState.pixelToCoord(x, 0);
                if (!coord || coord.logical == null) return null;
                // Find candle at this logical index from chart data
                if (window._chartCandles && Array.isArray(window._chartCandles)) {
                    var idx = Math.round(coord.logical);
                    if (idx >= 0 && idx < window._chartCandles.length) {
                        var c = window._chartCandles[idx];
                        return { open: c.open, high: c.high, low: c.low, close: c.close };
                    }
                }
                return null;
            });

            // Permanently set pointer-events: none on drawing layer so that default chart
            // panning and zooming work out-of-the-box. Events are routed via container capture listeners.
            if (this.container) this.container.style.pointerEvents = 'none';
            if (this.canvas) this.canvas.style.pointerEvents = 'none';

            // Global key listener for ESC (reset) and Delete/Backspace (delete selected)
            window.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    this.cancelDrag();
                    // TradingView ESC hierarchy:
                    // 1. If drawing in progress → cancel drawing, keep tool active
                    // 2. If drawing selected → deselect
                    // 3. If tool active with no drawing → deactivate tool
                    if (this.isDrawing && this.currentDrawing) {
                        this.cancelDrawing();
                        e.preventDefault();
                        return;
                    }
                    if (this.selectedDrawing) {
                        this._selectDrawing(null);
                        this.redraw();
                        e.preventDefault();
                        return;
                    }
                    if (this.activeTool && this.activeTool.type !== 'cursor') {
                        this.activeTool = null;
                        this.enterSelectionMode();
                        this.redraw();
                    }
                    e.preventDefault();
                    return;
                }
                if ((e.key === 'Delete' || e.key === 'Backspace') && this.selectedDrawing) {
                    this.deleteSelected();
                    e.preventDefault();
                }
                // Undo/Redo — skip if focus is inside a text input
                var tag = document.activeElement && document.activeElement.tagName;
                if (tag === 'INPUT' || tag === 'TEXTAREA' || document.activeElement && document.activeElement.getAttribute('contenteditable')) return;
                if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
                    this.engine.undo();
                    e.preventDefault();
                }
                if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
                    this.engine.redo();
                    e.preventDefault();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'd') {
                    if (this.selectedDrawing && this.selectedDrawing.model) {
                        var dup = this.engine.duplicateDrawing(this.selectedDrawing.model.id);
                        if (dup) this._selectDrawing(dup);
                    }
                    e.preventDefault();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
                    if (this.selectedDrawing && this.selectedDrawing.model) {
                        this._copyBuffer = this.selectedDrawing.model.toJSON();
                    }
                    e.preventDefault();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'v') {
                    if (this._copyBuffer) {
                        var cloned = JSON.parse(JSON.stringify(this._copyBuffer));
                        cloned.id = 'd_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,6);
                        var existingNames = this.drawings.map(function(d) { return (d.model ? d.model.name : d.name) || ''; }).filter(function(n) { return n; });
                        cloned.name = DrawingModel.generateName(cloned.type, existingNames);
                        var Cls = window.DrawingClasses ? window.DrawingClasses[cloned.type] : null;
                        if (Cls) {
                            var renderer = new Cls({ x: 0, y: 0 }, null, cloned.style || {});
                            renderer.coords = _modelPointsToCoords(cloned.points);
                            renderer.style = JSON.parse(JSON.stringify(cloned.style));
                            renderer.locked = !!cloned.locked;
                            renderer.hidden = !!(cloned.visibility && cloned.visibility.hidden);
                            renderer.model = cloned;
                            if (renderer.normalizeStyle) renderer.normalizeStyle();
                            this.drawings.push(renderer);
                            this.engine.undoManager.onNewDrawing();
                            this.engine.markDirty();
                            this._selectDrawing(renderer);
                            this.engine.saveNow();
                            this.redraw();
                        }
                    }
                    e.preventDefault();
                }
            });

            // Close submenus on click outside
            document.addEventListener('click', (e) => {
                if (!e.target.closest('.toolbar-item') && !e.target.closest('.drawing-submenu') && !e.target.closest('#drawing-toolbar') && !e.target.closest('.emoji-picker-panel')) {
                    this.closeAllSubmenus();
                }
            });

            // PARENT INTERACTION LAYER (Crucial for "Click-Through" behavior)
            // We listen on the chart-container in Capture phase to intercept events 
            // before they hit the Chart (when in selection mode) or Canvas.
            // Guards: instance flag prevents duplicate registration across constructor
            // and any late selectTool calls.
            if (!this._listenersRegistered) {
                var chartContainer = this.container ? this.container.parentElement : null;
                if (!chartContainer) chartContainer = document.getElementById('chart-container');
                this.chartContainer = chartContainer;
                if (chartContainer) {
                    this._listenersRegistered = true;
                    chartContainer.addEventListener('pointerdown', (e) => this.handleContainerMouseDown(e), { capture: true });
                    chartContainer.addEventListener('pointermove', (e) => this.handleContainerMouseMove(e), { capture: true });
                    chartContainer.addEventListener('pointerup', (e) => this.handleContainerMouseUp(e), { capture: true });
                    chartContainer.addEventListener('pointercancel', (e) => this.handleContainerMouseUp(e), { capture: true });
                    chartContainer.addEventListener('dblclick', (e) => this.handleContainerDoubleClick(e), { capture: true });
                    chartContainer.addEventListener('contextmenu', (e) => this.handleContainerContextMenu(e), { capture: true });

                    // Mobile Touch Interceptors: Allow pinch-to-zoom and axis scaling while handling drawing interactions
                    var handleTouchCapture = (e) => {
                        // Allow multi-touch gestures (pinch-to-zoom date gap & price scale)
                        if (e.touches && e.touches.length >= 2) {
                            this._isScalingChart = true;
                            this._enableChartScroll();
                            return;
                        }
                        // Check if touch is on bottom time scale or right price scale
                        var chart = window.bigChart || window.chart;
                        var plotWidth = (chart && typeof chart.timeScale === 'function' && typeof chart.timeScale().width === 'function') ? chart.timeScale().width() : (this.canvas ? this.canvas.width / (window.devicePixelRatio || 1) : 0);
                        var plotHeight = (chart && typeof chart.paneSize === 'function') ? (chart.paneSize() ? chart.paneSize().height : 0) : (this.canvas ? this.canvas.height / (window.devicePixelRatio || 1) : 0);
                        var parentElem = this.chartContainer || (this.container ? this.container.parentElement : null) || document.getElementById('chart-container');
                        if (parentElem && plotWidth > 0 && plotHeight > 0) {
                            var parentRect = parentElem.getBoundingClientRect();
                            var touch = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]);
                            if (touch) {
                                var tx = touch.clientX - parentRect.left;
                                var ty = touch.clientY - parentRect.top;
                                if (tx >= (plotWidth - 5) || ty >= (plotHeight - 5)) {
                                    this._isScalingChart = true;
                                    this._enableChartScroll();
                                    return;
                                }
                            }
                        }
                        if (e.type === 'touchstart') {
                            this._isScalingChart = false;
                        }
                        if (this._isScalingChart) {
                            return;
                        }

                        var isDragging = this.isDrawing || this.isDraggingHandle || this.isDraggingDrawing;
                        var isDrawingToolActive = this.activeTool && this.activeTool.type !== 'cursor';
                        if (isDragging || isDrawingToolActive) {
                            if (e.cancelable) e.preventDefault();
                            e.stopPropagation();
                        }
                    };
                    chartContainer.addEventListener('touchstart', handleTouchCapture, { capture: true, passive: false });
                    chartContainer.addEventListener('touchmove', handleTouchCapture, { capture: true, passive: false });
                    chartContainer.addEventListener('touchend', handleTouchCapture, { capture: true, passive: false });
                    chartContainer.addEventListener('touchcancel', handleTouchCapture, { capture: true, passive: false });

                    // Also add window-level safety pointerup to ensure drag termination (C-2)
                    window.addEventListener('pointerup', (e) => {
                        if (this.isDraggingHandle || this.isDraggingDrawing || this._isTouchMovingReticle) {
                            this.handleContainerMouseUp(e);
                        }
                    }, { capture: true });
                    window.addEventListener('touchend', (e) => {
                        if (this.isDraggingHandle || this.isDraggingDrawing || this._isTouchMovingReticle) {
                            this.handleContainerMouseUp(e);
                        }
                    }, { capture: true });
                    // Force redraw on container scroll/pan events
                    chartContainer.addEventListener('scroll', () => { if (window.toolManager) window.toolManager._needsRedraw = true; }, { passive: true });
                } else {
                    console.error('[DIAG] NO chartContainer found in constructor — drawing interactions disabled');
                }
            }
        }

        // --- Container Level Handlers (Capture Phase) ---
        // These run BEFORE the Chart or Canvas gets the event.

        handleContainerMouseDown(e) {
            if (e.button !== undefined && e.button !== 0) return;
            if (this._justActivatedTool) return;
            if (e.target.closest('.toolbar-item, .drawing-submenu, .drawing-toolbar, .left-toolbar, .submenu-item, #drawing-toolbar, #left-toolbar, .style-panel, .floating-panel, button, input, select')) {
                return;
            }

            var isTouch = (e.pointerType === 'touch' || e.type === 'touchstart' || (e.touches && e.touches.length > 0) || this.isMobileTouchMode());
            const isDrawingToolActive = this.activeTool && this.activeTool.type !== 'cursor';

            // Check if touch is on scale or multi-touch gesture — allow chart zoom and scale
            var chart = window.bigChart || window.chart;
            var plotWidth = (chart && typeof chart.timeScale === 'function' && typeof chart.timeScale().width === 'function') ? chart.timeScale().width() : (this.canvas ? this.canvas.width / (window.devicePixelRatio || 1) : 0);
            var plotHeight = (chart && typeof chart.paneSize === 'function') ? (chart.paneSize() ? chart.paneSize().height : 0) : (this.canvas ? this.canvas.height / (window.devicePixelRatio || 1) : 0);
            var parentElem = this.chartContainer || (this.container ? this.container.parentElement : null) || document.getElementById('chart-container');
            if (isTouch && parentElem && plotWidth > 0 && plotHeight > 0) {
                var parentRect = parentElem.getBoundingClientRect();
                var cx = (e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientX : 0)));
                var cy = (e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientY : 0)));
                var tx = cx - parentRect.left;
                var ty = cy - parentRect.top;
                var isMultiTouch = (e.touches && e.touches.length >= 2);
                if (isMultiTouch || tx >= (plotWidth - 5) || ty >= (plotHeight - 5)) {
                    this._isScalingChart = true;
                    this._enableChartScroll();
                    return;
                }
            }
            this._isScalingChart = false;

            // Mobile Touch Interception: Use precision crosshair reticle buffer
            if (isDrawingToolActive && isTouch) {
                e.stopPropagation();
                if (e.cancelable) e.preventDefault();
                this._disableChartScroll();
                this._isTouchMovingReticle = true;
                var cx0 = (e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientX : 0)));
                var cy0 = (e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientY : 0)));
                this._touchStartPos = { x: cx0, y: cy0 };
                this._touchStartTime = Date.now();
                this._touchHasMoved = false;

                // Ensure reticle has an initial position (if not already set, place in center)
                var dpr = window.devicePixelRatio || 1;
                var plotWidth = this.canvas ? (this.canvas.width / dpr) : 300;
                var plotHeight = this.canvas ? (this.canvas.height / dpr) : 300;
                if (!this.mobileReticle || !this.mobileReticle.visible || this.mobileReticle.x == null) {
                    this.mobileReticle.x = Math.round(plotWidth / 2);
                    this.mobileReticle.y = Math.round(plotHeight / 2);
                    this.mobileReticle.visible = true;
                }
                // Reticle stays in its current place and moves relative to finger drag
                this._reticleStartPos = { x: this.mobileReticle.x, y: this.mobileReticle.y };
                return;
            }

            const pos = isDrawingToolActive ? this.getMousePos(e, true) : this.getMousePos(e, false);
            this.logDrawingState('handleContainerMouseDown');
            const chartState = this.getChartState();
            this.containerMouseDownPos = pos;
            this.mouseDownPos = pos;

            if (isDrawingToolActive) {
                e.stopPropagation();
                if (e.cancelable) e.preventDefault();
                this._disableChartScroll();
                if (!this.isDrawing) {
                    this.isDrawing = true;
                    this.updatePointerEvents();
                    var options = this.activeTool.options || {};
                    options.points = this.activeTool.points;
                    this.currentDrawing = new this.activeTool.class(pos, chartState, options);

                    if (typeof this.currentDrawing.begin === 'function') {
                        this.currentDrawing.begin();
                    }
                    // Constructor already pushed the first coord; for 1-point tools we're done.
                    if (this.currentDrawing.isComplete && this.currentDrawing.isComplete()) {
                        this.finishDrawing();
                    }
                } else {
                    // Subsequent click: add another point at current cursor position
                    this.currentDrawing.addPoint(pos, chartState);
                    if (this.currentDrawing.isComplete && this.currentDrawing.isComplete()) {
                        this.finishDrawing();
                    }
                }
                this.redraw();
                return;
            }

            // Selection mode: hit-test handles on selected drawing first
            if (this.selectedDrawing && !this.selectedDrawing.locked) {
                var handleHit = this.hitTestHandles(pos, this.selectedDrawing, chartState);
                if (handleHit) {
                    e.stopPropagation();
                    if (e.cancelable) e.preventDefault();
                    this.isDraggingHandle = true;
                    this.draggedHandle = handleHit;
                    this.dragDrawing = this.selectedDrawing;
                    this.dragStartCoords = this.selectedDrawing.coords ? JSON.parse(JSON.stringify(this.selectedDrawing.coords)) : null;
                    this.dragStartTargetPrice = this.selectedDrawing.targetPrice;
                    this.dragStartStopPrice = this.selectedDrawing.stopPrice;
                    this.dragStartEntryPrice = this.selectedDrawing.entryPrice;
                    this._disableChartScroll();
                    this.updatePointerEvents();
                    this.engine.stopAutoScroll();
                    return;
                }
            }

            // Hit-test other drawings
            var hitResult = this.hitTestFull(pos, chartState);
            var hitDrawing = hitResult ? hitResult.drawing : null;

            if (hitDrawing && !hitDrawing.locked) {
                e.stopPropagation();
                if (e.cancelable) e.preventDefault();
                this._selectDrawing(hitDrawing);
                this.dragStartCoords = hitDrawing.coords ? JSON.parse(JSON.stringify(hitDrawing.coords)) : null;
                this.dragStartTargetPrice = hitDrawing.targetPrice;
                this.dragStartStopPrice = hitDrawing.stopPrice;
                this.dragStartEntryPrice = hitDrawing.entryPrice;

                if (hitResult && (hitResult.hitType === 'anchor' || hitResult.hitType === 'midpoint' || (hitResult.hitType === 'edge' && hitResult.handleLabel))) {
                    this.isDraggingHandle = true;
                    this.draggedHandle = hitResult;
                    this.dragDrawing = hitDrawing;
                } else {
                    this.isDraggingDrawing = true;
                }
                this._disableChartScroll();
                this.updatePointerEvents();
                this.engine.stopAutoScroll();
                this.redraw();
                return;
            }

            // Clicked empty space
            if (this.selectedDrawing) {
                this._selectDrawing(null);
                this.redraw();
            }

            // Marquee selection if Shift or Ctrl is held
            if ((e.shiftKey || e.ctrlKey)) {
                e.stopPropagation();
                e.preventDefault();
                this.engine.startMarquee(pos);
            }
        }

        handleContainerMouseMove(e) {
            if (e.target && e.target.closest && e.target.closest('.toolbar-item, .drawing-submenu, .drawing-toolbar, .left-toolbar, .submenu-item, #drawing-toolbar, #left-toolbar, .style-panel, .floating-panel')) {
                return;
            }

            var isTouch = (e.pointerType === 'touch' || e.type === 'touchmove' || (e.touches && e.touches.length > 0) || this.isMobileTouchMode());
            const isDrawingToolActive = this.activeTool && this.activeTool.type !== 'cursor';

            if (this._isScalingChart) {
                return;
            }

            // If touching on scale or multi-touch, allow chart scaling
            var chart = window.bigChart || window.chart;
            var plotWidth = (chart && typeof chart.timeScale === 'function' && typeof chart.timeScale().width === 'function') ? chart.timeScale().width() : (this.canvas ? this.canvas.width / (window.devicePixelRatio || 1) : 0);
            var plotHeight = (chart && typeof chart.paneSize === 'function') ? (chart.paneSize() ? chart.paneSize().height : 0) : (this.canvas ? this.canvas.height / (window.devicePixelRatio || 1) : 0);
            var parentElem = this.chartContainer || (this.container ? this.container.parentElement : null) || document.getElementById('chart-container');
            if (isTouch && parentElem && plotWidth > 0 && plotHeight > 0) {
                var parentRect = parentElem.getBoundingClientRect();
                var cx = (e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientX : 0)));
                var cy = (e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientY : 0)));
                var tx = cx - parentRect.left;
                var ty = cy - parentRect.top;
                var isMultiTouch = (e.touches && e.touches.length >= 2);
                if (isMultiTouch || tx >= (plotWidth - 5) || ty >= (plotHeight - 5)) {
                    return;
                }
            }

            if (isDrawingToolActive && (isTouch || this._isTouchMovingReticle)) {
                e.stopPropagation();
                if (e.cancelable) e.preventDefault();
                this._disableChartScroll();
                if (this._touchStartPos && this._reticleStartPos) {
                    var curX = (e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientX : 0)));
                    var curY = (e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : (e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientY : 0)));
                    var dx = curX - this._touchStartPos.x;
                    var dy = curY - this._touchStartPos.y;
                    if (Math.hypot(dx, dy) > 4) {
                        this._touchHasMoved = true;
                    }
                    this._updateMobileReticleDelta(this._reticleStartPos.x + dx, this._reticleStartPos.y + dy);
                }
                return;
            }

            const isDragging = this.isDrawing || this.isDraggingHandle || this.isDraggingDrawing;
            var useSnap = (isDrawingToolActive || isDragging);
            if (this.isDraggingHandle && this.dragDrawing && (this.dragDrawing instanceof LongPosition || this.dragDrawing instanceof ShortPosition)) {
                useSnap = false;
            }
            if (this.isDraggingDrawing && this.selectedDrawing && (this.selectedDrawing instanceof LongPosition || this.selectedDrawing instanceof ShortPosition)) {
                useSnap = false;
            }
            const pos = useSnap ? this.getMousePos(e, true) : this.getMousePos(e, false);
            this.lastMousePos = pos;
            if (isDrawingToolActive && !this.isDrawing) {
                this.redraw();
            }
            const chartState = this.getChartState();
            const container = document.getElementById('chart-container');

            if (this.isDrawing && this.currentDrawing) {
                e.stopPropagation();
                e.preventDefault();
                // Drag-based tools accumulate points via update(); others preview via updatePreview().
                if (this.activeTool && this.activeTool.dragBased) {
                    if (typeof this.currentDrawing.update === 'function') {
                        this.currentDrawing.update(pos, chartState);
                    } else if (typeof this.currentDrawing.updatePreview === 'function') {
                        this.currentDrawing.updatePreview(pos, chartState);
                    }
                } else if (typeof this.currentDrawing.updatePreview === 'function') {
                    this.currentDrawing.updatePreview(pos, chartState);
                } else {
                    this.currentDrawing.update(pos, chartState);
                }
                this.redraw();
                if (container) container.style.cursor = 'crosshair';
                return;
            }

            if (this.isDraggingHandle && this.dragDrawing && chartState) {
                e.stopPropagation();
                e.preventDefault();
                this._lastDragPos = { x: pos.x, y: pos.y };
                var dp = { x: pos.x, y: pos.y };
                if (this.dragDrawing.model && e.shiftKey) {
                    var originPixels = this.dragDrawing.getPixels ? this.dragDrawing.getPixels(chartState) : [];
                    if (originPixels.length > 0) {
                        dp = this.engine.applyConstraint(dp, originPixels[0], true, 'drag');
                    }
                }
                var coord = chartState.pixelToCoord(dp.x, dp.y);
                if (coord && coord.price !== null && coord.price !== undefined) {
                    if (typeof this.dragDrawing.updateHandle === 'function') {
                        var handleArg = (this.draggedHandle && this.draggedHandle.hitType === 'edge')
                            ? this.draggedHandle.handleLabel
                            : this.draggedHandle;
                        this.dragDrawing.updateHandle(handleArg, coord.price, chartState, dp.x, dp.y);
                    }
                    this.redraw();
                }
                this.engine.checkAutoScroll(pos.x, pos.y, chartState);
                if (container) {
                    var label = this.draggedHandle && (this.draggedHandle.handleLabel || this.draggedHandle.label || this.draggedHandle);
                    var idx = this.draggedHandle && (this.draggedHandle.handleIndex !== undefined ? this.draggedHandle.handleIndex : this.draggedHandle.index);
                    var drawingClass = this.dragDrawing && this.dragDrawing.constructor.name;
                    
                    if (label && typeof label === 'object' && label.hitType) {
                        idx = label.handleIndex;
                        label = label.handleLabel;
                    }
                    
                    if (label === 'left' || label === 'right') {
                        container.style.cursor = 'ew-resize';
                    } else if (label === 'target' || label === 'stop' || label === 'top' || label === 'bottom') {
                        container.style.cursor = 'ns-resize';
                    } else if (label === 'topLeft' || label === 'bottomRight' || ((drawingClass === 'Rectangle' || drawingClass === 'Circle' || drawingClass === 'Ellipse') && (idx === 0 || idx === 2))) {
                        container.style.cursor = 'nwse-resize';
                    } else if (label === 'topRight' || label === 'bottomLeft' || ((drawingClass === 'Rectangle' || drawingClass === 'Circle' || drawingClass === 'Ellipse') && (idx === 1 || idx === 3))) {
                        container.style.cursor = 'nesw-resize';
                    } else if (drawingClass === 'Rectangle' || drawingClass === 'Circle' || drawingClass === 'Ellipse') {
                        if (idx === 0 || idx === 1) container.style.cursor = 'ns-resize';
                        else container.style.cursor = 'ew-resize';
                    } else {
                        container.style.cursor = 'grabbing';
                    }
                }
                return;
            }

            if (this.isDraggingDrawing && this.selectedDrawing && chartState) {
                e.stopPropagation();
                e.preventDefault();
                this._lastDragPos = { x: pos.x, y: pos.y };
                this._updateBodyDragFromPos(pos);
                this.engine.checkAutoScroll(pos.x, pos.y, chartState);
                return;
            }

            if (this.engine.getMarqueeRect()) {
                e.stopPropagation();
                e.preventDefault();
                this.engine.updateMarquee(pos);
                this.redraw();
                return;
            }

            // Hover state logic when not dragging
            if (isDrawingToolActive) {
                if (container) container.style.cursor = 'crosshair';
                return;
            }

            var hitResult = this.hitTestFull(pos, chartState);
            var hitDrawing = hitResult ? hitResult.drawing : null;
            var previousHovered = this.hoveredDrawing;

            if (hitResult && hitResult.hitType !== 'anchor' && hitResult.hitType !== 'midpoint') {
                this.hoveredDrawing = hitDrawing;
            } else {
                this.hoveredDrawing = null;
            }

            this.updatePointerEvents();

            if (container) {
                if (hitResult) {
                    if (hitResult.hitType === 'anchor' || hitResult.hitType === 'midpoint') {
                        var idx = hitResult.handleIndex;
                        var drawingClass = hitResult.drawing.constructor.name;
                        
                        if (drawingClass === 'Rectangle' || drawingClass === 'Circle' || drawingClass === 'Ellipse') {
                            if (hitResult.hitType === 'midpoint') {
                                if (idx === 0 || idx === 1) container.style.cursor = 'ns-resize';
                                else container.style.cursor = 'ew-resize';
                            } else {
                                if (idx === 0 || idx === 2) container.style.cursor = 'nwse-resize';
                                else container.style.cursor = 'nesw-resize';
                            }
                        } else {
                            container.style.cursor = 'pointer';
                        }
                    } else if (hitResult.hitType === 'edge') {
                        var lbl = hitResult.handleLabel;
                        if (lbl === 'left' || lbl === 'right') {
                            container.style.cursor = 'ew-resize';
                        } else if (lbl === 'target' || lbl === 'stop' || lbl === 'top' || lbl === 'bottom') {
                            container.style.cursor = 'ns-resize';
                        } else if (lbl === 'topLeft' || lbl === 'bottomRight') {
                            container.style.cursor = 'nwse-resize';
                        } else if (lbl === 'topRight' || lbl === 'bottomLeft') {
                            container.style.cursor = 'nesw-resize';
                        } else {
                            container.style.cursor = 'pointer';
                        }
                    } else if (hitResult.hitType === 'fill' || hitResult.hitType === 'body') {
                        container.style.cursor = 'grab';
                    } else {
                        container.style.cursor = '';
                    }
                } else {
                    container.style.cursor = '';
                }
            }

            if (previousHovered !== this.hoveredDrawing) {
                this.redraw();
            }
        }

        handleContainerMouseUp(e) {
            this.engine.stopAutoScroll();
            if (this._isScalingChart) {
                this._isScalingChart = false;
                return;
            }
            if (this._isTouchMovingReticle) {
                e.stopPropagation();
                if (e.cancelable) e.preventDefault();
                this._isTouchMovingReticle = false;
                var wasTap = !this._touchHasMoved;
                this._touchStartPos = null;
                this._reticleStartPos = null;
                this._touchHasMoved = false;

                if (wasTap) {
                    // Quick tap commits the point at the current crosshair position!
                    this.commitMobileTouchPoint();
                } else {
                    // Drag moved the crosshair to the desired place: keep it there ready for user to tap!
                    this.mobileReticle.visible = true;
                    this.redraw();
                }
                return;
            }
            if (this._justFinishedDrawing) {
                this._justFinishedDrawing = false;
                this.containerMouseDownPos = null;
                return;
            }
            const isDrawingToolActive = this.activeTool && this.activeTool.type !== 'cursor';

            if (isDrawingToolActive && this.isDrawing && this.currentDrawing) {
                if (this.activeTool.dragBased) {
                    e.stopPropagation();
                    e.preventDefault();
                    this.finishDrawing();
                    return;
                }
            }

            if (this.isDraggingHandle) {
                e.stopPropagation();
                if (e.cancelable) e.preventDefault();
                if (this.dragDrawing && this.dragDrawing.model) {
                    this.engine.syncAndCapture(this.dragDrawing, 'resize');
                    this.engine.saveNow();
                } else {
                    this.saveDrawings();
                }
                this.isDraggingHandle = false;
                this.draggedHandle = null;
                this.dragDrawing = null;
                this.containerMouseDownPos = null;
                this.updatePointerEvents();
                this._enableChartScroll();
                this.redraw();
                return;
            }

            if (this.isDraggingDrawing) {
                e.stopPropagation();
                if (e.cancelable) e.preventDefault();
                var sel = this.selectedDrawing;
                if (sel && typeof sel._invalidateCache === 'function') {
                    sel._invalidateCache();
                }
                if (sel && sel.model) {
                    this.engine.syncAndCapture(sel, 'move');
                    this.engine.saveNow();
                } else {
                    this.saveDrawings();
                }
                this.isDraggingDrawing = false;
                this.dragStartCoords = null;
                this.containerMouseDownPos = null;
                this.updatePointerEvents();
                this._enableChartScroll();
                this.redraw();
                return;
            }

            if (this.engine.getMarqueeRect()) {
                e.stopPropagation();
                e.preventDefault();
                this.engine.endMarquee(this.getChartState());
                var selIds = this.engine.selection.getSelected();
                var primaryId = selIds && selIds.length > 0 ? selIds[selIds.length - 1] : null;
                var primaryDrawing = null;
                if (primaryId) {
                    for (var si = 0; si < this.drawings.length; si++) {
                        if (this.drawings[si].model && this.drawings[si].model.id === primaryId) {
                            primaryDrawing = this.drawings[si]; break;
                        }
                    }
                }
                this._selectDrawing(primaryDrawing);
                this.updatePointerEvents();
                this.redraw();
                return;
            }
        }

        // Double-click: finish a drawing in progress, or insert a vertex on a path segment
        handleContainerDoubleClick(e) {
            var chartState = this.getChartState();
            var pos = this.getMousePos(e, false);

            // 1. Path in progress → finish (double-click)
            if (this.isDrawing && this.currentDrawing && this.currentDrawing._pathLike) {
                e.stopPropagation();
                e.preventDefault();
                // A double-click fires 2 pointerdowns, adding duplicate trailing
                // vertices at the click position — collapse to a single final vertex.
                var px = this.currentDrawing.getPixels ? this.currentDrawing.getPixels(chartState) : [];
                var near = 0;
                while (px.length > 0) {
                    var last = px[px.length - 1];
                    var ddx = last.x - pos.x, ddy = last.y - pos.y;
                    if (Math.sqrt(ddx * ddx + ddy * ddy) < 6) { near++; px.pop(); }
                    else break;
                }
                for (var i = 0; i < near - 1; i++) {
                    this.currentDrawing.coords.pop();
                }
                this.finishDrawing();
                return;
            }

            // 2. Completed path: double-click on a segment inserts a vertex
            if (this.selectedDrawing && this.selectedDrawing._pathLike && chartState) {
                var hit = this.hitTestFull(pos, chartState);
                if (hit && hit.drawing === this.selectedDrawing && hit.hitType === 'edge') {
                    e.stopPropagation();
                    e.preventDefault();
                    var inserted = this.selectedDrawing.insertVertexAtPixel(hit.pixel || pos, chartState);
                    if (inserted) {
                        this.engine.syncAndCapture(this.selectedDrawing, 'edit');
                        this.engine.saveNow();
                        this.redraw();
                    }
                    return;
                }
            }
        }

        // Context menu: delete a path vertex under the cursor (right-click on a handle)
        handleContainerContextMenu(e) {
            var chartState = this.getChartState();
            if (!chartState || !this.selectedDrawing || !this.selectedDrawing._pathLike) return;
            var pos = this.getMousePos(e, false);
            var hit = this.hitTestFull(pos, chartState);
            if (hit && hit.drawing === this.selectedDrawing && hit.hitType === 'anchor' && hit.handleIndex !== undefined) {
                e.stopPropagation();
                e.preventDefault();
                if (this.selectedDrawing.deleteVertexAt(hit.handleIndex)) {
                    this.engine.syncAndCapture(this.selectedDrawing, 'edit');
                    this.engine.saveNow();
                    this.redraw();
                }
            }
        }

        saveDrawings() {
            // Phase 1.5: sync all renderers to their models, then save via engine
            for (var i = 0; i < this.drawings.length; i++) {
                var d = this.drawings[i];
                if (d && d.model && d.syncToModel) {
                    d.syncToModel();
                }
            }
            this.engine.saveNow();
        }

        loadDrawings() {
            // Phase 1.5: try engine format first, fall back to legacy format
            var loaded = this.engine.load();
            if (loaded.length > 0) {
                // Wrap each DrawingModel in a renderer
                var typeToClass = {
                    trendline: 'TrendLine', ray: 'Ray', extended: 'ExtendedLine',
                    infoline: 'InfoLine', trend_angle: 'TrendAngle',
                    horizontal_line: 'HorizontalLine', vertical_line: 'VerticalLine',
                    horizontal_ray: 'HorizontalRay', cross_line: 'CrossLine',
                    channel: 'ParallelChannel', flat_top_channel: 'FlatTopChannel',
                    flat_bottom_channel: 'FlatBottomChannel', disjoint_channel: 'DisjointChannel',
                    regression_trend: 'RegressionTrend', pitchfork: 'Pitchfork',
                    schiff_pitchfork: 'SchiffPitchfork', modified_schiff_pitchfork: 'ModifiedSchiffPitchfork',
                    inside_pitchfork: 'InsidePitchfork',
                    rectangle: 'Rectangle', circle: 'Circle', ellipse: 'Ellipse',
                    triangle: 'TriangleShape', path: 'PathDrawing',
                    brush: 'BrushDrawing', highlighter: 'HighlighterDrawing',
                    text: 'TextDrawing', price_label: 'PriceLabel',
                    arrow_marker: 'ArrowMarker',
                    note: 'TextNote', anchored_text: 'AnchoredText', callout: 'Callout',
                    xabcd_pattern: 'XabcdPattern', cypher_pattern: 'Cypher', abcd_pattern: 'Abcd', triangle_pattern: 'TriangleShape',
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
                    date_range: 'DateRange', date_price_range: 'DateRange', measure: 'Measure',
                    elliott_impulse: 'ImpulseWave', elliott_correction: 'CorrectiveWave',
                    elliott_triangle: 'ElliottTriangle', elliott_double_combo: 'ElliottDoubleCombo',
                    elliott_triple_combo: 'ElliottTripleCombo', elliott_flat: 'ElliottFlat',
                    elliott_zigzag: 'ElliottZigZag', elliott_combination: 'ElliottCombination',
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
                this.drawings = loaded.map(function(m) {
                    var Cls = DrawingClasses[typeToClass[m.type]] || BaseDrawing;
                    // Instantiate proper class with options/style (C-1)
                    var r = new Cls({ x: 0, y: 0 }, null, m.style || {});
                    r.coords = _modelPointsToCoords(m.points);
                    r.style = JSON.parse(JSON.stringify(m.style));
                    r.locked = !!m.locked;
                    r.hidden = !!m.hidden;
                    r.model = m;
                    if (m.content && m.content.plain) {
                        r.text = m.content.plain;
                        r.content = m.content;
                    } else if (m.text && m.text.content) {
                        r.text = m.text.content;
                        r.content = { plain: m.text.content, rich: null, markdown: null };
                    }
                    r.normalizeStyle();
                    return r;
                });
                this.engine.eventBus.emit('drawing:select', { ids: [] });
                this.redraw();
                return;
            }

            // Legacy fallback: try old format
            try {
                let ticker = window.currentTicker || 'default';
                let saved = localStorage.getItem('drawings_' + ticker);
                if (!saved) return;
                let data = JSON.parse(saved);
                this.drawings = [];
                for (let item of data) {
                    try {
                        let Cls = DrawingClasses[item.type];
                        if (Cls) {
                            // Instantiate via constructor (C-1)
                            let shape = new Cls({ x: 0, y: 0 }, null);
                            // Whitelist allowed fields to prevent localStorage prototype poisoning (L-7)
                            var allowedProps = ['coords', 'style', 'locked', 'hidden', 'text', 'points'];
                            for (var k in item) {
                                if (allowedProps.indexOf(k) !== -1) {
                                    shape[k] = item[k];
                                }
                            }
                            if (shape.coords) {
                                shape.coords.forEach(c => {
                                    if (c.logical === undefined && c.time !== undefined) c.logical = c.time;
                                });
                            }
                            shape.normalizeStyle();
                            // Phase 1.5: create model for legacy drawing
                            shape.model = this.engine.createModelFromRenderer(shape);
                            this.drawings.push(shape);
                        } else {
                            console.warn('loadDrawings: unknown type "' + item.type + '" — skipped');
                        }
                    } catch(e) {
                        console.error('loadDrawings: failed to restore drawing type "' + (item && item.type) + '" — skipped', e);
                    }
                }
                this.redraw();
            } catch(e) { console.error('Load drawings error', e); }
        }

        resizeCanvas() {
            if (this.container && this.canvas) {
                var dpr = window.devicePixelRatio || 1;
                var parentElem = this.chartContainer || (this.container ? this.container.parentElement : null) || document.getElementById('chart-container');
                var parentRect = parentElem ? parentElem.getBoundingClientRect() : null;

                this.container.style.width = '100%';
                this.container.style.height = '100%';
                this.container.style.overflow = 'hidden';

                var w = (parentRect && parentRect.width > 0) ? parentRect.width : 800;
                var h = (parentRect && parentRect.height > 0) ? parentRect.height : 500;

                this.canvas.width = Math.round(w * dpr);
                this.canvas.height = Math.round(h * dpr);
                this.canvas.style.width = w + 'px';
                this.canvas.style.height = h + 'px';
                this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                this.redraw();
            }
        }

        syncViewport() {
            if (!window.coordinateMapper) return false;
            var chart = window.bigChart || window.chart;
            var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
            if (!chart) return false;
            var lr = chart.timeScale().getVisibleLogicalRange();
            if (!lr) return false;

            var dpr = window.devicePixelRatio || 1;
            var parentElem = this.chartContainer || (this.container ? this.container.parentElement : null) || document.getElementById('chart-container');
            var parentRect = parentElem ? parentElem.getBoundingClientRect() : null;
            var totalW = (parentRect && parentRect.width > 0) ? parentRect.width : 800;
            var totalH = (parentRect && parentRect.height > 0) ? parentRect.height : 500;

            if (this.container) {
                this.container.style.width = '100%';
                this.container.style.height = '100%';
            }
            var curW = this.canvas ? this.canvas.width / dpr : 0;
            var curH = this.canvas ? this.canvas.height / dpr : 0;
            if (Math.abs(curW - totalW) > 1 || Math.abs(curH - totalH) > 1) {
                if (this.canvas) {
                    this.canvas.width = Math.round(totalW * dpr);
                    this.canvas.height = Math.round(totalH * dpr);
                    this.canvas.style.width = totalW + 'px';
                    this.canvas.style.height = totalH + 'px';
                    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                }
            }

            var width = chart.timeScale().width() || (totalW - 65);
            var timeScaleHeight = 26;
            if (typeof chart.timeScale().height === 'function') {
                var th = chart.timeScale().height();
                if (th > 0 && th < 100) timeScaleHeight = th;
            }
            var plotHeight = totalH - timeScaleHeight;
            if (typeof chart.paneSize === 'function') {
                var ps = chart.paneSize();
                if (ps && ps.height > 0) plotHeight = ps.height;
            }

            if (series) {
                var priceTop = series.coordinateToPrice(0);
                var priceBottom = series.coordinateToPrice(plotHeight);
                if (priceTop !== null && priceBottom !== null) {
                    // Detect whether the viewport has actually changed to decide if a redraw is needed
                    var vp = window.coordinateMapper.viewport;
                    var changed = (vp.logicalFrom !== lr.from || vp.logicalTo !== lr.to ||
                                   vp.priceTop !== priceTop || vp.priceBottom !== priceBottom ||
                                   vp.width !== width || vp.height !== plotHeight);
                    window.coordinateMapper.updateViewport(lr.from, lr.to, priceTop, priceBottom, width, plotHeight);
                    return changed;
                }
            }
            // Viewport update skipped (series not ready) — redraw will retry on next cycle
            return false;
        }

        _disableChartScroll() {
            var chart = window.bigChart || window.chart || (this.chartContainer && this.chartContainer._chart);
            if (chart && typeof chart.applyOptions === 'function') {
                try {
                    chart.applyOptions({
                        handleScroll: {
                            horzTouchDrag: false,
                            vertTouchDrag: false,
                            mouseWheel: false,
                            pressedMouseMove: false
                        },
                        handleScale: {
                            axisPressedMouseMove: { time: true, price: true },
                            axisDoubleClickReset: { time: true, price: true },
                            pinch: true,
                            mouseWheel: true
                        }
                    });
                } catch(e) {}
            }
        }

        _enableChartScroll() {
            var chart = window.bigChart || window.chart || (this.chartContainer && this.chartContainer._chart);
            if (chart && typeof chart.applyOptions === 'function') {
                try {
                    chart.applyOptions({
                        handleScroll: true,
                        handleScale: true
                    });
                } catch(e) {}
            }
            if (this.chartContainer) {
                this.chartContainer.style.touchAction = '';
            }
        }

        updatePointerEvents() {
            // Keep drawing overlay pointer-events permanently 'none' so that chart axes (X and Y scales)
            // and native gesture listeners always receive events without being obstructed.
            if (this.canvas && this.canvas.style.pointerEvents !== 'none') {
                this.canvas.style.pointerEvents = 'none';
            }
            if (this.container && this.container.style.pointerEvents !== 'none') {
                this.container.style.pointerEvents = 'none';
            }

            if (this.isDrawing || this.isDraggingDrawing || this.isDraggingHandle || (this.activeTool && this.activeTool.type !== 'cursor')) {
                this._disableChartScroll();
            } else {
                this._enableChartScroll();
            }
        }

        getChartState() {
            if (window.coordinateMapper) {
                this.syncViewport();
                return window.coordinateMapper;
            }
            return null;
        }

        // --- Tool Selection Logic ---

        setTool(toolDef) {
            if (this.locked) return;

            this.activeTool = toolDef;

            // Clear any old native chart crosshairs / buffer leftover from previous touches
            if (window.bigChart && typeof window.bigChart.clearCrosshairPosition === 'function') {
                try { window.bigChart.clearCrosshairPosition(); } catch(e) {}
            }
            if (window.chart && typeof window.chart.clearCrosshairPosition === 'function') {
                try { window.chart.clearCrosshairPosition(); } catch(e) {}
            }

            // Ensure canvas is sized correctly before activating tool
            this.resizeCanvas();

            this.cancelDrag(); // Cancel any in-progress drag
            this.cancelDrawing(); // Reset any current drawing before selection change
            this._selectDrawing(null); // Deselect drawing and hide style panel
            if (window.ToolbarConfig && window.ToolbarConfig.store) {
                window.ToolbarConfig.store.setActiveTool(toolDef.key);
            }
            this.updateUI();
            this.closeAllSubmenus();

            // Get the chart container to toggle drawing mode
            const chartContainer = this.container ? this.container.parentElement : document.getElementById('chart-container');

            // Listeners are registered once in the constructor — no duplicate registration here.

            // Set cursor based on tool type
            var chart = window.bigChart || window.chart;
            if (toolDef.type === 'cursor' || toolDef.key === 'eraser') {
                this.setCursor('default');
                if (this.mobileReticle) this.mobileReticle.visible = false;
                if (chartContainer) {
                    chartContainer.classList.remove('drawing-mode-active');
                    chartContainer.classList.add('selection-mode-active');
                }
                if (chart && typeof chart.applyOptions === 'function') {
                    try {
                        chart.applyOptions({
                            crosshair: {
                                vertLine: { color: '#758696', labelVisible: true },
                                horzLine: { color: '#758696', labelVisible: true }
                            }
                        });
                    } catch(e) {}
                }
            } else {
                if (toolDef.type === 'text') {
                    this.setCursor('text');
                } else {
                    this.setCursor('crosshair');
                }
                if (chartContainer) {
                    chartContainer.classList.add('drawing-mode-active');
                    chartContainer.classList.remove('selection-mode-active');
                }
                // Hide native white/gray chart crosshair lines so ONLY the blue dotted buffer line is visible
                if (chart && typeof chart.applyOptions === 'function') {
                    try {
                        chart.applyOptions({
                            crosshair: {
                                vertLine: { color: 'transparent', labelVisible: true },
                                horzLine: { color: 'transparent', labelVisible: true }
                            }
                        });
                    } catch(e) {}
                }
                // Immediately display mobile precision crosshair buffer on chart page
                if (this.isMobileTouchMode()) {
                    var dpr = window.devicePixelRatio || 1;
                    var plotWidth = this.canvas ? (this.canvas.width / dpr) : 300;
                    var plotHeight = this.canvas ? (this.canvas.height / dpr) : 300;
                    this.mobileReticle.x = Math.round(plotWidth / 2);
                    this.mobileReticle.y = Math.round(plotHeight / 2);
                    var chartState = this.getChartState();
                    if (chartState) {
                        var snapped = this.engine.snapping.snap(this.mobileReticle.x, this.mobileReticle.y, chartState);
                        if (snapped && snapped.snapped) {
                            this.mobileReticle.x = snapped.x;
                            this.mobileReticle.y = snapped.y;
                            this.mobileReticle.snapped = true;
                            this.mobileReticle.snapLabel = (snapped.label || 'Wick') + ': ' + this._formatPrice(snapped.value);
                            this.mobileReticle.price = snapped.value;
                        } else {
                            this.mobileReticle.snapped = false;
                            this.mobileReticle.snapLabel = '';
                            var coord = chartState.pixelToCoord(this.mobileReticle.x, this.mobileReticle.y);
                            if (coord) this.mobileReticle.price = coord.price;
                        }
                    }
                    this.mobileReticle.visible = true;
                }
            }

            this.updatePointerEvents();
            this.redraw();
            console.log(`Tool activated: ${toolDef.name}`);
            this.logDrawingState('setTool_End');
        }

        isMobileTouchMode() {
            return (window.innerWidth <= 768) || ('ontouchstart' in window && window.innerWidth <= 1024) || (navigator.maxTouchPoints > 0 && window.innerWidth <= 1024);
        }

        _formatPrice(price) {
            if (price == null || isNaN(price)) return '';
            return '₹' + Number(price).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        _formatTime(time) {
            if (!time) return '';
            if (typeof time === 'string') return time;
            if (typeof time === 'number') {
                var epochSec = time > 1e11 ? Math.floor(time / 1000) : time;
                var d = new Date(epochSec * 1000 + 5.5 * 3600 * 1000);
                if (!isNaN(d.getTime())) {
                    var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                    var dd = String(d.getUTCDate()).padStart(2, '0');
                    var mon = MON[d.getUTCMonth()];
                    var hh = String(d.getUTCHours()).padStart(2, '0');
                    var mm = String(d.getUTCMinutes()).padStart(2, '0');
                    return dd + ' ' + mon + ' ' + hh + ':' + mm;
                }
            }
            return String(time);
        }

        commitMobileTouchPoint() {
            const chartState = this.getChartState();
            if (!chartState || !this.activeTool || this.activeTool.type === 'cursor') return;

            const pos = { x: this.mobileReticle.x, y: this.mobileReticle.y };
            this._disableChartScroll();

            if (!this.isDrawing) {
                this.isDrawing = true;
                this.updatePointerEvents();
                var options = this.activeTool.options || {};
                options.points = this.activeTool.points;
                this.currentDrawing = new this.activeTool.class(pos, chartState, options);

                if (typeof this.currentDrawing.begin === 'function') {
                    this.currentDrawing.begin();
                }

                if (this.currentDrawing.isComplete && this.currentDrawing.isComplete()) {
                    this.finishDrawing();
                    this.mobileReticle.visible = false;
                } else {
                    if (typeof this.currentDrawing.updatePreview === 'function') {
                        this.currentDrawing.updatePreview(pos, chartState);
                    } else if (typeof this.currentDrawing.update === 'function') {
                        this.currentDrawing.update(pos, chartState);
                    }
                    this.mobileReticle.visible = true;
                    this.redraw();
                }
            } else {
                if (this.activeTool && this.activeTool.dragBased && typeof this.currentDrawing.update === 'function') {
                    this.currentDrawing.update(pos, chartState);
                }
                this.currentDrawing.addPoint(pos, chartState);
                if (this.currentDrawing.isComplete && this.currentDrawing.isComplete()) {
                    this.finishDrawing();
                    this.mobileReticle.visible = false;
                } else {
                    this.mobileReticle.visible = true;
                    this.redraw();
                }
            }
        }

        _updateMobileReticleDelta(targetX, targetY) {
            const chartState = this.getChartState();
            if (!chartState || !this.canvas) return;

            var dpr = window.devicePixelRatio || 1;
            var plotWidth = this.canvas.width / dpr;
            var plotHeight = this.canvas.height / dpr;

            var x = Math.max(0, Math.min(plotWidth, targetX));
            var y = Math.max(0, Math.min(plotHeight, targetY));

            // Snap to candle wick / body if magnet is active
            var snapped = this.engine.snapping.snap(x, y, chartState);
            if (snapped && snapped.snapped) {
                this.mobileReticle.x = snapped.x;
                this.mobileReticle.y = snapped.y;
                this.mobileReticle.snapped = true;
                this.mobileReticle.snapLabel = (snapped.label || 'Wick') + ': ' + this._formatPrice(snapped.value);
                this.mobileReticle.price = snapped.value;
            } else {
                this.mobileReticle.x = x;
                this.mobileReticle.y = y;
                this.mobileReticle.snapped = false;
                this.mobileReticle.snapLabel = '';
                var coord = chartState.pixelToCoord(x, y);
                this.mobileReticle.price = coord ? coord.price : null;
            }

            this.mobileReticle.visible = true;

            // Sync with chart native crosshair to display official price on right Y-axis and date on bottom X-axis
            var chart = window.bigChart || window.chart;
            var targetSeries = window.bigWhitespaceSeries || window.bigCandleSeries || window.mainSeries || (chart && chart._candleSeries);
            var coord = chartState.pixelToCoord(this.mobileReticle.x, this.mobileReticle.y);
            if (chart && targetSeries && this.mobileReticle.price != null && coord && coord.time != null) {
                try {
                    chart.setCrosshairPosition(this.mobileReticle.price, coord.time, targetSeries);
                } catch(e) {}
            }

            // Update live preview if drawing in progress
            if (this.isDrawing && this.currentDrawing) {
                var rPos = { x: this.mobileReticle.x, y: this.mobileReticle.y };
                if (typeof this.currentDrawing.updatePreview === 'function') {
                    this.currentDrawing.updatePreview(rPos, chartState);
                } else if (typeof this.currentDrawing.update === 'function') {
                    this.currentDrawing.update(rPos, chartState);
                }
            }

            this.redraw();
        }

        _updateMobileReticleFromTouch(e) {
            const chartState = this.getChartState();
            if (!chartState || !this.canvas) return;

            const rect = this.canvas.getBoundingClientRect();
            let clientX = e.clientX;
            let clientY = e.clientY;
            if ((clientX === undefined || clientX === null) && e.touches && e.touches.length > 0) {
                clientX = e.touches[0].clientX;
                clientY = e.touches[0].clientY;
            } else if ((clientX === undefined || clientX === null) && e.changedTouches && e.changedTouches.length > 0) {
                clientX = e.changedTouches[0].clientX;
                clientY = e.changedTouches[0].clientY;
            }

            if (clientX === undefined || clientX === null || clientY === undefined || clientY === null) return;

            var rawX = clientX - rect.left;
            var rawY = clientY - rect.top;

            this._updateMobileReticleDelta(rawX, rawY);
        }

        _drawMobileReticle(ctx, chartState, plotWidth, plotHeight) {
            if (!this.mobileReticle || !this.mobileReticle.visible) return;
            var rx = Math.max(0, Math.min(plotWidth, this.mobileReticle.x));
            var ry = Math.max(0, Math.min(plotHeight, this.mobileReticle.y));
            var isSnapped = this.mobileReticle.snapped;

            ctx.save();

            // 1. Clean Blue Dotted Crosshair Lines across entire plotting canvas
            ctx.strokeStyle = isSnapped ? 'rgba(0, 242, 254, 0.95)' : 'rgba(41, 98, 255, 0.9)';
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);

            // Horizontal Line
            ctx.beginPath();
            ctx.moveTo(0, ry);
            ctx.lineTo(plotWidth, ry);
            ctx.stroke();

            // Vertical Line
            ctx.beginPath();
            ctx.moveTo(rx, 0);
            ctx.lineTo(rx, plotHeight);
            ctx.stroke();
            ctx.setLineDash([]);

            // 2. Clean Center Intersection Dot
            ctx.beginPath();
            ctx.arc(rx, ry, 3.5, 0, Math.PI * 2);
            ctx.fillStyle = isSnapped ? '#00f2fe' : '#2962ff';
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            ctx.restore();
        }

        setCursor(type) {
            this.canvas.style.cursor = type;
        }

        // --- Input Handling ---

        getMousePos(e, allowSnap = true) {
            const rect = this.canvas.getBoundingClientRect();
            let clientX = e.clientX;
            let clientY = e.clientY;
            if (clientX === undefined && e.touches && e.touches.length > 0) {
                clientX = e.touches[0].clientX;
                clientY = e.touches[0].clientY;
            } else if (clientX === undefined && e.changedTouches && e.changedTouches.length > 0) {
                clientX = e.changedTouches[0].clientX;
                clientY = e.changedTouches[0].clientY;
            }
            let x = (clientX !== undefined ? clientX : 0) - rect.left;
            let y = (clientY !== undefined ? clientY : 0) - rect.top;

            // Long Position and Short Position do not use magnet snap
            if (this.activeTool && (this.activeTool.key === 'long_position' || this.activeTool.key === 'short_position')) {
                allowSnap = false;
            }
            if (this.currentDrawing && (this.currentDrawing instanceof LongPosition || this.currentDrawing instanceof ShortPosition)) {
                allowSnap = false;
            }
            if (this.dragDrawing && (this.dragDrawing instanceof LongPosition || this.dragDrawing instanceof ShortPosition)) {
                allowSnap = false;
            }

            // Apply Magnet via SnappingEngine (Phase 2)
            // Shift temporarily inverts magnet mode
            var snapActive = this.engine.snapping.isActive;
            if (e.shiftKey) snapActive = !snapActive;
            if (snapActive && allowSnap) {
                const chartState = this.getChartState();
                if (chartState) {
                    var snapped = this.engine.snapping.snap(x, y, chartState);
                    if (snapped && snapped.snapped) {
                        x = snapped.x;
                        y = snapped.y;
                    }
                }
            }
            return { x, y };
        }

        // Hit test (Phase 2: delegates to engine HitTestService)
        hitTest(pos, chartState) {
            if (!chartState) return null;
            var result = this.engine.hitTest.hitTest(pos, this.drawings, chartState);
            return result ? result.drawing : null;
        }

        // Full hit test with type info (Phase 2)
        hitTestFull(pos, chartState) {
            if (!chartState) return null;
            return this.engine.hitTest.hitTest(pos, this.drawings, chartState);
        }

        // Hit test for handles only (Phase 2)
        hitTestHandles(pos, drawing, chartState) {
            if (!drawing || !chartState) return null;
            return this.engine.hitTest.hitTestAnyHandle(pos, drawing, chartState);
        }

        // Pointer events are routed via handleContainer* in the capture phase.

        finishDrawing() {
            if (this.currentDrawing) {
                if (this.currentDrawing.isValid()) {
                    if (this.currentDrawing.coords.length < 2 && this.activeTool && this.activeTool.points > 1) {
                        return;
                    }
                    // Route through engine — creates DrawingModel, links it, pushes undo
                    var result = this.engine.finalizeDrawing(this.currentDrawing);
                    if (result && result.error) {
                        console.warn("Drawing validation failed:", result.error);
                    } else if (result && result.drawing) {
                        console.log("Drawing created via engine:", result.drawing.id);
                        var model = result.drawing;
                        var renderer = this.currentDrawing;
                        var type = model.type;
                        if (type === 'text' || type === 'text_note' || type === 'rich_anchored_text' || type === 'rich_callout' || type === 'callout') {
                            var chartState = this.getChartState();
                            var p1 = renderer.coords[0] ? chartState.coordToPixel(renderer.coords[0]) : null;
                            if (p1 && this.engine.textEditor) {
                                this.engine.textEditor.edit(model.id, renderer, p1);
                                if (typeof renderer._userEdited !== 'undefined') {
                                    renderer._userEdited = true;
                                }
                            }
                        }
                    } else {
                        console.warn("Drawing creation failed — unexpected result from engine.finalizeDrawing()");
                    }
                }
                this.currentDrawing = null;
            }
            this.isDrawing = false;
            this.updatePointerEvents();

            if (!this.stayMode) {
                this.enterSelectionMode();
            }
            if (this.mobileReticle) {
                this.mobileReticle.visible = false;
            }
            var chart = window.bigChart || window.chart;
            if (chart && typeof chart.clearCrosshairPosition === 'function') {
                try { chart.clearCrosshairPosition(); } catch(e) {}
            }
            this._justFinishedDrawing = true;
            this.redraw();
        }

        // Cancel the current in-progress drawing (but keep the tool selected)
        cancelDrawing() {
            this.isDrawing = false;
            this.currentDrawing = null;
            this._justFinishedDrawing = false;
            this.updatePointerEvents();
            if (this.mobileReticle) {
                this.mobileReticle.visible = false;
            }
            var chart = window.bigChart || window.chart;
            if (chart && typeof chart.clearCrosshairPosition === 'function') {
                try { chart.clearCrosshairPosition(); } catch(e) {}
            }
            this.redraw();
        }

        // Phase 1.5: Cancel any drag in progress and reset renderer from model
        cancelDrag() {
            if (this.selectedDrawing && this.dragStartCoords) {
                this.selectedDrawing.coords = JSON.parse(JSON.stringify(this.dragStartCoords));
            }
            this.isDraggingHandle = false;
            this.draggedHandle = null;
            this.dragDrawing = null;
            this.isDraggingDrawing = false;
            this.dragStartCoords = null;
            this._lastDragPos = null;
            this._justFinishedDrawing = false;
            this.updatePointerEvents();
        }

        // Recompute handle drag from a stored pixel position (used during auto-scroll)
        _updateDragFromPos(pos) {
            var chartState = this.getChartState();
            if (!chartState || !this.dragDrawing) return;
            var coord = chartState.pixelToCoord(pos.x, pos.y);
            if (coord && coord.price !== null && coord.price !== undefined) {
                if (typeof this.dragDrawing.updateHandle === 'function') {
                    var handleArg = (this.draggedHandle && this.draggedHandle.hitType === 'edge')
                        ? this.draggedHandle.handleLabel
                        : this.draggedHandle;
                    this.dragDrawing.updateHandle(handleArg, coord.price, chartState, pos.x);
                }
            }
        }

        // Recompute body drag from a stored pixel position (used during auto-scroll)
        _updateBodyDragFromPos(pos) {
            if (!this.selectedDrawing || !this.dragStartCoords) return;
            var chartState = this.getChartState();
            if (!chartState) return;
            var startCoord = chartState.pixelToCoord(this.containerMouseDownPos.x, this.containerMouseDownPos.y);
            var currentCoord = chartState.pixelToCoord(pos.x, pos.y);
            if (!startCoord || !currentCoord) {
                return;
            }
            var dl = currentCoord.logical - startCoord.logical;
            var dp = currentCoord.price - startCoord.price;
            if (Math.abs(dl) < 0.0001 && Math.abs(dp) < 0.0001) {
                return;
            }
            // PHASE A (found by real-browser drag QA): whole-object drag runs on
            // every pointermove and previously rebuilt coords as {logical,price}
            // only, dropping canonical time for the entire duration of the drag
            // and leaving it dropped afterward too (syncAndCapture on pointerup
            // updates model.points, not renderer.coords). This was the one
            // coord-mutation site inconsistent with every other one (handle
            // drag, resize, snap, translate) once Phase A closed those. A rigid
            // translate shifts logical by `dl` for every point, so each point's
            // time is re-derived from ITS OWN shifted logical -- not copied
            // from the old time, which would be stale at the new position.
            var _cmDrag = window.coordinateMapper;
            this.selectedDrawing.coords = this.dragStartCoords.map(function(c) {
                var newLogical = c.logical + dl;
                var t = null;
                if (_cmDrag && typeof _cmDrag.logicalToTime === 'function') {
                    var r = _cmDrag.logicalToTime(newLogical);
                    if (r !== null && r !== undefined && !isNaN(r)) t = r;
                }
                return { logical: newLogical, price: c.price + dp, time: t };
            });
            if (this.selectedDrawing instanceof LongPosition || this.selectedDrawing instanceof ShortPosition) {
                if (this.dragStartTargetPrice !== undefined && this.dragStartStopPrice !== undefined && this.dragStartEntryPrice !== undefined) {
                    this.selectedDrawing.targetPrice = this.dragStartTargetPrice + dp;
                    this.selectedDrawing.stopPrice = this.dragStartStopPrice + dp;
                    this.selectedDrawing.entryPrice = this.dragStartEntryPrice + dp;
                }
            }
            if (typeof this.selectedDrawing._invalidateCache === 'function') {
                this.selectedDrawing._invalidateCache();
            }
            this.redraw();
        }

        // Fully reset (e.g. ESC key)
        resetToDefault() {
            this.cancelDrag();
            this.isDrawing = false;
            this.currentDrawing = null;
            this.activeTool = null;
            this.enterSelectionMode(); // Default state is now Selection Mode (Interactive)
        }

        // Enter selection mode (allows clicking on drawings to select them)
        enterSelectionMode() {
            this.isDrawing = false;
            this.currentDrawing = null;
            this.activeTool = null;
            if (this.mobileReticle) {
                this.mobileReticle.visible = false;
            }
            var chart = window.bigChart || window.chart;
            if (chart && typeof chart.clearCrosshairPosition === 'function') {
                try { chart.clearCrosshairPosition(); } catch(e) {}
            }
            if (chart && typeof chart.applyOptions === 'function') {
                try {
                    chart.applyOptions({
                        crosshair: {
                            vertLine: { color: '#758696', labelVisible: true },
                            horzLine: { color: '#758696', labelVisible: true }
                        }
                    });
                } catch(e) {}
            }
            const chartContainer = document.getElementById('chart-container');

            if (chartContainer) {
                chartContainer.classList.remove('drawing-mode-active');
                chartContainer.classList.add('selection-mode-active');
                chartContainer.style.cursor = '';
            }
            this.updatePointerEvents();
            this.updateUI();
        }

        // Set the selected drawing and show/hide the style panel accordingly
        _selectDrawing(d) {
            var current = this.selectedDrawing;
            var currentId = current ? (current.model ? current.model.id : null) : null;
            var newId = d ? (d.model ? d.model.id : null) : null;
            if (currentId !== newId) {
                if (d && d.model) {
                    this.engine.selection.select(d.model.id);
                } else {
                    this.engine.selection.clear();
                }
                // Style panel is disabled — never show it automatically
                if (this.stylePanel) {
                    this.stylePanel.hide();
                }
                this.updatePointerEvents();
                this.redraw();
            }
        }

        // Delete only the selected drawing, then return to chart mode
        deleteSelected() {
            var sel = this.selectedDrawing;
            if (sel) {
                if (sel.locked) return;
                if (sel.model) {
                    this.engine.deleteDrawing(sel.model.id);
                } else {
                    var idx = this.drawings.indexOf(sel);
                    if (idx > -1) { this.drawings.splice(idx, 1); }
                    this.saveDrawings();
                }
                this._selectDrawing(null);
                this.redraw();
                this.enterSelectionMode();
            }
        }

        clearAll() {
            // Locked drawings are preserved
            var before = this.engine.undoManager.isRestoring ? null : this.drawings.map(function(d) { return d.model ? d.model.toJSON() : null; }).filter(Boolean);
            this.drawings = this.drawings.filter(function(d) { return d.locked; });
            this.saveDrawings();
            if (before && before.length > 0) {
                this.engine.undoManager.pushCommand('delete', before, []);
            }
            this._selectDrawing(null);
            this.cancelDrawing();
            this.redraw();
        }

        // --- Rendering (Phase 2) ---

        redraw() {
            if (!this.ctx) return;
            var dpr = window.devicePixelRatio || 1;
            var dims = BaseDrawing.prototype._getPlotDimensions(this.canvas);
            var plotWidth = dims.plotWidth;
            var plotHeight = dims.plotHeight;

            this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            this.ctx.clearRect(0, 0, this.canvas.width / dpr, this.canvas.height / dpr);

            if (this.hidden) return;

            const chartState = this.getChartState();
            if (!chartState) return;

            // Clip all drawing renders strictly within the candle chart plotting area (never overflow onto right Y-axis price scale)
            this.ctx.save();
            this.ctx.beginPath();
            this.ctx.rect(0, 0, plotWidth, plotHeight);
            this.ctx.clip();

            // Update engine canvas dimensions
            this.engine.setCanvas(this.canvas, dpr);

            // Use engine render pipeline (zIndex sort, viewport culling, DPR scaling)
            var selectedId = this.selectedDrawing ? (this.selectedDrawing.model ? this.selectedDrawing.model.id : null) : null;
            var hoveredId = this.hoveredDrawing ? (this.hoveredDrawing.model ? this.hoveredDrawing.model.id : null) : null;
            this.engine.render(this.ctx, chartState, selectedId, hoveredId);

            // Draw in-progress drawing on top
            if (this.currentDrawing) {
                this.currentDrawing.draw(this.ctx, chartState, false, false);
            }

            // Draw Mobile Precision Crosshair Reticle & Wick/Body Snap Indicator
            if (this.mobileReticle && this.mobileReticle.visible && this.activeTool && this.activeTool.type !== 'cursor') {
                this._drawMobileReticle(this.ctx, chartState, plotWidth, plotHeight);
            }

            // Draw marquee rectangle if active
            var marquee = this.engine.getMarqueeRect();
            if (marquee) {
                this.ctx.save();
                this.ctx.strokeStyle = '#2962ff';
                this.ctx.lineWidth = 1;
                this.ctx.setLineDash([4, 4]);
                this.ctx.fillStyle = 'rgba(41, 98, 255, 0.1)';
                var mr = marquee;
                this.ctx.fillRect(mr.left, mr.top, mr.right - mr.left, mr.bottom - mr.top);
                this.ctx.strokeRect(mr.left, mr.top, mr.right - mr.left, mr.bottom - mr.top);
                this.ctx.setLineDash([]);
                this.ctx.restore();
            }

            this.ctx.restore();

            // Phase 2: Render axis badges directly on the Y-Axis price scale and X-Axis time scale (unclipped)
            var allDrawings = this.drawings || [];
            for (var bi = 0; bi < allDrawings.length; bi++) {
                var d = allDrawings[bi];
                if (!d) continue;
                var dId = d.model ? d.model.id : null;
                var isSel = !!(dId && (dId === selectedId || (this.engine.selection && this.engine.selection.isSelected(dId))));
                var isHov = !!(dId && dId === hoveredId);
                if (typeof d.drawAxisBadges === 'function') {
                    d.drawAxisBadges(this.ctx, chartState, isSel, isHov);
                }
            }
            if (this.currentDrawing && typeof this.currentDrawing.drawAxisBadges === 'function') {
                this.currentDrawing.drawAxisBadges(this.ctx, chartState, true, false);
            }
        }

        logDrawingState(context) {
            const activeToolKey = this.activeTool ? this.activeTool.key : 'null';
            const activeToolGroup = this.activeTool ? this.activeTool.group : 'null';
            const isDrawing = this.isDrawing;
            const currentDrawingName = this.currentDrawing ? this.currentDrawing.constructor.name : 'null';
            const canvasCursor = this.canvas ? this.canvas.style.cursor : 'null';
            const canvasPointerEvents = this.canvas ? this.canvas.style.pointerEvents : 'null';
            const containerPointerEvents = this.container ? this.container.style.pointerEvents : 'null';
            
            var chartContainer = document.getElementById('chart-container');
            const hasDrawingModeActive = chartContainer ? chartContainer.classList.contains('drawing-mode-active') : false;
            const hasSelectionModeActive = chartContainer ? chartContainer.classList.contains('selection-mode-active') : false;
            
            var visibleSubmenus = [];
            document.querySelectorAll('.drawing-submenu.visible').forEach(el => {
                visibleSubmenus.push(el.id);
            });
            
            console.log(`[STATE_TRACE][${context}] ` +
                `activeTool: ${activeToolKey} (${activeToolGroup}) | ` +
                `isDrawing: ${isDrawing} | ` +
                `currentDrawing: ${currentDrawingName} | ` +
                `cursor: ${canvasCursor} | ` +
                `pointer-events (canvas/container): ${canvasPointerEvents}/${containerPointerEvents} | ` +
                `drawing-mode-active: ${hasDrawingModeActive} | ` +
                `selection-mode-active: ${hasSelectionModeActive} | ` +
                `visibleSubmenus: [${visibleSubmenus.join(', ')}]`
            );
        }

        // --- Submenu & UI ---

        closeAllSubmenus() {
            document.querySelectorAll('.drawing-submenu').forEach(el => {
                el.classList.remove('visible');
            });
        }

        updateUI() {
            if (window.ToolbarConfig && window.ToolbarConfig.store) {
                window.ToolbarConfig.store.setActiveTool(this.activeTool ? this.activeTool.key : null);
            }
            document.querySelectorAll('.toolbar-item, .submenu-item, .fav-tool-btn').forEach(el => {
                el.removeAttribute('data-active');
                el.classList.remove('active');
            });

            if (this.activeTool) {
                const key = this.activeTool.key;
                var subItem = null;
                var allItems = document.querySelectorAll('.submenu-item');
                for (var i = 0; i < allItems.length; i++) {
                    var oc = allItems[i].getAttribute('onclick') || '';
                    if (oc.indexOf("'" + key + "'") !== -1 || oc.indexOf('"' + key + '"') !== -1) {
                        subItem = allItems[i];
                        break;
                    }
                }
                if (subItem) subItem.classList.add('active');

                if (this.activeTool.group) {
                    var groupEl = document.querySelector('.toolbar-item[data-tool-group="' + this.activeTool.group + '"]');
                    if (groupEl) {
                        groupEl.setAttribute('data-active', 'true');
                    }
                }
            }
        }
    }

    // --- Undo / Redo ---
    // Phase 1.5: Legacy UndoRedoManager REMOVED.
    // All undo/redo goes through engine.undoManager (command pattern in drawing-core.js).
    // Engine undo/redo handlers in DOM keydown event call engine.undo()/engine.redo().

    // --- Drawing Style Defaults ---
    // Each key matches a class constructor name.
    // Used by BaseDrawing to initialise per-drawing style.
    const DrawingStyleDefaults = {
        default:            { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        TrendLine:          { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 11 },
        Ray:                { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 11 },
        ExtendedLine:       { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 11 },
        InfoLine:           { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 11 },
        TrendAngle:         { color: '#4caf50', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 11, extendLeft: false, extendRight: false },
        HorizontalLine:     { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        HorizontalRay:      { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        VerticalLine:       { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        CrossLine:          { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        ParallelChannel:    { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.1, showLabel: true, fontSize: 11, extendLeft: false, extendRight: false },
        RegressionTrend:    { color: '#2962ff', lowerColor: '#f23645', centerColor: '#2962ff', width: 2, opacity: 1, lineDash: [], upFillColor: '#2962ff', downFillColor: '#f23645', fillOpacity: 0.15, showLabel: true, fontSize: 11, extendLeft: true, extendRight: true, upperDeviation: 2, lowerDeviation: 2, showUpperDeviation: true, showLowerDeviation: true, source: 'close', showPearsonR: true },
        FlatTopChannel:     { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.1, showLabel: true, fontSize: 11, extendLeft: false, extendRight: false },
        FlatBottomChannel:  { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.1, showLabel: true, fontSize: 11, extendLeft: false, extendRight: false },
        DisjointChannel:    { color: '#089981', width: 2,   opacity: 1,   lineDash: [], fillColor: '#089981', fillOpacity: 0.18, showLabel: true, fontSize: 11, extendLeft: false, extendRight: false },
        Pitchfork:          { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.08, showLabel: true, fontSize: 11, showHandle: true, extendRight: true, medianColor: '#089981', medianWidth: 2, medianOpacity: 1, medianLineDash: [], tineColor: '#2962ff', upperTineColor: '#2962ff', lowerTineColor: '#F44336', tineWidth: 1.5, tineOpacity: 0.7, tineLineDash: [] },
        SchiffPitchfork:    { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#78aaff', fillOpacity: 0.18, showLabel: false, fontSize: 11, showHandle: true, extendRight: true, medianColor: '#F44336', medianWidth: 2, medianOpacity: 1, medianLineDash: [], tineColor: '#2196F3', upperTineColor: '#2196F3', lowerTineColor: '#089981', tineWidth: 2, tineOpacity: 0.8, tineLineDash: [], fillInnerColor: '#6edcdc', fillOuterColor: '#78aaff', constructionColor: '#F44336' },
        ModifiedSchiffPitchfork: { color: '#2196F3', width: 2,   opacity: 1,   lineDash: [], fillColor: '#78aaff', fillOpacity: 0.18, showLabel: false, fontSize: 11, showHandle: true, extendRight: true, medianColor: '#F44336', medianWidth: 2, medianOpacity: 1, medianLineDash: [], tineColor: '#2196F3', upperTineColor: '#2196F3', lowerTineColor: '#089981', tineWidth: 2, tineOpacity: 0.8, tineLineDash: [], fillInnerColor: '#6edcdc', fillOuterColor: '#78aaff', constructionColor: '#F44336' },
        InsidePitchfork:    { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#78aaff', fillOpacity: 0.18, showLabel: false, fontSize: 11, showHandle: true, extendRight: true, medianColor: '#F44336', medianWidth: 2, medianOpacity: 1, medianLineDash: [], tineColor: '#2196F3', upperTineColor: '#2196F3', lowerTineColor: '#089981', tineWidth: 2, tineOpacity: 0.8, tineLineDash: [], fillInnerColor: '#6edcdc', fillOuterColor: '#78aaff', constructionColor: '#F44336' },
        Rectangle:          { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.1, showLabel: true, fontSize: 11 },
        Circle:             { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.1, showLabel: true, fontSize: 11 },
        Ellipse:            { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.1, showLabel: true, fontSize: 11 },
        TriangleShape:      { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: '#2962ff', fillOpacity: 0.1, showLabel: true, fontSize: 11 },
        PathDrawing:        { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        BrushDrawing:       { color: '#2962ff', width: 4,   opacity: 0.5, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        HighlighterDrawing: { color: '#2962ff', width: 18,  opacity: 0.35,lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        TextDrawing:        { color: '#d1d4dc', width: 0,   opacity: 1,   lineDash: [], fillOpacity: 0, showLabel: true, fontSize: 16 },
        PriceLabel:         { color: '#37474f', width: 0,   opacity: 1,   lineDash: [], fillOpacity: 0, showLabel: true, fontSize: 13 },
        ArrowMarker:        { color: '#2962ff', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        FibRetracement:     { color: '#787b86', width: 1,   opacity: 0.8, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11, levelColors: {0:'#787b86',0.236:'#f44336',0.382:'#ff9800',0.5:'#ffc107',0.618:'#4caf50',0.786:'#00bcd4',1:'#787b86',1.618:'#2196f3',2.618:'#f44336',3.618:'#9c27b0',4.236:'#e91e63'} },
        FibExtension:       { color: '#787b86', width: 1,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11, levelColors: {0:'#787b86',0.236:'#ff5722',0.382:'#ff9800',0.5:'#ffc107',0.618:'#4caf50',0.786:'#00bcd4',1:'#787b86',1.272:'#2196f3',1.414:'#2196f3',1.618:'#2196f3',2:'#f44336',2.618:'#f44336',3.618:'#9c27b0',4.236:'#e91e63'} },
        FibChannel:         { color: '#2962ff', width: 1,   opacity: 0.8, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11, levelColors: {0:'#787b86',0.236:'#f44336',0.382:'#ff9800',0.5:'#ffc107',0.618:'#4caf50',0.786:'#00bcd4',1:'#787b86',1.618:'#2196f3',2.618:'#f44336',3.618:'#9c27b0',4.236:'#e91e63'} },
        FibFan:             { color: '#2962ff', width: 1,   opacity: 0.8, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        FibTimeZone:        { color: '#2196f3', width: 1,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 10 },
        FibCircles:         { color: '#787b86', width: 1,   opacity: 0.8, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11, levelColors: {0.236:'#f44336',0.382:'#ff9800',0.5:'#ffc107',0.618:'#4caf50',0.786:'#00bcd4',1:'#787b86',1.618:'#2196f3',2.618:'#9c27b0',3.618:'#e91e63',4.236:'#f44336'} },
        FibArcs:            { color: '#2962ff', width: 1,   opacity: 0.8, lineDash: [], fillColor: '#2962ff', fillOpacity: 0.05, showLabel: true, fontSize: 11 },
        FibSpeedResistance: { color: '#787b86', width: 1,   opacity: 0.8, lineDash: [4,4], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11, levelColors: {0:'#787b86',0.25:'#ff9800',0.382:'#00bcd4',0.5:'#4caf50',0.618:'#009688',0.75:'#2196f3',1:'#787b86'} },
        FibSpiral:          { color: '#00BCD4', width: 2,   opacity: 1, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 10 },
        FibWedge:           { color: '#2962ff', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#2962ff', fillOpacity: 0.05, showLabel: true, fontSize: 11 },
        Pitchfan:           { color: '#2962ff', width: 1,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        GannBox:            { color: '#2196f3', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#2196f3', fillOpacity: 0.04, showLabel: true, fontSize: 11, gridColor: '#90a4ae', diagonalColor: '#90a4ae' },
        GannFan:            { color: '#00bcd4', width: 1.5, opacity: 0.85, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 10 },
        GannSquare:         { color: '#555', width: 2, opacity: 1, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 9, gridColor: '#90a4ae', diagonalColor: '#90a4ae' },
        GannSquareFixed:    { color: '#555', width: 2, opacity: 1, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 9, gridColor: '#90a4ae', diagonalColor: '#90a4ae' },
        GannFan:            { color: '#2196f3', width: 1,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11, fanColors: ['#ff5722','#ff9800','#ffc107','#4caf50','#2196f3','#4caf50','#ffc107','#ff9800','#ff5722'] },
        LongPosition:       { color: '#26a69a', width: 1.5, opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        ShortPosition:      { color: '#ef5350', width: 1.5, opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        PriceRange:         { color: '#9c27b0', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        DateRange:          { color: '#ff5722', width: 2,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        Measure:            { color: '#00bcd4', width: 2,   opacity: 1,   lineDash: [4,4], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        // Elliott Wave (Phase 3.6)
        ImpulseWave:        { color: '#26a69a', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        CorrectiveWave:     { color: '#ef5350', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        ElliottTriangle:    { color: '#2196f3', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        ElliottDoubleCombo: { color: '#ff9800', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        ElliottTripleCombo: { color: '#ff9800', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        ElliottFlat:        { color: '#4caf50', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        ElliottZigZag:      { color: '#f44336', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        ElliottCombination: { color: '#787b86', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 12 },
        // Pattern Tools (Phase 3.7)
        Gartley:            { color: '#2196f3', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        Butterfly:          { color: '#9c27b0', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        Bat:                { color: '#ff9800', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        Crab:               { color: '#f44336', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        DeepCrab:           { color: '#e91e63', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        Shark:              { color: '#00bcd4', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        Cypher:             { color: '#4caf50', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        Abcd:               { color: '#ff5722', width: 2,   opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        HeadAndShoulders:   { color: '#2962ff', width: 1.5, opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        InverseHeadAndShoulders: { color: '#2962ff', width: 1.5, opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        DoubleTop:          { color: '#ef5350', width: 1.5, opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        DoubleBottom:       { color: '#26a69a', width: 1.5, opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        TripleTop:          { color: '#ef5350', width: 1.5, opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        TripleBottom:       { color: '#26a69a', width: 1.5, opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: true, fontSize: 11 },
        AscendingTriangle:  { color: '#4caf50', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#4caf50', fillOpacity: 0.06, showLabel: true, fontSize: 11 },
        DescendingTriangle: { color: '#f44336', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#f44336', fillOpacity: 0.06, showLabel: true, fontSize: 11 },
        SymmetricalTriangle:{ color: '#2196f3', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#2196f3', fillOpacity: 0.06, showLabel: true, fontSize: 11 },
        ExpandingTriangle:  { color: '#ff9800', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#ff9800', fillOpacity: 0.06, showLabel: true, fontSize: 11 },
        RisingWedge:        { color: '#4caf50', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#4caf50', fillOpacity: 0.05, showLabel: true, fontSize: 11 },
        FallingWedge:       { color: '#f44336', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#f44336', fillOpacity: 0.05, showLabel: true, fontSize: 11 },
        AscendingChannel:   { color: '#4caf50', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#4caf50', fillOpacity: 0.05, showLabel: true, fontSize: 11 },
        DescendingChannel:  { color: '#f44336', width: 1.5, opacity: 0.8, lineDash: [], fillColor: '#f44336', fillOpacity: 0.05, showLabel: true, fontSize: 11 },
        // Phase 3.9: Rich Objects
        TextNote:           { color: '#d1d4dc', width: 0,   opacity: 1,   lineDash: [], fillColor: '#2a2e39', fillOpacity: 0.9, showLabel: false, fontSize: 14 },
        AnchoredText:       { color: '#d1d4dc', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 14 },
        Callout:            { color: '#d1d4dc', width: 0,   opacity: 1,   lineDash: [], fillColor: '#2a2e39', fillOpacity: 0.9, showLabel: false, fontSize: 13 },
        Balloon:            { color: '#d1d4dc', width: 0,   opacity: 1,   lineDash: [], fillColor: '#2a2e39', fillOpacity: 0.9, showLabel: false, fontSize: 13 },
        ArrowLabel:         { color: '#2962ff', width: 1.5, opacity: 0.9, lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 13 },
        EmojiDrawing:       { color: null,      width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 24 },
        IconDrawing:        { color: '#787b86', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        SymbolDrawing:      { color: '#ff9800', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        ImageDrawing:       { color: '#787b86', width: 1,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        WatermarkDrawing:   { color: '#787b86', width: 0,   opacity: 0.15,lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        LogoDrawing:        { color: '#787b86', width: 1,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerBuy:         { color: '#26a69a', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerSell:        { color: '#ef5350', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerLong:        { color: '#26a69a', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerShort:       { color: '#ef5350', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerTarget:      { color: '#4caf50', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerStop:        { color: '#f44336', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerStar:        { color: '#ff9800', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerPin:         { color: '#f44336', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerCheck:       { color: '#4caf50', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
        StickerWarning:     { color: '#ff9800', width: 0,   opacity: 1,   lineDash: [], fillColor: null, fillOpacity: 0, showLabel: false, fontSize: 0 },
    };

    // --- Drawing Style Panel ---
    // Floating UI panel that appears when a drawing is selected.
    // Uses native 'change' events (fires on blur/commit) rather than 'input',
    // so undo captures are not spammed during slider drags or color-picker scrubbing.

    // Note: SchiffPitchfork/ModifiedSchiffPitchfork/InsidePitchfork are
    // intentionally excluded — their fill uses two independent colors
    // (fillInnerColor/fillOuterColor), so a single generic fill-color control
    // wouldn't map to anything real. Only classic Pitchfork still uses a
    // single fillColor.
    var _spFillTypes = { Rectangle: 1, ParallelChannel: 1, FlatTopChannel: 1, FlatBottomChannel: 1, DisjointChannel: 1, RegressionTrend: 1, Pitchfork: 1 };
    var _spTextTypes = { TextDrawing: 1, PriceLabel: 1 };

    class DrawingStylePanel {
        constructor(tm) {
            this.tm = tm;
            this._visible = false;
            this._build();
            this._bindEvents();
        }

        _build() {
            var el = document.createElement('div');
            el.id = 'drawing-style-panel';
            el.style.display = 'none';
            el.innerHTML =
                '<div class="sp-header">Style</div>' +
                '<div class="sp-row">' +
                    '<label class="sp-label">Color</label>' +
                    '<input type="color" class="sp-color" id="sp-color">' +
                    '<label class="sp-label" style="min-width:auto;margin-left:4px;">Opacity</label>' +
                    '<input type="range" class="sp-opacity" id="sp-opacity" min="0.05" max="1" step="0.05">' +
                    '<span class="sp-opacity-val" id="sp-opacity-val">1.00</span>' +
                '</div>' +
                '<div class="sp-row">' +
                    '<label class="sp-label">Width</label>' +
                    '<div class="sp-width-group" id="sp-width-group">' +
                        '<button data-w="1">1</button>' +
                        '<button data-w="2">2</button>' +
                        '<button data-w="3">3</button>' +
                        '<button data-w="4">4</button>' +
                        '<button data-w="5">5</button>' +
                    '</div>' +
                    '<div class="sp-style-group" id="sp-style-group">' +
                        '<button data-dash="solid" title="Solid">\u2014</button>' +
                        '<button data-dash="dashed" title="Dashed">- -</button>' +
                        '<button data-dash="dotted" title="Dotted">\u00b7\u00b7\u00b7</button>' +
                    '</div>' +
                '</div>' +
                '<div class="sp-row sp-fill-row" id="sp-fill-row">' +
                    '<label class="sp-label">Fill</label>' +
                    '<input type="color" class="sp-fill-color" id="sp-fill-color">' +
                    '<input type="range" class="sp-fill-opacity" id="sp-fill-opacity" min="0" max="0.5" step="0.05">' +
                '</div>' +
                '<div class="sp-row sp-font-row" id="sp-font-row">' +
                    '<label class="sp-label">Font</label>' +
                    '<input type="number" class="sp-font-size" id="sp-font-size" min="8" max="120" step="1" value="11">' +
                '</div>' +
                '<div class="sp-row sp-emoji-row" id="sp-emoji-row" style="display:none;align-items:center;gap:6px;">' +
                    '<label class="sp-label">Emoji</label>' +
                    '<button class="sp-emoji-btn" id="sp-emoji-btn" style="background:#2a2e39;color:#d1d4dc;border:1px solid #363a45;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:18px;min-width:38px;line-height:1;" title="Click to change emoji">📈</button>' +
                    '<label class="sp-label" style="min-width:auto;margin-left:4px;">Size</label>' +
                    '<input type="range" class="sp-emoji-size" id="sp-emoji-size" min="14" max="140" step="2" value="28" style="flex:1;min-width:60px;">' +
                    '<span class="sp-emoji-size-val" id="sp-emoji-size-val" style="font-size:11px;color:#d1d4dc;min-width:32px;text-align:right;">28px</span>' +
                '</div>' +
                '<div class="sp-row sp-emoji-presets-row" id="sp-emoji-presets-row" style="display:none;align-items:center;gap:4px;">' +
                    '<label class="sp-label">Preset</label>' +
                    '<div class="sp-width-group" id="sp-emoji-preset-group">' +
                        '<button data-size="20" title="Small (20px)">S</button>' +
                        '<button data-size="32" title="Medium (32px)">M</button>' +
                        '<button data-size="48" title="Large (48px)">L</button>' +
                        '<button data-size="64" title="Extra Large (64px)">XL</button>' +
                        '<button data-size="96" title="Huge (96px)">2X</button>' +
                    '</div>' +
                '</div>' +
                '<div class="sp-divider sp-regression-row" id="sp-regression-divider"></div>' +
                '<div class="sp-row sp-regression-row" id="sp-regression-source-row">' +
                    '<label class="sp-label">Source</label>' +
                    '<select id="sp-reg-source">' +
                        '<option value="close">Close</option>' +
                        '<option value="open">Open</option>' +
                        '<option value="high">High</option>' +
                        '<option value="low">Low</option>' +
                        '<option value="hl2">HL2</option>' +
                        '<option value="hlc3">HLC3</option>' +
                        '<option value="ohlc4">OHLC4</option>' +
                    '</select>' +
                '</div>' +
                '<div class="sp-row sp-toggles sp-regression-row" id="sp-regression-dev-row">' +
                    '<label class="sp-label" style="min-width:auto;">Upper Dev</label>' +
                    '<input type="number" class="sp-num" id="sp-reg-upper-dev" min="0" step="0.1">' +
                    '<label><input type="checkbox" id="sp-reg-show-upper"> Show</label>' +
                    '<label class="sp-label" style="min-width:auto;margin-left:6px;">Lower Dev</label>' +
                    '<input type="number" class="sp-num" id="sp-reg-lower-dev" min="0" step="0.1">' +
                    '<label><input type="checkbox" id="sp-reg-show-lower"> Show</label>' +
                '</div>' +
                '<div class="sp-row sp-toggles sp-regression-row" id="sp-regression-r-row">' +
                    '<label><input type="checkbox" id="sp-reg-show-r"> Show Pearson’s R</label>' +
                '</div>' +
                '<div class="sp-divider sp-pitchfork-row" id="sp-pitchfork-divider"></div>' +
                '<div class="sp-row sp-pitchfork-row" id="sp-pitchfork-color-row">' +
                    '<label class="sp-label">Median</label>' +
                    '<input type="color" id="sp-pf-median-color">' +
                    '<label class="sp-label" style="margin-left:8px;">Upper</label>' +
                    '<input type="color" id="sp-pf-upper-tine-color">' +
                    '<label class="sp-label" style="margin-left:8px;">Lower</label>' +
                    '<input type="color" id="sp-pf-lower-tine-color">' +
                '</div>' +
                '<div class="sp-divider"></div>' +
                '<div class="sp-row sp-toggles">' +
                    '<label><input type="checkbox" id="sp-show-label"> Label</label>' +
                    '<label><input type="checkbox" id="sp-locked"> Locked</label>' +
                    '<label><input type="checkbox" id="sp-hidden"> Hidden</label>' +
                '</div>';
            document.body.appendChild(el);
            this._el = el;
        }

        _bindEvents() {
            var self = this;

            function getStyle() {
                return self.tm.selectedDrawing ? self.tm.selectedDrawing.style : null;
            }
            function getDrawing() {
                return self.tm.selectedDrawing;
            }

            // Phase 1.5: use engine undo/save instead of legacy capture
            function setAndSave(updateFn) {
                var d = getDrawing();
                if (!d) return;
                var engine = self.tm.engine;
                // Capture undo state if model exists
                var before = d.model ? d.model.toJSON() : null;
                updateFn(d);
                // Sync renderer → model
                if (d.model && d.syncToModel) d.syncToModel();
                if (d.model && engine) {
                    engine.undoManager.pushCommand('restyle', before, d.model.toJSON());
                    engine.markDirty();
                } else {
                    self.tm.saveDrawings();
                }
                self.tm.redraw();
            }

            // Color (change = user committed the color, not scrubbing)
            this._on('#sp-color', 'change', function () {
                var val = this.value;
                setAndSave(function (d) { d.style.color = val; });
            });

            // Opacity slider
            this._on('#sp-opacity', 'change', function () {
                var val = parseFloat(this.value);
                setAndSave(function (d) { d.style.opacity = val; });
                document.getElementById('sp-opacity-val').textContent = val.toFixed(2);
            });
            // Also update the display value on input (but DON'T save/undo during drag)
            this._on('#sp-opacity', 'input', function () {
                document.getElementById('sp-opacity-val').textContent = parseFloat(this.value).toFixed(2);
            });

            // Width buttons
            this._on('#sp-width-group', 'click', function (e) {
                var btn = e.target.closest('button[data-w]');
                if (!btn) return;
                var w = parseInt(btn.getAttribute('data-w'), 10);
                setAndSave(function (d) { d.style.width = w; });
                self._updateWidthActive(w);
            });

            // Line style buttons
            this._on('#sp-style-group', 'click', function (e) {
                var btn = e.target.closest('button[data-dash]');
                if (!btn) return;
                var dashMap = { solid: [], dashed: [6, 4], dotted: [2, 3] };
                var style = btn.getAttribute('data-dash');
                setAndSave(function (d) { d.style.lineDash = dashMap[style] || []; });
                self._updateStyleActive(style);
            });

            // Fill color
            this._on('#sp-fill-color', 'change', function () {
                var val = this.value;
                setAndSave(function (d) { d.style.fillColor = val; });
            });

            // Fill opacity
            this._on('#sp-fill-opacity', 'change', function () {
                var val = parseFloat(this.value);
                setAndSave(function (d) { d.style.fillOpacity = val; });
            });

            // Font size (Text Drawings)
            this._on('#sp-font-size', 'change', function () {
                var val = parseInt(this.value, 10);
                if (isNaN(val) || val < 8) val = 8;
                if (val > 120) val = 120;
                this.value = val;
                setAndSave(function (d) { 
                    d.style.fontSize = val; 
                    if (d._invalidateCache) d._invalidateCache();
                });
            });

            // Emoji size slider
            this._on('#sp-emoji-size', 'change', function () {
                var val = parseInt(this.value, 10);
                if (isNaN(val) || val < 10) val = 10;
                setAndSave(function (d) {
                    d.style.fontSize = val;
                    if (d._invalidateCache) d._invalidateCache();
                });
                var valEl = document.getElementById('sp-emoji-size-val');
                if (valEl) valEl.textContent = val + 'px';
                self._updateEmojiPresetActive(val);
            });
            this._on('#sp-emoji-size', 'input', function () {
                var val = parseInt(this.value, 10);
                var valEl = document.getElementById('sp-emoji-size-val');
                if (valEl) valEl.textContent = val + 'px';
                var d = getDrawing();
                if (d) {
                    d.style.fontSize = val;
                    if (d._invalidateCache) d._invalidateCache();
                    self.tm.redraw();
                }
                self._updateEmojiPresetActive(val);
            });

            // Emoji preset buttons (S, M, L, XL, 2X)
            this._on('#sp-emoji-preset-group', 'click', function (e) {
                var btn = e.target.closest('button[data-size]');
                if (!btn) return;
                var size = parseInt(btn.getAttribute('data-size'), 10);
                if (isNaN(size)) return;
                setAndSave(function (d) {
                    d.style.fontSize = size;
                    if (d._invalidateCache) d._invalidateCache();
                });
                var slider = document.getElementById('sp-emoji-size');
                if (slider) slider.value = size;
                var valEl = document.getElementById('sp-emoji-size-val');
                if (valEl) valEl.textContent = size + 'px';
                self._updateEmojiPresetActive(size);
            });

            // Show label
            this._on('#sp-show-label', 'change', function () {
                var checked = this.checked;
                setAndSave(function (d) { d.style.showLabel = checked; });
            });

            // Emoji button clicked in Style Panel -> opens rich emoji picker
            this._on('#sp-emoji-btn', 'click', function (e) {
                e.stopPropagation();
                if (window.showEmojiPickerForStylePanel) {
                    window.showEmojiPickerForStylePanel(this);
                }
            });

            // Locked (per-drawing)
            this._on('#sp-locked', 'change', function () {
                var checked = this.checked;
                setAndSave(function (d) { d.locked = checked; });
            });

            // Hidden (per-drawing)
            this._on('#sp-hidden', 'change', function () {
                var checked = this.checked;
                setAndSave(function (d) { d.hidden = checked; });
            });

            // --- Regression Trend specific ---
            this._on('#sp-reg-source', 'change', function () {
                var val = this.value;
                setAndSave(function (d) { d.style.source = val; });
            });
            this._on('#sp-reg-upper-dev', 'change', function () {
                var val = parseFloat(this.value);
                if (isNaN(val) || val < 0) val = 0;
                this.value = val;
                setAndSave(function (d) { d.style.upperDeviation = val; });
            });
            this._on('#sp-reg-lower-dev', 'change', function () {
                var val = parseFloat(this.value);
                if (isNaN(val) || val < 0) val = 0;
                this.value = val;
                setAndSave(function (d) { d.style.lowerDeviation = val; });
            });
            this._on('#sp-reg-show-upper', 'change', function () {
                var checked = this.checked;
                setAndSave(function (d) { d.style.showUpperDeviation = checked; });
            });
            this._on('#sp-reg-show-lower', 'change', function () {
                var checked = this.checked;
                setAndSave(function (d) { d.style.showLowerDeviation = checked; });
            });
            this._on('#sp-reg-show-r', 'change', function () {
                var checked = this.checked;
                setAndSave(function (d) { d.style.showPearsonR = checked; });
            });

            // --- Pitchfork family specific ---
            this._on('#sp-pf-median-color', 'change', function () {
                var val = this.value;
                setAndSave(function (d) { d.style.medianColor = val; });
            });
            this._on('#sp-pf-upper-tine-color', 'change', function () {
                var val = this.value;
                setAndSave(function (d) { d.style.upperTineColor = val; });
            });
            this._on('#sp-pf-lower-tine-color', 'change', function () {
                var val = this.value;
                setAndSave(function (d) { d.style.lowerTineColor = val; });
            });
        }

        _on(selector, eventType, fn) {
            var el = this._el.querySelector(selector);
            if (el) {
                el.addEventListener(eventType, fn);
            } else {
                // Delegate from panel root
                this._el.addEventListener(eventType, function (e) {
                    var target = e.target.closest(selector);
                    if (target) fn.call(target, e);
                });
            }
        }

        _updateWidthActive(w) {
            this._el.querySelectorAll('#sp-width-group button').forEach(function (btn) {
                btn.classList.toggle('active', parseInt(btn.getAttribute('data-w'), 10) === w);
            });
        }

        _updateStyleActive(style) {
            this._el.querySelectorAll('#sp-style-group button').forEach(function (btn) {
                btn.classList.toggle('active', btn.getAttribute('data-dash') === style);
            });
        }

        _updateEmojiPresetActive(size) {
            var group = this._el.querySelector('#sp-emoji-preset-group');
            if (!group) return;
            group.querySelectorAll('button').forEach(function (btn) {
                var s = parseInt(btn.getAttribute('data-size'), 10);
                btn.classList.toggle('active', s === size);
            });
        }

        show(drawing) {
            var s = drawing.style;
            var cname = (drawing.constructor && drawing.constructor.name) || '';
            var isRegression = cname === 'RegressionTrend';
            var isPitchfork = cname === 'Pitchfork' || cname === 'SchiffPitchfork' ||
                cname === 'ModifiedSchiffPitchfork' || cname === 'InsidePitchfork';

            this._el.querySelectorAll('.sp-regression-row').forEach(function (row) {
                row.style.display = isRegression ? '' : 'none';
            });
            this._el.querySelectorAll('.sp-pitchfork-row').forEach(function (row) {
                row.style.display = isPitchfork ? '' : 'none';
            });
            if (isRegression) {
                var srcEl = document.getElementById('sp-reg-source');
                if (srcEl) srcEl.value = s.source || 'close';
                var upDevEl = document.getElementById('sp-reg-upper-dev');
                if (upDevEl) upDevEl.value = s.upperDeviation != null ? s.upperDeviation : 2;
                var lowDevEl = document.getElementById('sp-reg-lower-dev');
                if (lowDevEl) lowDevEl.value = s.lowerDeviation != null ? s.lowerDeviation : 2;
                var showUpEl = document.getElementById('sp-reg-show-upper');
                if (showUpEl) showUpEl.checked = s.showUpperDeviation !== false;
                var showLowEl = document.getElementById('sp-reg-show-lower');
                if (showLowEl) showLowEl.checked = s.showLowerDeviation !== false;
                var showREl = document.getElementById('sp-reg-show-r');
                if (showREl) showREl.checked = s.showPearsonR !== false;
            }
            if (isPitchfork) {
                var medEl = document.getElementById('sp-pf-median-color');
                if (medEl) medEl.value = s.medianColor || '#089981';
                var upperTineEl = document.getElementById('sp-pf-upper-tine-color');
                if (upperTineEl) upperTineEl.value = s.upperTineColor || s.tineColor || s.color || '#2196F3';
                var lowerTineEl = document.getElementById('sp-pf-lower-tine-color');
                if (lowerTineEl) lowerTineEl.value = s.lowerTineColor || '#F44336';
            }

            // Populate fields
            var isEmoji = cname === 'EmojiDrawing' || (drawing.getObjectDef && drawing.getObjectDef() && (drawing.getObjectDef().geometry === RichGeometry.EMOJI || (drawing.getObjectDef().defaults && drawing.getObjectDef().defaults.emoji)));

            var colorEl = document.getElementById('sp-color');
            var colorLabelEl = colorEl ? colorEl.previousElementSibling : null;
            if (colorEl) {
                colorEl.value = s.color || '#2962ff';
                colorEl.style.display = isEmoji ? 'none' : '';
            }
            if (colorLabelEl) colorLabelEl.style.display = isEmoji ? 'none' : '';

            var opacityEl = document.getElementById('sp-opacity');
            if (opacityEl) opacityEl.value = s.opacity != null ? s.opacity : 1;

            var opacityVal = document.getElementById('sp-opacity-val');
            if (opacityVal) opacityVal.textContent = (s.opacity != null ? s.opacity : 1).toFixed(2);

            this._updateWidthActive(s.width || 2);

            var dashStyle = 'solid';
            if (s.lineDash && s.lineDash.length > 0) {
                dashStyle = s.lineDash[0] >= 4 ? 'dashed' : 'dotted';
            }
            this._updateStyleActive(dashStyle);

            var widthGroupEl = document.getElementById('sp-width-group');
            var widthRowEl = widthGroupEl ? widthGroupEl.parentElement : null;
            if (widthRowEl) widthRowEl.style.display = isEmoji ? 'none' : 'flex';

            var fillRow = document.getElementById('sp-fill-row');
            if (fillRow) fillRow.style.display = _spFillTypes[cname] ? 'flex' : 'none';

            var fillColorEl = document.getElementById('sp-fill-color');
            if (fillColorEl && s.fillColor) fillColorEl.value = s.fillColor;

            var fillOpacityEl = document.getElementById('sp-fill-opacity');
            if (fillOpacityEl) fillOpacityEl.value = s.fillOpacity != null ? s.fillOpacity : 0;

            var fontRow = document.getElementById('sp-font-row');
            if (fontRow) fontRow.style.display = (_spTextTypes[cname] && !isEmoji) ? 'flex' : 'none';

            var fontSizeEl = document.getElementById('sp-font-size');
            if (fontSizeEl) fontSizeEl.value = s.fontSize || 11;

            var showLabelEl = document.getElementById('sp-show-label');
            if (showLabelEl) {
                showLabelEl.checked = s.showLabel !== false;
                if (showLabelEl.parentElement) showLabelEl.parentElement.style.display = isEmoji ? 'none' : '';
            }

            var lockedEl = document.getElementById('sp-locked');
            if (lockedEl) lockedEl.checked = drawing.locked || false;

            var hiddenEl = document.getElementById('sp-hidden');
            if (hiddenEl) hiddenEl.checked = drawing.hidden || false;

            var emojiRow = document.getElementById('sp-emoji-row');
            var emojiPresetsRow = document.getElementById('sp-emoji-presets-row');
            if (emojiRow) emojiRow.style.display = isEmoji ? 'flex' : 'none';
            if (emojiPresetsRow) emojiPresetsRow.style.display = isEmoji ? 'flex' : 'none';
            if (isEmoji) {
                var emojiBtn = document.getElementById('sp-emoji-btn');
                if (emojiBtn) {
                    var plainEmoji = (drawing.model && drawing.model.content && drawing.model.content.plain) || drawing.content || '📈';
                    if (typeof plainEmoji === 'object' && plainEmoji.plain) {
                        plainEmoji = plainEmoji.plain;
                    }
                    emojiBtn.textContent = typeof plainEmoji === 'string' ? plainEmoji : '📈';
                }
                var curEmojiSize = s.fontSize || 28;
                var emojiSlider = document.getElementById('sp-emoji-size');
                if (emojiSlider) emojiSlider.value = curEmojiSize;
                var emojiValEl = document.getElementById('sp-emoji-size-val');
                if (emojiValEl) emojiValEl.textContent = curEmojiSize + 'px';
                this._updateEmojiPresetActive(curEmojiSize);
            }

            // Position below the drawing toolbar, then clamp so it can't render off-screen
            this._el.style.display = 'block';
            var toolbar = document.querySelector('.drawing-toolbar');
            var top, left;
            if (toolbar) {
                var rect = toolbar.getBoundingClientRect();
                top = rect.bottom + 6;
                left = rect.left;
            } else {
                left = 12;
                top = 140;
            }
            var panelRect = this._el.getBoundingClientRect();
            var maxLeft = Math.max(8, window.innerWidth - panelRect.width - 8);
            var maxTop = Math.max(8, window.innerHeight - panelRect.height - 8);
            this._el.style.left = Math.min(left, maxLeft) + 'px';
            this._el.style.top = Math.min(top, maxTop) + 'px';

            this._visible = true;
        }

        hide() {
            if (!this._visible) return;
            this._visible = false;
            this._el.style.display = 'none';
        }
    }

    // --- Drawing Classes ---

    class BaseDrawing {
        constructor(startPos, chartState, options = {}) {
            this.coords = []; // Store Chart Coordinates (logical/price)
            if (chartState) {
                if (window.getSnappedAnchor) {
                    var snapInfo = window.getSnappedAnchor(startPos.x, startPos.y, chartState);
                    if (snapInfo) {
                        this.coords.push({ logical: snapInfo.logical, price: snapInfo.price, time: snapInfo.time });
                    }
                } else {
                    const coord = chartState.pixelToCoord(startPos.x, startPos.y);
                    if (coord && coord.logical != null && coord.price != null) {
                        this.coords.push(coord);
                    }
                }
            }
            this.options = options;
            this.currentPos = startPos;

            // Per-drawing lock/hide: lock prevents move/delete/edit,
            // hide skips rendering + hit testing.
            // Style edits are allowed even when locked (TradingView-compatible).
            this.locked = false;
            this.hidden = false;

            // Per-drawing style — merged from class default + any per-instance override.
            // Axis labels opt-in (only Ray, ExtendedLine, etc.)
            this.showAxisLabels = false;
            // The style panel updates these fields at runtime.
            this.normalizeStyle();

            // Phase 1.5: DrawingModel bridge — single source of truth for data
            this.model = null;
        }

        // Phase 1.5: Sync renderer state back to the DrawingModel
        syncToModel() {
            if (!this.model) return;
            // Use engine directly if available, else fallback to toolManager
            var eng = window._drawingEngine || (window.toolManager && window.toolManager.engine);
            if (eng) {
                eng.syncModel(this);
            } else {
                // Minimal inline sync
                this.model.points = _coordsToModelPoints(this.coords);
                this.model.locked = !!this.locked;
                this.model.hidden = !!this.hidden;
            }
        }

        normalizeStyle() {
            var defaults = DrawingStyleDefaults[this.constructor.name] || DrawingStyleDefaults.default;
            if (!this.style || typeof this.style !== 'object') {
                this.style = {};
            }
            for (var k in defaults) {
                if (this.style[k] === undefined) {
                    this.style[k] = defaults[k];
                }
            }
            this.locked = this.locked || false;
            this.hidden = this.hidden || false;
        }

        // Apply stroke style from this.style onto the canvas context.
        // Must be called in renderShape() before any stroke operations.
        applyStyle(ctx) {
            ctx.strokeStyle = this.style.color || '#2962ff';
            ctx.lineWidth = this.style.width || 2;
            ctx.globalAlpha = this.style.opacity != null ? this.style.opacity : 1;
            var dash = this.style.lineDash;
            ctx.setLineDash(Array.isArray(dash) ? dash : []);
        }

        // Apply fill style from this.style onto the canvas context.
        // Must be called in renderShape() before any fill operations.
        applyFillStyle(ctx) {
            if (this.style.fillColor) {
                ctx.fillStyle = this.style.fillColor;
                ctx.globalAlpha = this.style.fillOpacity != null ? this.style.fillOpacity : 0;
            }
        }

        begin() {
            // Lifecycle initialization hook
        }

        updatePreview(pos, chartState) {
            if (chartState && window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pos.x, pos.y, chartState);
                if (snapInfo) {
                    var px = chartState.coordToPixel ? chartState.coordToPixel({ logical: snapInfo.logical, price: snapInfo.price, time: snapInfo.time }) : pos;
                    this.currentPos = px || pos;
                    return;
                }
            }
            this.currentPos = pos;
        }

        commitPoint(pos, chartState) {
            if (chartState) {
                if (window.getSnappedAnchor) {
                    var snapInfo = window.getSnappedAnchor(pos.x, pos.y, chartState);
                    if (snapInfo) {
                        this.coords.push({ logical: snapInfo.logical, price: snapInfo.price, time: snapInfo.time });
                        return true;
                    }
                } else {
                    const coord = chartState.pixelToCoord(pos.x, pos.y);
                    if (coord && coord.logical != null && coord.price != null) {
                        this.coords.push(coord);
                        return true;
                    }
                }
            }
            return false;
        }

        isComplete() {
            var needed = this.pointCount || (this.options && this.options.points) || 2;
            return this.coords.length >= needed;
        }

        cancel() {
            // Cancel hook
        }

        finish() {
            // Finalize hook
        }

        addPoint(pos, chartState) {
            return this.commitPoint(pos, chartState);
        }

        update(pos, chartState) {
            this.updatePreview(pos, chartState);
        }

        isValid() { return this.coords.length > 0; }

        draw(ctx, chartState) { }

        // Helper to get pixels
        getPixels(chartState) {
            if (!chartState) return [];
            return this.coords.map(c => chartState.coordToPixel(c)).filter(Boolean);
        }

        // Translate all coords by a pixel delta (for drag-to-reposition)
        translate(dx, dy, chartState) {
            // Locked drawings cannot be repositioned
            if (this.locked) return;
            if (!chartState || !this.coords || this.coords.length === 0) return;
            this.coords = this.coords.map(c => {
                const pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                const newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }

        // Update a single anchor point when handle-dragged (used by engine interaction)
        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;
            if (idx < 0 || idx >= this.coords.length) return;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    this.coords[idx] = { logical: snapInfo.logical, price: snapInfo.price, time: snapInfo.time };
                    if (typeof this._invalidateCache === 'function') this._invalidateCache();
                    return;
                }
            }

            var logical = this.coords[idx].logical;
            var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
            if (mapper) {
                var newLogical = mapper(pixelX);
                if (newLogical != null) logical = newLogical;
            }
            this.coords[idx] = { logical: logical, price: newPrice };
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }

        // Phase 2: Anchor points for handle hit testing (override in subclasses)
        getAnchorPoints(pixels) {
            return pixels || [];
        }

        // Phase 2: Midpoints for handle hit testing (override in subclasses)
        getMidpoints(pixels) {
            return [];
        }

        // Phase 2: Fill shape polygon for hit testing (override in subclasses)
        getFillShape(pixels) { return null; }

        // Draw selection handles (overridden by subclasses; called by engine post-render pass)
        drawHandles(ctx, pixels, isSelected) {
            if (!pixels) return;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this.drawHandle(ctx, pixels[i], isSelected);
            }
        }

        _getPlotDimensions(canvas) {
            var dpr = window.devicePixelRatio || 1;
            var totalWidth = (canvas ? canvas.width / dpr : 0) ||
                             (window.toolManager && window.toolManager.canvas ? window.toolManager.canvas.clientWidth : 0) ||
                             (window.coordinateMapper && window.coordinateMapper.viewport ? window.coordinateMapper.viewport.width : 800);
            var totalHeight = (canvas ? canvas.height / dpr : 0) ||
                              (window.toolManager && window.toolManager.canvas ? window.toolManager.canvas.clientHeight : 0) ||
                              (window.coordinateMapper && window.coordinateMapper.viewport ? window.coordinateMapper.viewport.height : 500);

            var chart = window.bigChart || window.chart;
            var timeScale = chart && typeof chart.timeScale === 'function' ? chart.timeScale() : null;

            var plotWidth = null;
            if (timeScale && typeof timeScale.width === 'function') {
                var tw = timeScale.width();
                if (tw > 0) plotWidth = tw;
            }
            if (plotWidth === null && window.coordinateMapper && window.coordinateMapper.viewport && window.coordinateMapper.viewport.width) {
                plotWidth = window.coordinateMapper.viewport.width;
            }
            if (plotWidth === null || plotWidth <= 0 || plotWidth >= totalWidth) {
                plotWidth = Math.max(100, totalWidth - 65);
            }

            var timeScaleHeight = 26;
            if (timeScale && typeof timeScale.height === 'function') {
                var th = timeScale.height();
                if (th > 0 && th < 100) timeScaleHeight = th;
            }

            var plotHeight = totalHeight - timeScaleHeight;
            if (chart && typeof chart.paneSize === 'function') {
                var ps = chart.paneSize();
                if (ps && ps.height > 0 && ps.height < totalHeight) plotHeight = ps.height;
            }
            if (plotHeight <= 0) {
                plotHeight = Math.max(100, totalHeight - 26);
            }

            return {
                totalWidth: totalWidth,
                totalHeight: totalHeight,
                plotWidth: plotWidth,
                plotHeight: plotHeight,
                timeScaleHeight: timeScaleHeight,
                priceScaleWidth: Math.max(40, totalWidth - plotWidth)
            };
        }

        // Draw price badge on the canvas (TradingView-style)
        getPriceLabel(idx, pixel, chartState) {
            if (this.coords && this.coords[idx] && this.coords[idx].price != null) {
                return this.coords[idx].price.toFixed(2);
            }
            if (pixel && chartState && typeof chartState.pixelToCoord === 'function') {
                var c = chartState.pixelToCoord(pixel.x, pixel.y);
                if (c && c.price != null && !isNaN(c.price)) {
                    return c.price.toFixed(2);
                }
            }
            if (pixel && chartState && typeof chartState.yToPrice === 'function') {
                var p = chartState.yToPrice(pixel.y);
                if (p != null && !isNaN(p)) return p.toFixed(2);
            }
            return null;
        }

        getTimeLabel(idx, pixel, chartState) {
            if (this.coords && this.coords[idx] && this.coords[idx].logical != null) {
                return this._formatTime(this.coords[idx].logical, chartState);
            }
            if (pixel && chartState && typeof chartState.pixelToCoord === 'function') {
                var c = chartState.pixelToCoord(pixel.x, pixel.y);
                if (c && c.logical != null) {
                    return this._formatTime(c.logical, chartState);
                }
            }
            if (pixel && chartState && typeof chartState.xToLogical === 'function') {
                var lg = chartState.xToLogical(pixel.x);
                if (lg != null) return this._formatTime(lg, chartState);
            }
            return null;
        }

        drawPriceBadge(ctx, x, y, text, color) {
            if (text == null || y == null) return;
            var dims = this._getPlotDimensions(ctx.canvas);
            var plotWidth = dims.plotWidth;
            var priceScaleWidth = dims.priceScaleWidth;

            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw = ctx.measureText(text).width;
            var pad = 6, bh = 18;
            var bw = Math.min(Math.round(tw + pad * 2), priceScaleWidth - 4);
            var rx = plotWidth + 2;

            // Badge background
            ctx.fillStyle = color || '#2962ff';
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(rx, y - bh / 2, bw, bh, 3);
            } else {
                ctx.rect(rx, y - bh / 2, bw, bh);
            }
            ctx.fill();

            // Badge text
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, rx + bw / 2, y);
            ctx.restore();
        }

        // Draw axis badges on the Y-Axis price scale and X-Axis time scale (unclipped phase)
        drawAxisBadges(ctx, chartState, isSelected, isHovered) {
            if (!chartState) return;

            // 1. If drawing has permanent price label style (e.g. horizontal line)
            if (this.style && this.style.showLabel && typeof this.getPriceLabel === 'function') {
                var pLabel = this.getPriceLabel(0);
                if (pLabel && typeof this.getPixels === 'function') {
                    var pxs = this.getPixels(chartState);
                    if (pxs && pxs.length > 0 && pxs[0]) {
                        this.drawPriceBadge(ctx, 0, pxs[0].y, pLabel, this.style.color);
                    }
                }
            }

            // 2. When selected, hovered, or in-progress: draw axis badges for all points
            if (isSelected || isHovered) {
                var pixels = [];
                if (typeof this.getPixels === 'function') {
                    pixels = this.getPixels(chartState) || [];
                }
                // If in progress and second point hasn't been finalized yet, include currentPos
                if (pixels.length === 1 && this.currentPos) {
                    pixels = [pixels[0], this.currentPos];
                }
                if (pixels && pixels.length > 0) {
                    this.drawAxisLabels(ctx, pixels, chartState);
                }
            }
        }

        // Axis coordinate readout (shared — every line-type tool uses this)
        _formatTime(logical, chartState) {
            if (logical == null) return null;
            if (window.coordinateMapper) {
                var time = window.coordinateMapper._logicalToTime(logical);
                if (time != null && typeof time === 'number' && isFinite(time) && time > 946684800) {
                    var d = new Date(time * 1000);
                    var month = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
                    return d.getUTCDate() + ' ' + month + ' ' + d.getUTCFullYear();
                }
            }
            return null;
        }

        // Default: all anchor points belong to a single highlight span. Tools
        // whose points form multiple independent visual lines (e.g. Disjoint
        // Channel's two separate boundary lines) override this to return each
        // line's points as its own group, so each gets its own connecting band
        // instead of one band silently swallowing (or worse, ignoring) the rest.
        getAxisHighlightGroups(pixels) {
            return [pixels];
        }

        drawAxisLabels(ctx, pixels, chartState) {
            if (!ctx || !pixels || pixels.length === 0) return;
            var dims = this._getPlotDimensions(ctx.canvas);
            var plotWidth = dims.plotWidth;
            var plotHeight = dims.plotHeight;
            var color = (this.style && this.style.color) || '#2962ff';

            // Range highlights (drawn behind labels): one connecting band per
            // highlight group, spanning that group's min→max price (Y axis)
            // and min→max time (X axis).
            var groups = this.getAxisHighlightGroups(pixels) || [pixels];
            for (var gi = 0; gi < groups.length; gi++) {
                var group = groups[gi];
                if (!group) continue;
                var valid = [];
                for (var vi = 0; vi < group.length; vi++) {
                    if (group[vi]) valid.push(group[vi]);
                }
                if (valid.length < 2) continue;

                var yMin = valid[0].y, yMax = valid[0].y;
                var xMin = valid[0].x, xMax = valid[0].x;
                for (var vj = 1; vj < valid.length; vj++) {
                    if (valid[vj].y < yMin) yMin = valid[vj].y;
                    if (valid[vj].y > yMax) yMax = valid[vj].y;
                    if (valid[vj].x < xMin) xMin = valid[vj].x;
                    if (valid[vj].x > xMax) xMax = valid[vj].x;
                }

                ctx.save();
                ctx.fillStyle = color + '35';
                ctx.fillRect(plotWidth, yMin, 3, yMax - yMin);
                ctx.fillRect(xMin, plotHeight, xMax - xMin, 3);
                ctx.restore();
            }

            for (var i = 0; i < pixels.length; i++) {
                if (!pixels[i]) continue;
                this._drawAxisLabel(ctx, pixels[i], i, chartState, dims);
            }
        }

        _drawAxisLabel(ctx, pixel, idx, chartState, dims) {
            if (!pixel) return;
            if (!dims) dims = this._getPlotDimensions(ctx.canvas);
            var color = (this.style && this.style.color) || '#2962ff';
            var plotWidth = dims.plotWidth;
            var plotHeight = dims.plotHeight;
            var priceScaleWidth = dims.priceScaleWidth;
            var timeScaleHeight = dims.timeScaleHeight;

            // Price axis label (on RIGHT Y-axis price scale)
            var priceText = this.getPriceLabel(idx, pixel, chartState);
            if (priceText != null) {
                ctx.save();
                ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                var tw = ctx.measureText(priceText).width;
                var pad = 6, bh = 18;
                var bw = Math.min(Math.round(tw + pad * 2), priceScaleWidth - 4);
                var rx = plotWidth + 2;

                ctx.fillStyle = color;
                ctx.beginPath();
                if (typeof ctx.roundRect === 'function') {
                    ctx.roundRect(rx, pixel.y - bh / 2, bw, bh, 3);
                } else {
                    ctx.rect(rx, pixel.y - bh / 2, bw, bh);
                }
                ctx.fill();

                ctx.fillStyle = '#ffffff';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(priceText, rx + bw / 2, pixel.y);
                ctx.restore();
            }

            // Time axis label (on BOTTOM X-axis time scale)
            var timeText = this.getTimeLabel(idx, pixel, chartState);
            if (timeText != null) {
                ctx.save();
                ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                var tw2 = ctx.measureText(timeText).width;
                var pad2 = 6, bh2 = Math.min(20, Math.max(16, timeScaleHeight - 4));
                var bw2 = tw2 + pad2 * 2;
                var bx2 = Math.max(2, Math.min(plotWidth - bw2 - 2, pixel.x - bw2 / 2));
                var by2 = plotHeight + 2;

                ctx.fillStyle = color;
                ctx.beginPath();
                if (typeof ctx.roundRect === 'function') {
                    ctx.roundRect(bx2, by2, bw2, bh2, 3);
                } else {
                    ctx.rect(bx2, by2, bw2, bh2);
                }
                ctx.fill();

                ctx.fillStyle = '#ffffff';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(timeText, bx2 + bw2 / 2, by2 + bh2 / 2);
                ctx.restore();
            }
        }

        drawHandle(ctx, p, isSelected) {
            if (!p) return;
            ctx.save();
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff'; // Blue fill if selected
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2); // Slightly larger if selected
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1.5;
            ctx.stroke();
            ctx.restore();
        }
    }

    class TwoPointDrawing extends BaseDrawing {
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 2;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            const pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;

            var p1 = pixels[0];
            var p2 = pixels.length > 1 ? pixels[1] : this.currentPos;
            if (!p1 || !p2) return;
            this.renderShape(ctx, p1, p2, isHovered);

            // In-progress drawing (not yet finalized): draw preview handle inline
            if (pixels.length < 2) {
                this.drawHandle(ctx, p1, false);
            }

            // Hover affordance: "+" prompt near midpoint for adding text labels
            if (isHovered && pixels.length >= 2) {
                this.drawHoverPrompt(ctx, p1, p2);
            }
        }

        // Hover affordance: faint "+ Add text" prompt near midpoint, rotated to line angle
        drawHoverPrompt(ctx, p1, p2) {
            var segments = this.getEdgeSegments ? this.getEdgeSegments([p1, p2]) : null;
            if (!segments || segments.length === 0) return;
            var seg = segments[0];
            if (!seg.p1 || !seg.p2) return;
            var mx = (seg.p1.x + seg.p2.x) / 2;
            var my = (seg.p1.y + seg.p2.y) / 2;
            var angle = Math.atan2(seg.p2.y - seg.p1.y, seg.p2.x - seg.p1.x);

            ctx.save();
            ctx.translate(mx, my);
            ctx.rotate(angle);
            ctx.fillStyle = 'rgba(255, 255, 255, 0.35)';
            ctx.font = 'bold 13px -apple-system, Roboto, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('+', 0, 0);
            ctx.restore();
        }

        // Draw selection handles (called by engine post-render pass)
        drawHandles(ctx, pixels, isSelected, chartState) {
            if (!pixels || pixels.length < 2) return;
            // Draw anchor handles
            var anchors = this.getAnchorPoints ? this.getAnchorPoints(pixels) : pixels;
            for (var ai = 0; ai < anchors.length; ai++) {
                if (anchors[ai]) this.drawHandle(ctx, anchors[ai], isSelected);
            }
            // Draw midpoint handles
            var midpoints = this.getMidpoints ? this.getMidpoints(pixels) : [];
            for (var mi = 0; mi < midpoints.length; mi++) {
                if (midpoints[mi]) this.drawHandle(ctx, midpoints[mi], isSelected);
            }
            // Axis coordinate readout — only for tools that opt in
            if (isSelected && this.showAxisLabels) this.drawAxisLabels(ctx, pixels, chartState);
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff'; // Blue fill if selected
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2); // Slightly larger if selected
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        renderShape(ctx, p1, p2) { console.warn("Implement renderShape"); }

        // Phase 2: Anchor points for handle hit testing
        getAnchorPoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            return [pixels[0], pixels[1]];
        }

        // Phase 2: Midpoint for handle hit testing
        getMidpoints(pixels) {
            return [];
        }

        // Phase 2: Fill shape for hit testing (null for line drawings)
        getFillShape(pixels) { return null; }
    }

    class TrendLine extends TwoPointDrawing {
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return null;
            if (!this.style.extendLeft && !this.style.extendRight) {
                return [{ p1: pixels[0], p2: pixels[1] }];
            }
            var clipper = new GeometryClipper();
            var clipped;
            if (this.style.extendLeft && this.style.extendRight) clipped = clipper.clipInfiniteLine(pixels[0], pixels[1]);
            else if (this.style.extendRight) clipped = clipper.clipRay(pixels[0], pixels[1]);
            else if (this.style.extendLeft) clipped = clipper.clipRay(pixels[1], pixels[0]);
            
            if (!clipped) return [];
            return [{ p1: clipped.p1, p2: clipped.p2 }];
        }

        renderShape(ctx, p1, p2) {
            var segments = this.getEdgeSegments([p1, p2]);
            if (!segments || segments.length === 0) return;
            var clipped = segments[0];

            ctx.beginPath();
            ctx.moveTo(clipped.p1.x, clipped.p1.y);
            ctx.lineTo(clipped.p2.x, clipped.p2.y);
            this.applyStyle(ctx);
            ctx.stroke();

            if (this.style.showLabel) this.drawPriceBadge(ctx, p2.x + 6, p2.y, this.getPriceLabel(1));
        }
    }

    class Ray extends TwoPointDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.showAxisLabels = true;
        }
        // Override: dragging the anchor translates the entire ray (preserves angle).
        // Only the direction handle rotates the ray.
        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;
            if (idx < 0 || idx >= this.coords.length) return;
            if (idx === 0) {
                // Dragging anchor: preserve the offset between anchor and direction
                var oldC0 = this.coords[0];
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
                var newLogical = mapper ? mapper(pixelX) : oldC0.logical;
                var dl = newLogical - oldC0.logical;
                var dp = newPrice - oldC0.price;
                this.coords[0] = { logical: newLogical, price: newPrice };
                this.coords[1] = { logical: this.coords[1].logical + dl, price: this.coords[1].price + dp };
            } else {
                super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
            }
        }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            return [GeometryUtils.midpoint(pixels[0], pixels[1])];
        }
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var clipper = new GeometryClipper();
            var clipped = clipper.clipRay(pixels[0], pixels[1]);
            if (!clipped) return [];
            return [{ p1: clipped.p1, p2: clipped.p2 }];
        }
        renderShape(ctx, p1, p2, isHovered) {
            var segments = this.getEdgeSegments([p1, p2]);
            if (!segments || segments.length === 0) return;
            var clipped = segments[0];

            ctx.beginPath();
            ctx.moveTo(clipped.p1.x, clipped.p1.y);
            ctx.lineTo(clipped.p2.x, clipped.p2.y);
            this.applyStyle(ctx);

            // Hover effect: slightly bolder line (TradingView parity)
            if (isHovered) {
                ctx.lineWidth = (this.style.width || 2) + 2;
                ctx.globalAlpha = (this.style.opacity != null ? this.style.opacity : 1) * 0.9;
            }

            ctx.stroke();

            if (this.style.showLabel) this.drawPriceBadge(ctx, p1.x + 6, p1.y - 6, this.getPriceLabel(0));
        }
    }

    class InfoLine extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            var clipper = new GeometryClipper();
            var clipped = clipper.clipSegment(p1, p2);
            if (!clipped) return;

            ctx.beginPath();
            ctx.moveTo(clipped.p1.x, clipped.p1.y);
            ctx.lineTo(clipped.p2.x, clipped.p2.y);
            this.applyStyle(ctx);
            ctx.stroke();

            // Show info bubble
            const midX = (p1.x + p2.x) / 2;
            const midY = (p1.y + p2.y) / 2;
            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            const angle = Math.atan2(dy, dx) * (180 / Math.PI);

            var deltaPrice = 0;
            var deltaBars = 0;
            var percentText = "";
            if (this.coords && this.coords.length >= 2) {
                var c1 = this.coords[0];
                var c2 = this.coords[1];
                deltaPrice = c2.price - c1.price;
                deltaBars = Math.round(c2.logical - c1.logical);
                if (c1.price !== 0) {
                    percentText = ((deltaPrice / c1.price) * 100).toFixed(2) + '%';
                }
            }
            var priceText = (deltaPrice >= 0 ? '+' : '') + deltaPrice.toFixed(2) + (percentText ? ' (' + percentText + ')' : '');
            var barsText = Math.abs(deltaBars) + (Math.abs(deltaBars) === 1 ? ' bar' : ' bars');

            ctx.fillStyle = 'rgba(30, 34, 45, 0.9)';
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.roundRect(midX - 70, midY - 25, 140, 50, 6);
            ctx.fill();
            ctx.stroke();

            ctx.fillStyle = '#fff';
            ctx.font = 'bold 10px -apple-system, Roboto, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(priceText, midX, midY - 8);
            ctx.fillText(barsText, midX, midY + 6);
            ctx.fillText(`${angle.toFixed(1)}°`, midX, midY + 18);
        }
    }

    class ExtendedLine extends TwoPointDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.showAxisLabels = true;
        }
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var clipper = new GeometryClipper();
            var clipped = clipper.clipInfiniteLine(pixels[0], pixels[1]);
            if (!clipped) return [];
            return [{ p1: clipped.p1, p2: clipped.p2 }];
        }
        renderShape(ctx, p1, p2, isHovered) {
            var segments = this.getEdgeSegments([p1, p2]);
            if (!segments || segments.length === 0) return;
            var clipped = segments[0];

            ctx.beginPath();
            ctx.moveTo(clipped.p1.x, clipped.p1.y);
            ctx.lineTo(clipped.p2.x, clipped.p2.y);
            this.applyStyle(ctx);
            ctx.stroke();
        }
    }

    class HorizontalLine extends BaseDrawing {
        // 1-point drawing: only price is meaningful; horizontal line spans full width.
        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length === 0) return null;
            var vp = chartState && chartState.viewport;
            var w = vp ? vp.width : 800;
            var p = pixels[0];
            if (!p) return null;
            return [{ p1: { x: 0, y: p.y }, p2: { x: w, y: p.y } }];
        }
        getPixels(chartState) {
            if (!chartState || this.coords.length === 0) return [];
            var px = chartState.coordToPixel(this.coords[0]);
            return px ? [px] : [];
        }

        getAnchorPoints(pixels) {
            return pixels && pixels.length > 0 ? [pixels[0]] : [];
        }

        getMidpoints(pixels) {
            return this.getAnchorPoints(pixels);
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels || pixels.length === 0) return;
            this.drawHandle(ctx, pixels[0], isSelected);
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        draw(ctx, chartState, isSelected) {
            var pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            var p = pixels[0];

            var cw = ctx.canvas.width / (window.devicePixelRatio || 1);
            var ch = ctx.canvas.height / (window.devicePixelRatio || 1);
            var pts = window.GeometryUtils.lineCanvasIntersection(
                { x: 0, y: p.y }, { x: 1, y: p.y }, cw, ch);
            if (!pts || pts.length < 2) return;
            ctx.beginPath();
            ctx.moveTo(pts[0].x, pts[0].y);
            ctx.lineTo(pts[1].x, pts[1].y);
            this.applyStyle(ctx);
            ctx.stroke();

            if (this.style.showLabel) this.drawPriceBadge(ctx, cw - 70, p.y, this.getPriceLabel(0));
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords || this.coords.length === 0) return;
            // HorizontalLine only moves along price axis (vertical)
            var c = this.coords[0];
            var pixel = chartState.coordToPixel(c);
            if (!pixel) return;
            var newCoord = chartState.pixelToCoord(pixel.x, pixel.y + dy);
            if (newCoord) this.coords[0] = newCoord;
        }
    }

    class HorizontalRay extends BaseDrawing {
        // 1-point drawing: anchor at (time, price). Ray extends rightward to canvas edge.
        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length === 0) return null;
            var vp = chartState && chartState.viewport;
            var w = vp ? vp.width : 800;
            var p = pixels[0];
            if (!p) return null;
            return [{ p1: p, p2: { x: w, y: p.y } }];
        }
        getPixels(chartState) {
            if (!chartState || this.coords.length === 0) return [];
            var px = chartState.coordToPixel(this.coords[0]);
            return px ? [px] : [];
        }

        getAnchorPoints(pixels) {
            return pixels && pixels.length > 0 ? [pixels[0]] : [];
        }

        getMidpoints(pixels) {
            return this.getAnchorPoints(pixels);
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels || pixels.length === 0) return;
            this.drawHandle(ctx, pixels[0], isSelected);
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        draw(ctx, chartState, isSelected) {
            var pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            var p = pixels[0];

            var cw = ctx.canvas.width / (window.devicePixelRatio || 1);
            var ch = ctx.canvas.height / (window.devicePixelRatio || 1);
            // Ray extends rightward from anchor
            var pts = window.GeometryUtils.lineCanvasIntersection(
                { x: p.x, y: p.y }, { x: cw, y: p.y }, cw, ch);
            if (!pts || pts.length < 1) return;
            // Pick the intersection furthest to the right
            var endPt = pts[0];
            for (var i = 1; i < pts.length; i++) {
                if (pts[i].x > endPt.x) endPt = pts[i];
            }
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(endPt.x, endPt.y);
            this.applyStyle(ctx);
            ctx.stroke();

            if (this.style.showLabel) this.drawPriceBadge(ctx, endPt.x - 70, p.y, this.getPriceLabel(0));
        }
    }

    class VerticalLine extends BaseDrawing {
        // 1-point drawing: only time is meaningful; vertical line spans full height.
        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length === 0) return null;
            var vp = chartState && chartState.viewport;
            var h = vp ? vp.height : 500;
            var p = pixels[0];
            if (!p) return null;
            return [{ p1: { x: p.x, y: 0 }, p2: { x: p.x, y: h } }];
        }
        getPixels(chartState) {
            if (!chartState || this.coords.length === 0) return [];
            var px = chartState.coordToPixel(this.coords[0]);
            return px ? [px] : [];
        }

        getAnchorPoints(pixels) {
            return pixels && pixels.length > 0 ? [pixels[0]] : [];
        }

        getMidpoints(pixels) {
            return this.getAnchorPoints(pixels);
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels || pixels.length === 0) return;
            this.drawHandle(ctx, pixels[0], isSelected);
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        draw(ctx, chartState, isSelected) {
            var pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            var p = pixels[0];

            var cw = ctx.canvas.width / (window.devicePixelRatio || 1);
            var ch = ctx.canvas.height / (window.devicePixelRatio || 1);
            var pts = window.GeometryUtils.lineCanvasIntersection(
                { x: p.x, y: 0 }, { x: p.x, y: 1 }, cw, ch);
            if (!pts || pts.length < 2) return;
            ctx.beginPath();
            ctx.moveTo(pts[0].x, pts[0].y);
            ctx.lineTo(pts[1].x, pts[1].y);
            this.applyStyle(ctx);
            ctx.stroke();

            if (this.style.showLabel) this.drawPriceBadge(ctx, p.x + 6, 14, this.getPriceLabel(0));
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords || this.coords.length === 0) return;
            // VerticalLine only moves along time axis (horizontal)
            var c = this.coords[0];
            var pixel = chartState.coordToPixel(c);
            if (!pixel) return;
            var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y);
            if (newCoord) this.coords[0] = newCoord;
        }
    }

    class CrossLine extends BaseDrawing {
        // 1-point drawing: crosshair at (time, price)
        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length === 0) return null;
            var vp = chartState && chartState.viewport;
            var w = vp ? vp.width : 800;
            var h = vp ? vp.height : 500;
            var p = pixels[0];
            if (!p) return null;
            return [
                { p1: { x: 0, y: p.y }, p2: { x: w, y: p.y } },
                { p1: { x: p.x, y: 0 }, p2: { x: p.x, y: h } }
            ];
        }
        getPixels(chartState) {
            if (!chartState || this.coords.length === 0) return [];
            var px = chartState.coordToPixel(this.coords[0]);
            return px ? [px] : [];
        }

        getAnchorPoints(pixels) {
            return pixels && pixels.length > 0 ? [pixels[0]] : [];
        }

        getMidpoints(pixels) {
            return this.getAnchorPoints(pixels);
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels || pixels.length === 0) return;
            this.drawHandle(ctx, pixels[0], isSelected);
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        draw(ctx, chartState, isSelected) {
            var pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            var p = pixels[0];

            var cw = ctx.canvas.width / (window.devicePixelRatio || 1);
            var ch = ctx.canvas.height / (window.devicePixelRatio || 1);

            // Vertical line through p.x
            var vPts = window.GeometryUtils.lineCanvasIntersection(
                { x: p.x, y: 0 }, { x: p.x, y: 1 }, cw, ch);
            // Horizontal line through p.y
            var hPts = window.GeometryUtils.lineCanvasIntersection(
                { x: 0, y: p.y }, { x: 1, y: p.y }, cw, ch);

            if (vPts && vPts.length >= 2) {
                ctx.beginPath();
                ctx.moveTo(vPts[0].x, vPts[0].y);
                ctx.lineTo(vPts[1].x, vPts[1].y);
                this.applyStyle(ctx);
                ctx.stroke();
            }
            if (hPts && hPts.length >= 2) {
                ctx.beginPath();
                ctx.moveTo(hPts[0].x, hPts[0].y);
                ctx.lineTo(hPts[1].x, hPts[1].y);
                this.applyStyle(ctx);
                ctx.stroke();
            }

            if (this.style.showLabel) this.drawPriceBadge(ctx, cw - 70, p.y, this.getPriceLabel(0));
        }
    }

    // =============================================================================
    // Channel Drawing Tools (Phase 3.2)
    // =============================================================================

    // Base class for all channel-type drawings
    class BaseChannelDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
        }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= this.pointCount;
        }

        // The 3rd point (P3) only encodes channel width/offset — it isn't a
        // second real line, so it shouldn't stretch the axis highlight band.
        // Subclasses with genuinely independent lines (Disjoint Channel)
        // override this.
        getAxisHighlightGroups(pixels) {
            if (pixels && pixels[0] && pixels[1]) return [[pixels[0], pixels[1]]];
            return [pixels];
        }

        // Subclasses override to return channel quad:
        // { b1Start:{x,y}, b1End:{x,y}, b2Start:{x,y}, b2End:{x,y} }
        getChannelBoundaries(pixels, cw, ch, chartState) { return null; }

        getPixels(chartState) {
            if (!chartState) return [];
            return this.coords.map(function(c) { return chartState.coordToPixel ? chartState.coordToPixel(c) : null; }).filter(Boolean);
        }

        draw(ctx, chartState, isSelected) {
            var pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;

            // In-progress preview: extend pixel array with currentPos for missing points
            var drawPixels = pixels.slice();
            if (pixels.length < this.pointCount && this.currentPos) {
                while (drawPixels.length < this.pointCount) {
                    drawPixels.push(this.currentPos);
                }
            }

            if (drawPixels.length < 2) return;
            var bounds = this.getChannelBoundaries(drawPixels, cw, ch, chartState);
            if (!bounds) return;

            var isPreview = pixels.length < this.pointCount;

            // Clip boundaries to anchor extent when extendLeft/Right is disabled
            this._applyChannelClip(bounds, pixels);

            // Ensure left-to-right ordering of b1 and b2 to prevent crossed fill shapes
            if (bounds.b1Start && bounds.b1End && bounds.b1Start.x > bounds.b1End.x) {
                var tmp1 = bounds.b1Start; bounds.b1Start = bounds.b1End; bounds.b1End = tmp1;
            }
            if (bounds.b2Start && bounds.b2End && bounds.b2Start.x > bounds.b2End.x) {
                var tmp2 = bounds.b2Start; bounds.b2Start = bounds.b2End; bounds.b2End = tmp2;
            }

            // Fill
            if (this.style.fillColor) {
                ctx.fillStyle = this.style.fillColor;
                ctx.globalAlpha = (isPreview ? 0.3 : 1) * (this.style.fillOpacity != null ? this.style.fillOpacity : 0.1);
                ctx.beginPath();
                ctx.moveTo(bounds.b1Start.x, bounds.b1Start.y);
                ctx.lineTo(bounds.b1End.x, bounds.b1End.y);
                ctx.lineTo(bounds.b2End.x, bounds.b2End.y);
                ctx.lineTo(bounds.b2Start.x, bounds.b2Start.y);
                ctx.closePath();
                ctx.fill();
            }
            ctx.globalAlpha = (isPreview ? 0.4 : 1) * (this.style.opacity != null ? this.style.opacity : 1);

            // Boundary lines
            this._drawLine(ctx, bounds.b1Start, bounds.b1End);
            this._drawLine(ctx, bounds.b2Start, bounds.b2End);

            // Side boundary lines (closing left and right ends when extend is disabled)
            if (this.style.extendLeft === false && bounds.b1Start && bounds.b2Start) {
                this._drawLine(ctx, bounds.b1Start, bounds.b2Start);
            }
            if (this.style.extendRight === false && bounds.b1End && bounds.b2End) {
                this._drawLine(ctx, bounds.b1End, bounds.b2End);
            }

            // Median line (dashed) — only when provided by subclass
            if (bounds.meanStart && bounds.meanEnd) {
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(bounds.meanStart.x, bounds.meanStart.y);
                ctx.lineTo(bounds.meanEnd.x, bounds.meanEnd.y);
                ctx.strokeStyle = this.style.color || '#2962ff';
                ctx.lineWidth = (this.style.width || 2) * 0.75;
                ctx.globalAlpha = (this.style.opacity != null ? this.style.opacity : 1) * 0.6;
                ctx.setLineDash([6, 4]);
                ctx.stroke();
                ctx.restore();
            }

            // Axis labels on all anchor points — selected only (TradingView shows
            // these as a transient selection highlight, not a permanent tag).
            if (!isPreview && isSelected && this.showAxisLabels && pixels.length >= 2) {
                this.drawAxisLabels(ctx, pixels, chartState);
            }
        }

        _drawLine(ctx, from, to) {
            ctx.beginPath();
            ctx.moveTo(from.x, from.y);
            ctx.lineTo(to.x, to.y);
            this.applyStyle(ctx);
            ctx.stroke();
        }

        _clipBoundary(bounds, key, minX, maxX, anchors) {
            var start = bounds[key + 'Start'], end = bounds[key + 'End'];
            if (!start || !end) return;

            // Sort start/end to know which is left and right
            var leftPt = start.x < end.x ? start : end;
            var rightPt = start.x < end.x ? end : start;

            var dx = rightPt.x - leftPt.x;
            var dy = rightPt.y - leftPt.y;

            if (Math.abs(dx) > 0.001) {
                var slope = dy / dx;
                if (this.style.extendLeft === false) {
                    var diffX = minX - leftPt.x;
                    leftPt.x = minX;
                    leftPt.y = leftPt.y + diffX * slope;
                }
                if (this.style.extendRight === false) {
                    var diffX = maxX - rightPt.x;
                    rightPt.x = maxX;
                    rightPt.y = rightPt.y + diffX * slope;
                }
            } else {
                // Vertical or near-vertical line: clip by Y coordinates of the anchors
                if (anchors && anchors.length >= 2) {
                    var minY = Math.min(anchors[0].y, anchors[1].y);
                    var maxY = Math.max(anchors[0].y, anchors[1].y);
                    var topPt = start.y < end.y ? start : end;
                    var bottomPt = start.y < end.y ? end : start;
                    if (this.style.extendLeft === false) {
                        topPt.y = minY;
                    }
                    if (this.style.extendRight === false) {
                        bottomPt.y = maxY;
                    }
                }
            }
        }

        _applyChannelClip(bounds, pixels) {
            if (!bounds || !pixels || pixels.length < 2) return;
            if (this.style.extendLeft !== false && this.style.extendRight !== false) return;

            var b1Anchors = [pixels[0], pixels[1]];
            var b1Xs = b1Anchors.map(function(p) { return p.x; });
            var b1MinX = Math.min.apply(null, b1Xs);
            var b1MaxX = Math.max.apply(null, b1Xs);
            this._clipBoundary(bounds, 'b1', b1MinX, b1MaxX, b1Anchors);

            var b2Anchors = (pixels.length >= 4) ? [pixels[2], pixels[3]] : [pixels[0], pixels[1]];
            var b2Xs = b2Anchors.map(function(p) { return p.x; });
            var b2MinX = Math.min.apply(null, b2Xs);
            var b2MaxX = Math.max.apply(null, b2Xs);
            this._clipBoundary(bounds, 'b2', b2MinX, b2MaxX, b2Anchors);

            if (bounds.meanStart) {
                this._clipBoundary(bounds, 'mean', b1MinX, b1MaxX, b1Anchors);
            }
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var cw = 10000;
            var ch = 10000;
            var bounds = this.getChannelBoundaries(pixels, cw, ch, null);
            if (!bounds) return null;
            this._applyChannelClip(bounds, pixels);
            return [bounds.b1Start, bounds.b1End, bounds.b2End, bounds.b2Start];
        }

        // TradingView-compatible edge hit-testing: each channel exposes the
        // four boundary segments that form its closed quadrilateral.
        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return null;
            var vp = (window.coordinateMapper && window.coordinateMapper.viewport) || {};
            var cw = vp.width || 2000;
            var ch = vp.height || 2000;
            var bounds = this.getChannelBoundaries(pixels, cw, ch, chartState);
            if (!bounds) return null;
            this._applyChannelClip(bounds, pixels);
            var segments = [];
            if (bounds.b1Start && bounds.b1End) segments.push({ p1: bounds.b1Start, p2: bounds.b1End });
            if (bounds.b2Start && bounds.b2End) segments.push({ p1: bounds.b2Start, p2: bounds.b2End });
            // Connector edges (only exist when extension is disabled)
            if (this.style.extendLeft === false && bounds.b1Start && bounds.b2Start) {
                segments.push({ p1: bounds.b1Start, p2: bounds.b2Start });
            }
            if (this.style.extendRight === false && bounds.b1End && bounds.b2End) {
                segments.push({ p1: bounds.b1End, p2: bounds.b2End });
            }
            return segments;
        }

        getAnchorPoints(pixels) {
            if (!pixels) return [];
            // Return all anchor points (one per coord)
            var result = [];
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) result.push(pixels[i]);
            }
            return result;
        }

        getMidpoints(pixels) {
            // TradingView channel tools expose only their anchor-point handles.
            // The old midpoint handle dragged a single anchor instead of the
            // whole channel, which broke "move like TradingView" expectations.
            // Translating a channel is done by dragging body, edge, or fill.
            return [];
        }

        // Subclasses override to return the second line's midpoint pixel
        _getSecondLineMidpoint(pixels) { return null; }

        drawHandles(ctx, pixels, isSelected, chartState) {
            if (!pixels) return;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
            }
            var mids = this.getMidpoints(pixels);
            for (var mi = 0; mi < mids.length; mi++) {
                if (mids[mi]) this._drawHandle(ctx, mids[mi], isSelected, true);
            }
            // Axis labels on all anchor points (opt-in)
            if (isSelected && this.showAxisLabels && pixels.length >= 2) {
                this.drawAxisLabels(ctx, pixels, chartState);
            }
        }

        _drawHandle(ctx, p, isSelected, isMidpoint) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            if (isMidpoint) {
                var s = 5;
                ctx.beginPath();
                ctx.moveTo(p.x, p.y - s);
                ctx.lineTo(p.x + s, p.y);
                ctx.lineTo(p.x, p.y + s);
                ctx.lineTo(p.x - s, p.y);
                ctx.closePath();
            } else {
                ctx.beginPath();
                ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            }
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }
    }

    // --- Parallel Channel ---
    // 3 anchor points:
    //   p1, p2 = define the main line
    //   p3     = encodes price offset (stored in this.style.offset)
    // Second line = main line shifted by offset in price space.
    // Median line = dashed, halfway between the two lines.
    class ParallelChannel extends BaseChannelDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
            this.showAxisLabels = true;
        }

        // Called after p3 is placed to store the price offset
        _computeAndStoreOffset() {
            if (this.coords.length < 3) return;
            var c1 = this.coords[0], c2 = this.coords[1], c3 = this.coords[2];
            var logicalSpan = c2.logical - c1.logical;
            var t = logicalSpan !== 0 ? (c3.logical - c1.logical) / logicalSpan : 0.5;
            var priceOnMain = c1.price + t * (c2.price - c1.price);
            this.style.offset = c3.price - priceOnMain;
        }

        // Update p3 coord to sit at the midpoint of the second line
        _updateP3ToSecondLineMidpoint() {
            if (this.coords.length < 3 || this.style.offset == null) return;
            var c1 = this.coords[0], c2 = this.coords[1];
            var midLogical = (c1.logical + c2.logical) / 2;
            var midPrice = (c1.price + c2.price) / 2 + this.style.offset;
            // PHASE A: this anchor is DERIVED from a computed midpoint, so its
            // time must be resolved from the new logical rather than copied.
            var _cmMid = window.coordinateMapper;
            var _tMid = (_cmMid && typeof _cmMid.logicalToTime === 'function') ? _cmMid.logicalToTime(midLogical) : null;
            this.coords[2] = { logical: midLogical, price: midPrice, time: (_tMid !== null && _tMid !== undefined && !isNaN(_tMid)) ? _tMid : null };
        }

        addPoint(pos, chartState) {
            var result = super.addPoint(pos, chartState);
            if (this.coords.length >= 3) {
                this._computeAndStoreOffset();
                this._updateP3ToSecondLineMidpoint();
            }
            return result;
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || !chartState) return;

            // Midpoint 0 (main line) → translate whole channel vertically
            if (draggedHandle.hitType === 'midpoint' && draggedHandle.handleIndex === 0) {
                var c0 = this.coords[0], c1 = this.coords[1];
                var oldMidPrice = (c0.price + c1.price) / 2;
                var delta = newPrice - oldMidPrice;
                // logical unchanged (vertical move only) -> keep canonical time
                this.coords[0] = { logical: c0.logical, price: c0.price + delta, time: c0.time };
                this.coords[1] = { logical: c1.logical, price: c1.price + delta, time: c1.time };
                this._updateP3ToSecondLineMidpoint();
                return;
            }

            // Midpoint 1 (second line) or anchor 2 (p3) → change offset (perpendicular constraint)
            if ((draggedHandle.hitType === 'midpoint' && draggedHandle.handleIndex === 1) ||
                (draggedHandle.hitType === 'anchor' && draggedHandle.handleIndex === 2)) {
                if (this.coords.length < 3) return;
                var p1Px = chartState.coordToPixel(this.coords[0]);
                var p2Px = chartState.coordToPixel(this.coords[1]);
                if (!p1Px || !p2Px) return;
                var dx = p2Px.x - p1Px.x, dy = p2Px.y - p1Px.y;
                var len = Math.sqrt(dx * dx + dy * dy);
                if (len < 1) return;
                var perpX = -dy / len, perpY = dx / len;
                var mouseY = chartState.priceToY ? chartState.priceToY(newPrice) : newPrice;
                var perpDist = (pixelX - p1Px.x) * perpX + (mouseY - p1Px.y) * perpY;
                var constrainedPx = { x: p1Px.x + perpX * perpDist, y: p1Px.y + perpY * perpDist };
                var constrainedCoord = chartState.pixelToCoord(constrainedPx.x, constrainedPx.y);
                if (!constrainedCoord || constrainedCoord.logical == null || constrainedCoord.price == null) return;
                // Convert perpendicular pixel distance to price offset
                var c0c = this.coords[0], c1c = this.coords[1];
                var t = (c1c.logical - c0c.logical) !== 0
                    ? (constrainedCoord.logical - c0c.logical) / (c1c.logical - c0c.logical)
                    : 0;
                var priceOnMain = c0c.price + t * (c1c.price - c0c.price);
                this.style.offset = constrainedCoord.price - priceOnMain;
                this._updateP3ToSecondLineMidpoint();
                return;
            }

            // Anchor 0 or 1 (p1/p2) → normal anchor drag, preserve offset
            if (draggedHandle.hitType === 'anchor' && (draggedHandle.handleIndex === 0 || draggedHandle.handleIndex === 1)) {
                super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
                this._updateP3ToSecondLineMidpoint();
                return;
            }

            super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
        }

        _getSecondLineMidpoint(pixels) {
            if (!pixels || pixels.length < 3 || !pixels[2]) return null;
            return pixels[2];
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
            this._computeAndStoreOffset();
            this._updateP3ToSecondLineMidpoint();
        }

        getChannelBoundaries(pixels, cw, ch, chartState) {
            if (pixels.length < 2) return null;
            var p1 = pixels[0], p2 = pixels[1];

            // Main line clipped to canvas
            var mainPts = GeometryUtils.lineCanvasIntersection(p1, p2, cw, ch);
            if (!mainPts || mainPts.length < 2) return null;

            // Determine pixel Y offset for second line
            var pixelDy = 0;
            if (pixels.length >= 3 && this.coords.length >= 3 && this.style.offset != null) {
                // Finalized: compute pixelDy directly from price offset using chart's y-scale.
                // This avoids division by priceDelta which blows up for near-horizontal lines.
                if (chartState && chartState.priceToY) {
                    // Use mid-price of the main line as reference to get the pixel/price scale
                    var refPrice = (this.coords[0].price + this.coords[1].price) / 2;
                    var refY = chartState.priceToY(refPrice);
                    var offsetY = chartState.priceToY(refPrice + this.style.offset);
                    pixelDy = offsetY - refY;
                } else {
                    // Fallback: ratio-based but guarded against near-zero priceDelta
                    var c1 = this.coords[0], c2 = this.coords[1];
                    var priceDelta = c2.price - c1.price;
                    var pixelHeight = p2.y - p1.y;
                    if (Math.abs(priceDelta) > 0.000001 && Math.abs(pixelHeight) < ch * 4) {
                        pixelDy = pixelHeight * this.style.offset / priceDelta;
                    } else {
                        // Horizontal line: use canvas height per price unit approach
                        pixelDy = 0;
                    }
                }
                // Sanity clamp: never let pixelDy push lines off-canvas by more than 2x canvas height
                pixelDy = Math.max(-ch * 2, Math.min(ch * 2, pixelDy));
            } else if (pixels.length >= 3 && pixels[2]) {
                // Preview (between click 2 and click 3): compute from mouse position
                var p3px = pixels[2];
                var dx = p2.x - p1.x, dy = p2.y - p1.y;
                if (Math.abs(dx) > 0.001) {
                    var slope = dy / dx;
                    pixelDy = (p3px.y - p1.y) - slope * (p3px.x - p1.x);
                } else {
                    // Vertical line: offset is vertical distance to mouse Y
                    pixelDy = p3px.y - (p1.y + p2.y) / 2;
                }
            }

            // Second line = original line shifted by pixelDy in price space, then clipped to canvas
            var op1 = { x: p1.x, y: p1.y + pixelDy };
            var op2 = { x: p2.x, y: p2.y + pixelDy };
            var offsetPts = GeometryUtils.lineCanvasIntersection(op1, op2, cw, ch);
            if (!offsetPts || offsetPts.length < 2) {
                offsetPts = [{ x: 0, y: mainPts[0].y + pixelDy }, { x: cw, y: mainPts[1].y + pixelDy }];
            }

            // Median line = halfway between (also properly clipped)
            var mp1 = { x: p1.x, y: p1.y + pixelDy / 2 };
            var mp2 = { x: p2.x, y: p2.y + pixelDy / 2 };
            var meanPts = GeometryUtils.lineCanvasIntersection(mp1, mp2, cw, ch);
            if (!meanPts || meanPts.length < 2) {
                meanPts = [{ x: mainPts[0].x, y: mainPts[0].y + pixelDy / 2 }, { x: mainPts[1].x, y: mainPts[1].y + pixelDy / 2 }];
            }

            return { b1Start: mainPts[0], b1End: mainPts[1], b2Start: offsetPts[0], b2End: offsetPts[1], meanStart: meanPts[0], meanEnd: meanPts[1] };
        }
    }

    // --- Flat Top Channel ---
    // 3 points: p1/p2 = trend line, p3 = flat level line
    class FlatTopChannel extends BaseChannelDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
            this.showAxisLabels = true;
        }

        getAnchorPoints(pixels) {
            if (!pixels || pixels.length < 2) return pixels || [];
            var p1 = pixels[0], p2 = pixels[1], p3 = pixels[2] || p2;
            var flatY = p3.y;
            return [
                { x: p1.x, y: p1.y },
                { x: p2.x, y: p2.y },
                { x: p2.x, y: flatY },
                { x: p1.x, y: flatY }
            ];
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || !chartState || !this.coords) return;
            var idx = draggedHandle.handleIndex;
            var mapper = (chartState && (chartState.xToLogical || chartState.xToTime));

            if (idx === 0) {
                var logical = this.coords[0] ? this.coords[0].logical : 0;
                if (mapper && pixelX != null) {
                    var nl = mapper(pixelX); if (nl != null) logical = nl;
                }
                this.coords[0] = { logical: logical, price: newPrice };
            } else if (idx === 1) {
                var logical = this.coords[1] ? this.coords[1].logical : 0;
                if (mapper && pixelX != null) {
                    var nl = mapper(pixelX); if (nl != null) logical = nl;
                }
                this.coords[1] = { logical: logical, price: newPrice };
            } else if (idx === 2) {
                var logical2 = this.coords[1] ? this.coords[1].logical : 0;
                if (mapper && pixelX != null) {
                    var nl = mapper(pixelX); if (nl != null) logical2 = nl;
                }
                if (this.coords[1]) this.coords[1] = { logical: logical2, price: this.coords[1].price };
                var p3Logical = (this.coords[2] && this.coords[2].logical != null) ? this.coords[2].logical : logical2;
                this.coords[2] = { logical: p3Logical, price: newPrice };
            } else if (idx === 3) {
                var logical1 = this.coords[0] ? this.coords[0].logical : 0;
                if (mapper && pixelX != null) {
                    var nl = mapper(pixelX); if (nl != null) logical1 = nl;
                }
                if (this.coords[0]) this.coords[0] = { logical: logical1, price: this.coords[0].price };
                var p3Logical = (this.coords[2] && this.coords[2].logical != null) ? this.coords[2].logical : logical1;
                this.coords[2] = { logical: p3Logical, price: newPrice };
            }
        }

        _getGeometry(chartState) {
            if (!this.coords || this.coords.length < 1 || !chartState || !chartState.coordToPixel) return null;
            var pixels = this.getPixels(chartState);
            if (!pixels || pixels.length < 1) return null;

            var p1 = pixels[0];
            var p2 = pixels[1];
            var isPhase1 = false;
            var isPhase2 = false;

            if (!p2) {
                if (this.currentPos) {
                    p2 = this.currentPos;
                    isPhase1 = true;
                } else {
                    return null;
                }
            }

            if (isPhase1) {
                return { isPhase1: true, p1: p1, p2: p2 };
            }

            var p3 = pixels[2];
            if (!p3) {
                if (this.currentPos) {
                    p3 = this.currentPos;
                    isPhase2 = true;
                } else {
                    p3 = p2;
                }
            }

            var flatY = p3.y;
            var x1 = p1.x, y1 = p1.y;
            var x2 = p2.x, y2 = p2.y;

            var v1 = { x: x1, y: y1 };
            var v2 = { x: x2, y: y2 };
            var v3 = { x: x2, y: flatY };
            var v4 = { x: x1, y: flatY };

            return {
                isPhase1: false,
                isPreview: isPhase2,
                v1: v1, v2: v2, v3: v3, v4: v4,
                p1: p1, p2: p2, p3: p3,
                flatY: flatY
            };
        }

        draw(ctx, chartState, isSelected) {
            var geom = this._getGeometry(chartState);
            if (!geom) return;

            // Phase 1 (between click 1 and click 2): bounded line from P1 to cursor
            if (geom.isPhase1) {
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(geom.p1.x, geom.p1.y);
                ctx.lineTo(geom.p2.x, geom.p2.y);
                this.applyStyle(ctx);
                ctx.stroke();
                ctx.restore();
                return;
            }

            var isPreview = geom.isPreview;

            // 1. Fill region
            if (this.style.fillColor) {
                ctx.save();
                ctx.fillStyle = this.style.fillColor;
                ctx.globalAlpha = (isPreview ? 0.3 : 1) * (this.style.fillOpacity != null ? this.style.fillOpacity : 0.1);
                ctx.beginPath();
                ctx.moveTo(geom.v1.x, geom.v1.y);
                ctx.lineTo(geom.v2.x, geom.v2.y);
                ctx.lineTo(geom.v3.x, geom.v3.y);
                ctx.lineTo(geom.v4.x, geom.v4.y);
                ctx.closePath();
                ctx.fill();
                ctx.restore();
            }

            // 2. Boundary Lines
            ctx.save();
            this.applyStyle(ctx);
            ctx.globalAlpha = (isPreview ? 0.4 : 1) * (this.style.opacity != null ? this.style.opacity : 1);

            // Sloped trendline (P1 -> P2)
            ctx.beginPath(); ctx.moveTo(geom.v1.x, geom.v1.y); ctx.lineTo(geom.v2.x, geom.v2.y); ctx.stroke();
            // Flat horizontal segment (V3 -> V4)
            ctx.beginPath(); ctx.moveTo(geom.v3.x, geom.v3.y); ctx.lineTo(geom.v4.x, geom.v4.y); ctx.stroke();

            ctx.restore();

            // 3. Axis labels (opt-in)
            if (!isPreview && this.showAxisLabels) {
                var pixels = this.getPixels(chartState);
                if (pixels && pixels.length >= 2) {
                    this.drawAxisLabels(ctx, pixels, chartState);
                }
            }
        }

        drawHandles(ctx, pixels, isSelected, chartState) {
            var geom = this._getGeometry(chartState);
            if (!geom || geom.isPhase1) {
                if (pixels) {
                    for (var i = 0; i < pixels.length; i++) {
                        if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
                    }
                }
                return;
            }

            var handles = [geom.v1, geom.v2, geom.v3, geom.v4];
            for (var hi = 0; hi < handles.length; hi++) {
                if (handles[hi]) {
                    this._drawHandle(ctx, handles[hi], isSelected, false);
                }
            }

            if (isSelected && this.showAxisLabels && pixels && pixels.length >= 2) {
                this.drawAxisLabels(ctx, pixels, chartState);
            }
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var p1 = pixels[0], p2 = pixels[1], p3 = pixels[2] || p2;
            var flatY = p3.y;
            return [
                { x: p1.x, y: p1.y },
                { x: p2.x, y: p2.y },
                { x: p2.x, y: flatY },
                { x: p1.x, y: flatY }
            ];
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return [];
            var p1 = pixels[0], p2 = pixels[1], p3 = pixels[2] || p2;
            var flatY = p3.y;
            var v1 = { x: p1.x, y: p1.y };
            var v2 = { x: p2.x, y: p2.y };
            var v3 = { x: p2.x, y: flatY };
            var v4 = { x: p1.x, y: flatY };
            return [
                { p1: v1, p2: v2 },
                { p1: v2, p2: v3 },
                { p1: v3, p2: v4 },
                { p1: v4, p2: v1 }
            ];
        }
    }

    // --- Flat Bottom Channel ---
    class FlatBottomChannel extends FlatTopChannel {}

    // --- Disjoint Channel ---
    // 4 independent points placed in 2 line pairs:
    // P1 (top-left) -> P2 (top-right): Top Line
    // P3 (bottom-left) -> P4 (bottom-right): Bottom Line
    // Quadrilateral fill between top line and bottom line.
    class DisjointChannel extends BaseChannelDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 4;
            this.showAxisLabels = true;
        }

        // Two independent lines (P1-P2 top, P3-P4 bottom) → two independent
        // axis highlight bands, not one band spanning all four points.
        getAxisHighlightGroups(pixels) {
            var groups = [];
            if (pixels && pixels[0] && pixels[1]) groups.push([pixels[0], pixels[1]]);
            if (pixels && pixels[2] && pixels[3]) groups.push([pixels[2], pixels[3]]);
            return groups.length ? groups : [pixels];
        }

        draw(ctx, chartState, isSelected) {
            if (!this.coords || this.coords.length < 1 || !chartState || !chartState.coordToPixel) return;
            var pixels = this.getPixels(chartState);
            if (!pixels || pixels.length < 1) return;

            var p1 = pixels[0];
            var p2 = pixels[1];
            var p3 = pixels[2];
            var p4 = pixels[3];

            // Placement phase 1 (only 1 point placed): preview top line to mouse
            if (!p2) {
                if (this.currentPos) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.moveTo(p1.x, p1.y);
                    ctx.lineTo(this.currentPos.x, this.currentPos.y);
                    this.applyStyle(ctx);
                    ctx.stroke();
                    ctx.restore();
                }
                return;
            }

            var isPreview = false;

            // Placement phase 2 (2 points placed): top line fixed, show parallel channel preview to mouse
            if (!p3) {
                if (this.currentPos) {
                    isPreview = true;
                    var dx = this.currentPos.x - p1.x;
                    var dy = this.currentPos.y - p1.y;
                    p3 = this.currentPos;
                    p4 = { x: p2.x + dx, y: p2.y + dy };
                } else {
                    ctx.save();
                    this.applyStyle(ctx);
                    ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
                    ctx.restore();
                    return;
                }
            } else if (!p4) {
                // Placement phase 3 (3 points placed): top line fixed, bottom line connects P3 -> mouse
                if (this.currentPos) {
                    isPreview = true;
                    p4 = this.currentPos;
                } else {
                    p4 = p3;
                }
            }

            // Fill quad: P1 -> P2 -> P4 -> P3
            if (this.style.fillColor) {
                ctx.save();
                ctx.fillStyle = this.style.fillColor;
                ctx.globalAlpha = (isPreview ? 0.3 : 1) * (this.style.fillOpacity != null ? this.style.fillOpacity : 0.18);
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.lineTo(p4.x, p4.y);
                ctx.lineTo(p3.x, p3.y);
                ctx.closePath();
                ctx.fill();
                ctx.restore();
            }

            // Stroke lines: ONLY Top Line (P1 -> P2) and Bottom Line (P3 -> P4)
            ctx.save();
            this.applyStyle(ctx);
            ctx.globalAlpha = (isPreview ? 0.4 : 1) * (this.style.opacity != null ? this.style.opacity : 1);

            // Top Line (P1 -> P2)
            ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
            // Bottom Line (P3 -> P4)
            ctx.beginPath(); ctx.moveTo(p3.x, p3.y); ctx.lineTo(p4.x, p4.y); ctx.stroke();

            ctx.restore();

            // Axis labels — selected only
            if (!isPreview && isSelected && this.showAxisLabels && pixels.length >= 2) {
                this.drawAxisLabels(ctx, pixels, chartState);
            }
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 4) return null;
            return [
                { x: pixels[0].x, y: pixels[0].y },
                { x: pixels[1].x, y: pixels[1].y },
                { x: pixels[3].x, y: pixels[3].y },
                { x: pixels[2].x, y: pixels[2].y }
            ];
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return [];
            var p1 = pixels[0], p2 = pixels[1];
            var p3 = pixels[2], p4 = pixels[3];
            var segs = [];
            if (p1 && p2) segs.push({ p1: p1, p2: p2 }); // Top line
            if (p3 && p4) segs.push({ p1: p3, p2: p4 }); // Bottom line
            if (p1 && p3) segs.push({ p1: p1, p2: p3 }); // Left boundary for hit-testing
            if (p2 && p4) segs.push({ p1: p2, p2: p4 }); // Right boundary for hit-testing
            return segs;
        }

        drawHandles(ctx, pixels, isSelected, chartState) {
            if (!pixels) return;
            var drawPx = pixels.slice();
            if (pixels.length < 4 && this.currentPos) {
                drawPx.push(this.currentPos);
            }
            for (var i = 0; i < drawPx.length; i++) {
                if (drawPx[i]) this._drawHandle(ctx, drawPx[i], isSelected, false);
            }
            if (isSelected && this.showAxisLabels && pixels.length >= 2) {
                this.drawAxisLabels(ctx, pixels, chartState);
            }
        }
    }

    // --- Regression Trend ---
    // Exactly 2 anchors define the candle range. The center line is computed by
    // least-squares linear regression over candle closes inside the range
    // (x = candle index inside selection, 0..N-1; y = close). Never interpolated
    // from anchor prices. Channel width = sigmaMultiplier * residual stddev
    // (default 2σ, TradingView behavior — width auto-expands with volatility).
    // Geometry is finite: the channel begins at the first anchor and ends at the
    // second anchor. No infinite extension. Recomputes every frame from live
    // candle data (O(N), no per-frame allocations).
    class RegressionTrend extends BaseChannelDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 2;
            this.showAxisLabels = true;
            this._lastChartState = null;
            this._lastGeom = null;
        }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 2;
        }

        // 4 handles: 0=Start Top, 1=Start Bottom, 2=End Top, 3=End Bottom.
        // Left handles edit the beginning (coord 0), right handles the end (coord 1).
        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined || !chartState) return;
            var idx = draggedHandle.handleIndex;
            var coordIdx = (idx === 0 || idx === 1) ? 0 : 1;
            if (coordIdx >= this.coords.length) return;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    this.coords[coordIdx] = { logical: snapInfo.logical, price: snapInfo.price, time: snapInfo.time };
                    return;
                }
            }
            var logical = this.coords[coordIdx].logical;
            var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
            if (mapper) {
                var newLogical = mapper(pixelX);
                if (newLogical != null) logical = newLogical;
            }
            this.coords[coordIdx] = { logical: logical, price: newPrice };
        }

        // No center handle (TradingView-compatible).
        getMidpoints(pixels) { return []; }

        // Resolves the configured source field from a candle. Defaults to
        // 'close'. Composite sources (hl2/hlc3/ohlc4) match TradingView's
        // standard definitions.
        _getSourceValue(c, source) {
            if (!c) return null;
            switch (source) {
                case 'open': return c.open;
                case 'high': return c.high;
                case 'low': return c.low;
                case 'hl2': return (c.high + c.low) / 2;
                case 'hlc3': return (c.high + c.low + c.close) / 3;
                case 'ohlc4': return (c.open + c.high + c.low + c.close) / 4;
                case 'close':
                default: return c.close;
            }
        }

        // Least-squares regression over the configured source in [startIdx, endIdx].
        // x = 0..N-1 (relative candle index), y = source value. Ignores gaps.
        // O(N), single allocation-free pass for sums + one residual pass.
        _computeRegression(startIdx, endIdx) {
            var candles = window._chartCandles;
            if (!candles || !Array.isArray(candles)) return null;
            var source = (this.style && this.style.source) || 'close';

            var n = 0;
            var sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
            var x = 0;
            for (var a = startIdx; a <= endIdx && a < candles.length; a++) {
                var c = candles[a];
                if (!c) continue;
                var y = this._getSourceValue(c, source);
                if (typeof y !== 'number' || !isFinite(y)) continue;
                sumX += x; sumY += y; sumXY += x * y; sumX2 += x * x;
                n++; x++;
            }
            if (n < 2) return null;

            var meanX = sumX / n;
            var meanY = sumY / n;
            var ssxx = sumX2 - n * meanX * meanX;
            if (ssxx === 0) return null;
            var slope = (sumXY - n * meanX * meanY) / ssxx;
            var intercept = meanY - slope * meanX;

            var ssRes = 0;
            var ssyy = 0;
            var xi = 0;
            for (var a = startIdx; a <= endIdx && a < candles.length; a++) {
                var c = candles[a];
                if (!c) continue;
                var y = this._getSourceValue(c, source);
                if (typeof y !== 'number' || !isFinite(y)) continue;
                var pred = intercept + slope * xi;
                var r = y - pred;
                ssRes += r * r;
                var dy = y - meanY;
                ssyy += dy * dy;
                xi++;
            }
            var sigma = Math.sqrt(ssRes / n);
            // Independent upper/lower deviation multipliers (TradingView exposes these
            // as separate "Upper Deviation" / "Lower Deviation" inputs, default 2 each,
            // so the bands need not be symmetric). style.sigmaMultiplier is kept as a
            // legacy fallback for both sides if the newer fields aren't set.
            var legacyMult = (this.style && this.style.sigmaMultiplier != null) ? this.style.sigmaMultiplier : 2;
            var upMult = (this.style && this.style.upperDeviation != null) ? this.style.upperDeviation : legacyMult;
            var downMult = (this.style && this.style.lowerDeviation != null) ? this.style.lowerDeviation : legacyMult;
            var widthUp = sigma * upMult;
            var widthDown = sigma * downMult;

            var pearsonR = (ssxx > 0 && ssyy > 0) ? (sumXY - n * meanX * meanY) / Math.sqrt(ssxx * ssyy) : 0;

            return {
                slope: slope, intercept: intercept, sigma: sigma,
                widthUp: widthUp, widthDown: widthDown, n: n, startIdx: startIdx, endIdx: endIdx,
                pearsonR: pearsonR
            };
        }

        // Build finite pixel geometry (upper/lower/center + 4 corner handles).
        _getGeometry(chartState) {
            if (!chartState || !this.coords || this.coords.length < 1) return null;
            var t1 = this.coords[0].logical;
            if (t1 == null) return null;

            var t2 = null;
            if (this.coords.length >= 2 && this.coords[1]) t2 = this.coords[1].logical;
            // Placement preview: extend the range to the current cursor position.
            if (t2 == null && this.currentPos && chartState.xToLogical) {
                var pl = chartState.xToLogical(this.currentPos.x);
                if (pl != null) t2 = pl;
            }
            if (t2 == null) return null;

            var startIdx = Math.round(Math.min(t1, t2));
            var endIdx = Math.round(Math.max(t1, t2));
            var reg = this._computeRegression(startIdx, endIdx);
            if (!reg) return null;

            // Finite channel: anchored to the two anchors' x positions.
            var x1 = null, x2 = null;
            if (chartState.coordToPixel) {
                var p1 = chartState.coordToPixel({ logical: startIdx, price: 0 });
                var p2 = chartState.coordToPixel({ logical: endIdx, price: 0 });
                if (p1) x1 = p1.x;
                if (p2) x2 = p2.x;
            }
            if (x1 == null || x2 == null) return null;

            var priceToY = chartState.priceToY || function(p) { return p; };
            var yStart = reg.intercept;                                   // x = 0
            var yEnd = reg.intercept + reg.slope * (reg.n - 1);           // x = N-1
            var wUp = reg.widthUp;
            var wDown = reg.widthDown;

            var centerStart = { x: x1, y: priceToY(yStart) };
            var centerEnd = { x: x2, y: priceToY(yEnd) };
            var upperStart = { x: x1, y: priceToY(yStart + wUp) };
            var upperEnd = { x: x2, y: priceToY(yEnd + wUp) };
            var lowerStart = { x: x1, y: priceToY(yStart - wDown) };
            var lowerEnd = { x: x2, y: priceToY(yEnd - wDown) };

            var geom = {
                reg: reg,
                centerStart: centerStart, centerEnd: centerEnd,
                upperStart: upperStart, upperEnd: upperEnd,
                lowerStart: lowerStart, lowerEnd: lowerEnd,
                // 4 handles: Start Top / Start Bottom / End Top / End Bottom
                startTop: upperStart, startBottom: lowerStart,
                endTop: upperEnd, endBottom: lowerEnd,
                isPreview: this.coords.length < this.pointCount
            };
            this._lastGeom = geom;
            return geom;
        }

        draw(ctx, chartState, isSelected) {
            if (chartState) this._lastChartState = chartState;
            var geom = this._getGeometry(chartState);
            if (!geom) return;

            var isPreview = geom.isPreview;
            var style = this.style || {};
            var alpha = (isPreview ? 0.4 : 1) * (style.opacity != null ? style.opacity : 1);
            // "Use Upper/Lower Deviation" toggles — default on, matching TradingView.
            var showUpper = style.showUpperDeviation !== false;
            var showLower = style.showLowerDeviation !== false;

            // 1. Upper Channel Polygon Fill (Blue)
            if (showUpper) {
                ctx.save();
                ctx.fillStyle = style.upFillColor || '#2962ff';
                ctx.globalAlpha = (isPreview ? 0.3 : 1) * (style.fillOpacity != null ? style.fillOpacity : 0.15);
                ctx.beginPath();
                ctx.moveTo(geom.upperStart.x, geom.upperStart.y);
                ctx.lineTo(geom.upperEnd.x, geom.upperEnd.y);
                ctx.lineTo(geom.centerEnd.x, geom.centerEnd.y);
                ctx.lineTo(geom.centerStart.x, geom.centerStart.y);
                ctx.closePath();
                ctx.fill();
                ctx.restore();
            }

            // 2. Lower Channel Polygon Fill (Red)
            if (showLower) {
                ctx.save();
                ctx.fillStyle = style.downFillColor || '#f23645';
                ctx.globalAlpha = (isPreview ? 0.3 : 1) * (style.fillOpacity != null ? style.fillOpacity : 0.15);
                ctx.beginPath();
                ctx.moveTo(geom.centerStart.x, geom.centerStart.y);
                ctx.lineTo(geom.centerEnd.x, geom.centerEnd.y);
                ctx.lineTo(geom.lowerEnd.x, geom.lowerEnd.y);
                ctx.lineTo(geom.lowerStart.x, geom.lowerStart.y);
                ctx.closePath();
                ctx.fill();
                ctx.restore();
            }

            // 3. Upper border (Blue, 2px)
            if (showUpper) {
                ctx.save();
                ctx.strokeStyle = style.color || '#2962ff';
                ctx.lineWidth = style.width || 2;
                ctx.globalAlpha = alpha;
                ctx.setLineDash(Array.isArray(style.lineDash) ? style.lineDash : []);
                ctx.beginPath();
                ctx.moveTo(geom.upperStart.x, geom.upperStart.y);
                ctx.lineTo(geom.upperEnd.x, geom.upperEnd.y);
                ctx.stroke();
                ctx.restore();
            }

            // 4. Lower border (Red, 2px)
            if (showLower) {
                ctx.save();
                ctx.strokeStyle = style.lowerColor || '#f23645';
                ctx.lineWidth = style.width || 2;
                ctx.globalAlpha = alpha;
                ctx.setLineDash(Array.isArray(style.lineDash) ? style.lineDash : []);
                ctx.beginPath();
                ctx.moveTo(geom.lowerStart.x, geom.lowerStart.y);
                ctx.lineTo(geom.lowerEnd.x, geom.lowerEnd.y);
                ctx.stroke();
                ctx.restore();
            }

            // 5. Center regression line (Blue, 1.5px)
            ctx.save();
            ctx.strokeStyle = style.centerColor || '#2962ff';
            ctx.lineWidth = style.centerWidth || 1.5;
            ctx.globalAlpha = alpha;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(geom.centerStart.x, geom.centerStart.y);
            ctx.lineTo(geom.centerEnd.x, geom.centerEnd.y);
            ctx.stroke();
            ctx.restore();

            // 6. Calculation text readout (Pearson R) below lowerStart
            if (style.showPearsonR !== false && geom.reg && geom.reg.pearsonR != null) {
                ctx.save();
                ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                ctx.fillStyle = style.color || '#2962ff';
                ctx.textAlign = 'left';
                ctx.textBaseline = 'top';
                var rVal = geom.reg.pearsonR;
                var rStr = String(rVal);
                if (rStr.length > 18) rStr = rVal.toFixed(16);
                ctx.fillText(rStr, geom.lowerStart.x, geom.lowerStart.y + 15);
                ctx.restore();
            }

            // 7. Axis labels (Price on Y-axis, Date on X-axis) — selected only
            if (!isPreview && isSelected && this.showAxisLabels) {
                var pixels = this.getPixels(chartState);
                if (pixels && pixels.length >= 2) {
                    this.drawAxisLabels(ctx, pixels, chartState);
                }
            }
        }

        // 4 corner handles: [Start Top, Start Bottom, End Top, End Bottom]
        getAnchorPoints(pixels) {
            var g = this._lastGeom;
            if (!g) return pixels || [];
            return [g.startTop, g.startBottom, g.endTop, g.endBottom];
        }

        getEdgeSegments(pixels, chartState) {
            var g = this._lastGeom;
            if (!g && chartState) g = this._getGeometry(chartState);
            if (!g) return [];
            return [
                { p1: g.upperStart, p2: g.upperEnd },
                { p1: g.lowerStart, p2: g.lowerEnd },
                { p1: g.centerStart, p2: g.centerEnd }
            ];
        }

        getFillShape(pixels) {
            var g = this._lastGeom;
            if (!g) return null;
            return [g.upperStart, g.upperEnd, g.lowerEnd, g.lowerStart];
        }

        drawHandles(ctx, pixels, isSelected, chartState) {
            var g = this._lastGeom;
            if (!g && chartState) g = this._getGeometry(chartState);
            if (g) {
                var pts = [g.startTop, g.startBottom, g.endTop, g.endBottom];
                for (var i = 0; i < pts.length; i++) {
                    if (pts[i]) this._drawHandle(ctx, pts[i], isSelected, false);
                }
            } else if (pixels) {
                for (var i = 0; i < pixels.length; i++) {
                    if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
                }
            }
        }
    }

    // =============================================================================
    // Pitchfork Family (Phase 3.3)
    // =============================================================================
    //
    // Renders a median line + two outer tines (all parallel) with optional fill
    // regions between them. Three anchor points determine geometry:
    //   p1 = median origin (or variant-specific reference)
    //   p2 = upper tine reference
    //   p3 = lower tine reference
    //
    // All three lines share a common slope computed from (medianOrigin → midpoint23).
    // Lines extend as rays from their reference points in that direction.
    //
    // Subclasses override geometry hooks only (getMedianOrigin, getUpperRef,
    // getLowerRef, getDirection).

    class BasePitchforkDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
        }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 3;
        }

        // -----------------------------------------------------------------------
        // Geometry hooks — override in variant subclasses
        // -----------------------------------------------------------------------
        getMedianOrigin(pixels) { return pixels[0]; }
        getUpperRef(pixels)     { return pixels[1]; }
        getLowerRef(pixels)     { return pixels[2]; }
        getDirection(pixels) {
            if (!pixels || pixels.length < 3) return { x: 1, y: 0 };
            var m23 = GeometryUtils.midpoint(pixels[1], pixels[2]);
            var o = this.getMedianOrigin(pixels);
            return { x: m23.x - o.x, y: m23.y - o.y };
        }

        // -----------------------------------------------------------------------
        // Pixels from data coords
        // -----------------------------------------------------------------------
        getPixels(chartState) {
            if (!chartState) return [];
            return this.coords.map(function(c) { return chartState.coordToPixel(c); }).filter(Boolean);
        }

        // -----------------------------------------------------------------------
        // Ray clipping helper — returns [start, canvasEdgeEnd] for a ray
        // from `pixel` in direction (ux, uy) clipped to canvas [0,0,cw,ch]
        // -----------------------------------------------------------------------
        _clipRay(pixel, ux, uy, cw, ch) {
            var dirPt = { x: pixel.x + ux, y: pixel.y + uy };
            var end = GeometryUtils.rayCanvasIntersection(pixel, dirPt, cw, ch);
            if (!end) return [pixel, pixel];
            return [pixel, end];
        }

        // -----------------------------------------------------------------------
        // Main draw
        // -----------------------------------------------------------------------
        draw(ctx, chartState, isSelected) {
            var pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;

            var drawPixels = pixels.slice();
            if (pixels.length < this.pointCount && this.currentPos) {
                while (drawPixels.length < this.pointCount) drawPixels.push(this.currentPos);
            }
            if (drawPixels.length < 2) return;

            var bounds = this._computeBounds(drawPixels, cw, ch);
            var isPreview = pixels.length < this.pointCount;
            if (!bounds) return;

            if (isPreview) ctx.globalAlpha = 0.4;

            // ---- Fills (behind lines) ----
            var fillAlpha = this.style.fillOpacity != null ? this.style.fillOpacity : 0.08;
            var fillColor = this.style.fillColor || this.style.color || '#2962ff';
            if (bounds.fillUpper && bounds.fillUpper.length >= 3) {
                ctx.save();
                ctx.globalAlpha = isPreview ? fillAlpha * 0.4 : fillAlpha;
                ctx.fillStyle = fillColor;
                ctx.beginPath();
                ctx.moveTo(bounds.fillUpper[0].x, bounds.fillUpper[0].y);
                for (var i = 1; i < bounds.fillUpper.length; i++) ctx.lineTo(bounds.fillUpper[i].x, bounds.fillUpper[i].y);
                ctx.closePath();
                ctx.fill();
                ctx.restore();
            }
            if (bounds.fillLower && bounds.fillLower.length >= 3) {
                ctx.save();
                ctx.globalAlpha = isPreview ? fillAlpha * 0.4 : fillAlpha;
                ctx.fillStyle = fillColor;
                ctx.beginPath();
                ctx.moveTo(bounds.fillLower[0].x, bounds.fillLower[0].y);
                for (var i = 1; i < bounds.fillLower.length; i++) ctx.lineTo(bounds.fillLower[i].x, bounds.fillLower[i].y);
                ctx.closePath();
                ctx.fill();
                ctx.restore();
            }

            // ---- Handle segment (dashed) ----
            if (bounds.handle && this.style.showHandle !== false) {
                ctx.save();
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = this.style.color || '#2962ff';
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.4;
                ctx.beginPath();
                ctx.moveTo(bounds.handle[0].x, bounds.handle[0].y);
                ctx.lineTo(bounds.handle[1].x, bounds.handle[1].y);
                ctx.stroke();
                ctx.restore();
            }

            // ---- Outer tines (TradingView convention: blue upper, red lower) ----
            var upperTineColor = this.style.upperTineColor || this.style.tineColor || this.style.color || '#2962ff';
            var lowerTineColor = this.style.lowerTineColor || this.style.tineColor || '#F44336';
            var tineWidth = this.style.tineWidth != null ? this.style.tineWidth : (this.style.width || 1.5);
            var tineAlpha = isPreview ? 0.4 : (this.style.tineOpacity != null ? this.style.tineOpacity : (this.style.opacity != null ? this.style.opacity : 0.7));
            var tineDash = this.style.tineLineDash || this.style.lineDash || [];

            if (bounds.upper) {
                ctx.save();
                ctx.strokeStyle = upperTineColor;
                ctx.lineWidth = tineWidth;
                ctx.globalAlpha = tineAlpha;
                ctx.setLineDash(tineDash);
                ctx.beginPath();
                ctx.moveTo(bounds.upper[0].x, bounds.upper[0].y);
                ctx.lineTo(bounds.upper[1].x, bounds.upper[1].y);
                ctx.stroke();
                ctx.restore();
            }
            if (bounds.lower) {
                ctx.save();
                ctx.strokeStyle = lowerTineColor;
                ctx.lineWidth = tineWidth;
                ctx.globalAlpha = tineAlpha;
                ctx.setLineDash(tineDash);
                ctx.beginPath();
                ctx.moveTo(bounds.lower[0].x, bounds.lower[0].y);
                ctx.lineTo(bounds.lower[1].x, bounds.lower[1].y);
                ctx.stroke();
                ctx.restore();
            }

            // ---- Median (drawn last, on top) ----
            var medColor = this.style.medianColor || this.style.color || '#2962ff';
            var medWidth = this.style.medianWidth != null ? this.style.medianWidth : (this.style.width || 2);
            var medAlpha = isPreview ? 0.4 : (this.style.medianOpacity != null ? this.style.medianOpacity : (this.style.opacity != null ? this.style.opacity : 1));
            var medDash = this.style.medianLineDash || this.style.lineDash || [];

            if (bounds.median) {
                ctx.save();
                ctx.strokeStyle = medColor;
                ctx.lineWidth = medWidth;
                ctx.globalAlpha = medAlpha;
                ctx.setLineDash(medDash);
                ctx.beginPath();
                ctx.moveTo(bounds.median[0].x, bounds.median[0].y);
                ctx.lineTo(bounds.median[1].x, bounds.median[1].y);
                ctx.stroke();
                ctx.restore();
            }

            // ---- Axis labels (price right, time bottom) + date-range highlight ----
            // Selected only — matches TradingView's transient selection highlight.
            // drawAxisLabels() renders both the min→max highlight band (via the
            // default getAxisHighlightGroups — all 3 anchors as one span) and
            // the per-point badges, so no separate highlight code is needed here.
            if (!isPreview && isSelected && pixels.length >= 3) {
                this.drawAxisLabels(ctx, pixels, chartState);
            }

            if (isPreview) ctx.globalAlpha = 1;
        }

        // -----------------------------------------------------------------------
        // Compute all geometry for the current pixel positions
        // -----------------------------------------------------------------------
        _computeBounds(pixels, cw, ch) {
            if (pixels.length < 2) return null;
            var origin = this.getMedianOrigin(pixels);
            var dir = this.getDirection(pixels);
            if (!origin || !dir) return null;
            var len = Math.sqrt(dir.x * dir.x + dir.y * dir.y);
            if (len < 1) return null;
            var ux = dir.x / len, uy = dir.y / len;

            var upperRef = this.getUpperRef(pixels);
            var lowerRef = this.getLowerRef(pixels);
            var extendRight = this.style.extendRight !== false;
            var far = 100000;

            // Clip lines as rays (or infinite lines if extendRight is false)
            var medianPts = null, upperPts = null, lowerPts = null;
            var fillUpper = null, fillLower = null;
            var handle = null;

            if (extendRight) {
                medianPts = this._clipRay(origin, ux, uy, cw, ch);
                if (upperRef) upperPts = this._clipRay(upperRef, ux, uy, cw, ch);
                if (lowerRef) lowerPts = this._clipRay(lowerRef, ux, uy, cw, ch);
            } else {
                var farP = { x: origin.x + ux * far, y: origin.y + uy * far };
                medianPts = GeometryUtils.lineCanvasIntersection(origin, farP, cw, ch);
                if (!medianPts || medianPts.length < 2) medianPts = [origin, farP];
                if (upperRef) {
                    var upFar = { x: upperRef.x + ux * far, y: upperRef.y + uy * far };
                    upperPts = GeometryUtils.lineCanvasIntersection(upperRef, upFar, cw, ch);
                    if (!upperPts || upperPts.length < 2) upperPts = [upperRef, upFar];
                }
                if (lowerRef) {
                    var lowFar = { x: lowerRef.x + ux * far, y: lowerRef.y + uy * far };
                    lowerPts = GeometryUtils.lineCanvasIntersection(lowerRef, lowFar, cw, ch);
                    if (!lowerPts || lowerPts.length < 2) lowerPts = [lowerRef, lowFar];
                }
            }
            if (!medianPts) return null;

            // Handle segment
            if (pixels.length >= 3 && pixels[1] && pixels[2]) {
                handle = [pixels[1], pixels[2]];
            } else if (pixels.length >= 2 && pixels[1] && this.currentPos) {
                var cp = this.currentPos;
                if (cp && cp.x != null) handle = [pixels[1], cp];
            }

            // Fill regions (quadrilateral between median and each tine)
            if (upperPts && upperRef && lowerPts && lowerRef &&
                this.style.fillColor && this.style.fillOpacity > 0) {
                fillUpper = [origin, medianPts[1], upperPts[1], upperRef];
                fillLower = [origin, medianPts[1], lowerPts[1], lowerRef];
            }

            return {
                median: medianPts,
                upper: upperPts,
                lower: lowerPts,
                handle: handle,
                fillUpper: fillUpper,
                fillLower: fillLower
            };
        }

        // -----------------------------------------------------------------------
        // Hit testing
        // -----------------------------------------------------------------------
        getFillShape(pixels) {
            if (!pixels || pixels.length < 3) return null;
            // Use a large canvas to capture full ray extent
            var bounds = this._computeBounds(pixels, 100000, 100000);
            if (!bounds) return null;
            var verts = [];
            if (bounds.fillUpper) {
                for (var i = 0; i < bounds.fillUpper.length; i++) verts.push(bounds.fillUpper[i]);
            }
            if (bounds.fillLower) {
                for (var i = 0; i < bounds.fillLower.length; i++) verts.push(bounds.fillLower[i]);
            }
            return verts.length > 0 ? verts : null;
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 3) return [];
            var cw = 100000, ch = 100000;
            if (chartState && chartState.width != null) cw = chartState.width;
            if (chartState && chartState.height != null) ch = chartState.height;
            var bounds = this._computeBounds(pixels, cw, ch);
            if (!bounds) return [];
            var segs = [];
            if (bounds.median) segs.push({ p1: bounds.median[0], p2: bounds.median[1] });
            if (bounds.upper) segs.push({ p1: bounds.upper[0], p2: bounds.upper[1] });
            if (bounds.lower) segs.push({ p1: bounds.lower[0], p2: bounds.lower[1] });
            return segs;
        }

        getAnchorPoints(pixels) {
            if (!pixels) return [];
            var result = [];
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) result.push(pixels[i]);
            }
            return result;
        }

        getMidpoints(pixels) {
            var mids = [];
            if (!pixels || pixels.length < 2) return mids;
            mids.push(GeometryUtils.midpoint(pixels[0], pixels[1]));
            if (pixels.length >= 3) {
                mids.push(GeometryUtils.midpoint(pixels[1], pixels[2]));
            }
            return mids;
        }

        // -----------------------------------------------------------------------
        // Handles
        // -----------------------------------------------------------------------
        drawHandles(ctx, pixels, isSelected) {
            if (!pixels) return;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this._drawAnchor(ctx, pixels[i], isSelected);
            }
            var mids = this.getMidpoints(pixels);
            for (var mi = 0; mi < mids.length; mi++) {
                if (mids[mi]) this._drawMidpoint(ctx, mids[mi], isSelected);
            }
        }

        _drawAnchor(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        _drawMidpoint(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            var s = 5;
            ctx.beginPath();
            ctx.moveTo(p.x, p.y - s);
            ctx.lineTo(p.x + s, p.y);
            ctx.lineTo(p.x, p.y + s);
            ctx.lineTo(p.x - s, p.y);
            ctx.closePath();
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        // -----------------------------------------------------------------------
        // Drag / translate
        // -----------------------------------------------------------------------
        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !chartState || !this.coords) return;
            var handleIndex = draggedHandle.handleIndex;
            if (handleIndex < 0 || handleIndex >= this.coords.length) return;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    this.coords[handleIndex] = { logical: snapInfo.logical, price: snapInfo.price, time: snapInfo.time };
                    return;
                }
            }

            var oldCoord = this.coords[handleIndex];
            if (!oldCoord) return;

            var price = newPrice != null ? newPrice : oldCoord.price;
            var logical = oldCoord.logical;
            if (pixelX != null && chartState.xToLogical) {
                var newLogical = chartState.xToLogical(pixelX);
                if (newLogical != null) logical = newLogical;
            } else if (pixelX != null && chartState.xToTime) {
                var newTime = chartState.xToTime(pixelX);
                if (newTime != null) logical = newTime;
            }

            this.coords[handleIndex] = { logical: logical, price: price };
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }
    }

    // --- Classic Pitchfork (Andrews) ---
    // Median from p1 through midpoint(p2,p3). Outer tines through p2, p3.
    class Pitchfork extends BasePitchforkDrawing {}

    // --- Schiff Pitchfork (TradingView parity) ---
    // Click 1 = P1 (Origin), Click 2 = P2 (Upper swing), Click 3 = P3 (Lower swing)
    // ShiftedOrigin.x = P1.x, ShiftedOrigin.y = (P1.y + P2.y) / 2 — TradingView's own
    // wording: "an additional point calculated based on the X coordinate of point 1
    // and the Y coordinate of the middle between point 1 and point 2."
    // (https://www.tradingview.com/support/solutions/43000657911-auto-pitchfork/)
    // Median through ShiftedOrigin and midpoint(P2,P3). P2/P3 anchor the OUTER
    // upper/lower lines directly; the inner tines are derived at half that
    // perpendicular offset from the median — so 2 anchors (P2, P3) control all
    // 5 lines. Construction lines P1→P2 and P2→P3 shown only when selected
    // (no P1→P3 — that isn't part of the actual construction).
    class SchiffPitchfork extends BasePitchforkDrawing {
        // Schiff origin — TradingView's own docs, verbatim: "An additional point
        // is calculated, based on the X coordinate of the point 1 and the Y
        // coordinate of the middle between point 1 and point 2." So X = P1.x
        // (NOT the midpoint — Modified Schiff is the variant that midpoints X),
        // Y = midpoint(P1.y, P2.y). Verified directly against
        // https://www.tradingview.com/support/solutions/43000657911-auto-pitchfork/
        // on 2026-08-15 — do not "fix" this to average X without re-checking
        // that source; it looks asymmetric but it is correct.
        getMedianOrigin(pixels) {
            if (!pixels || pixels.length < 2) return null;
            return { x: pixels[0].x, y: (pixels[0].y + pixels[1].y) / 2 };
        }
        getUpperRef(pixels) { return pixels ? pixels[1] : null; }
        getLowerRef(pixels) { return pixels ? pixels[2] : null; }
        getDirection(pixels) {
            if (!pixels || pixels.length < 3) return { x: 1, y: 0 };
            var so = this.getMedianOrigin(pixels);
            var m23 = GeometryUtils.midpoint(pixels[1], pixels[2]);
            return { x: m23.x - so.x, y: m23.y - so.y };
        }
        // Where the 4 non-median lines' visible rays start: at their crossing
        // with the P2→P3 line. Correct for Schiff because upperRef/lowerRef
        // (P2/P3) already sit ON that line — see InsidePitchfork's override
        // for why that assumption doesn't hold for every variant.
        _forkStartsAtOriginBoundary() { return false; }

        // -----------------------------------------------------------------------
        // Full Schiff geometry — 5 infinite lines + fills + construction segs
        // -----------------------------------------------------------------------
        _computeForkGeometry(pixels, cw, ch) {
            if (!pixels || pixels.length < 3) return null;
            var p1 = pixels[0], p2 = pixels[1], p3 = pixels[2];
            var so = this.getMedianOrigin(pixels);
            var upperRef = this.getUpperRef(pixels);
            var lowerRef = this.getLowerRef(pixels);
            if (!so || !upperRef || !lowerRef) return null;
            // Direction comes from the subclass hook (e.g. Schiff: toward
            // midpoint(P2,P3); Inside: toward P3 directly) — never hardcode
            // it here, or variant-specific direction overrides get silently
            // ignored by whichever variant inherits this shared method.
            var dir = this.getDirection(pixels);
            var len = Math.sqrt(dir.x * dir.x + dir.y * dir.y);
            if (len < 1) return null;
            var ux = dir.x / len, uy = dir.y / len;
            var nx = -uy, ny = ux;
            var far = 100000;
            var extendRight = this.style.extendRight !== false;

            // Rays (default, TradingView-compatible): start exactly at the anchor
            // and extend only forward (toward increasing time), never backward.
            // Bidirectional line: only used when extendRight is explicitly off.
            function clipLine(pt, dx, dy) {
                if (extendRight) {
                    var dirPt = { x: pt.x + dx, y: pt.y + dy };
                    var end = GeometryUtils.rayCanvasIntersection(pt, dirPt, cw, ch);
                    return end ? [pt, end] : [pt, pt];
                }
                var a = { x: pt.x - dx * far, y: pt.y - dy * far };
                var b = { x: pt.x + dx * far, y: pt.y + dy * far };
                return GeometryUtils.lineCanvasIntersection(a, b, cw, ch);
            }
            // Where a line (through pt, direction ux,uy) crosses the infinite
            // line through P2→P3. The median is explicitly NOT run through this
            // — it stays "the primary central pitchfork line" and keeps
            // extending from the origin regardless of where it crosses P2→P3.
            // The other 4 lines terminate exactly here: outerUpper/outerLower
            // trivially resolve to P2/P3 themselves (they're ON the P2→P3 line
            // by definition); the inner tines get a real line-line intersection
            // since they don't pass through P2 or P3.
            var p2p3dx = p3.x - p2.x, p2p3dy = p3.y - p2.y;
            function intersectWithP2P3(pt) {
                var denom = ux * p2p3dy - uy * p2p3dx;
                if (Math.abs(denom) < 1e-9) return pt; // parallel to P2→P3 — nothing to clip against
                var t = ((p2.x - pt.x) * p2p3dy - (p2.y - pt.y) * p2p3dx) / denom;
                return { x: pt.x + t * ux, y: pt.y + t * uy };
            }
            // Alternative boundary: pull a point back/forward along the fork's
            // own direction so it sits on the SAME cross-section as the origin
            // (perpendicular through 'so'). Unlike intersectWithP2P3, this
            // treats any two points that are symmetric about 'so' identically
            // — which matters for Inside Pitchfork, where upperRef/lowerRef
            // (P1/P2) are symmetric about so by construction, but only one of
            // them (P2) happens to sit ON the P2→P3 line. Using P2→P3 there
            // would treat P1 and P2 asymmetrically (one trivially already on
            // the boundary, one not) — using the origin cross-section instead
            // treats both the same way.
            function alignToOriginCrossSection(pt) {
                var t = (pt.x - so.x) * ux + (pt.y - so.y) * uy;
                return { x: pt.x - t * ux, y: pt.y - t * uy };
            }
            // Signed perpendicular distance from the median origin, in the +n direction.
            function signedDist(pt) {
                return ((pt.y - so.y) * dir.x - (pt.x - so.x) * dir.y) / len;
            }
            var self = this;
            function resolveLineStart(pt) {
                return self._forkStartsAtOriginBoundary() ? alignToOriginCrossSection(pt) : intersectWithP2P3(pt);
            }

            // Median stroke: unclipped ray from the origin — continues past
            // the P2→P3 boundary in both rendering directions (it's a ray, so
            // only "past" is forward; it simply isn't terminated at P2→P3).
            var medianPts = clipLine(so, ux, uy);

            var dU = signedDist(upperRef);
            var dL = signedDist(lowerRef);
            var innerUpperRefPt = { x: so.x + nx * dU / 2, y: so.y + ny * dU / 2 };
            var innerLowerRefPt = { x: so.x + nx * dL / 2, y: so.y + ny * dL / 2 };

            // All 4 non-median lines' visible rays start at their crossing
            // with the P2→P3 boundary — never at the raw reference point
            // itself. For Schiff, upperRef/lowerRef ARE P2/P3, which already
            // sit ON that line, so this is a no-op there (identical result
            // either way). For Inside, upperRef/lowerRef are P1/P2 — using
            // their P2→P3 crossing (not their raw position) is what keeps the
            // fork confined to the P2–P3 region instead of stretching back to
            // P1: the P1→P2→P3 construction zigzag stays visually separate
            // from the fork, matching the reference. The anchor handles
            // themselves still render at the true raw P1/P2/P3 positions
            // (drawHandles uses the raw pixels, untouched by any of this).
            // (Inside Pitchfork overrides _forkStartsAtOriginBoundary() to use
            // the origin cross-section instead, for the symmetry reason above.)
            var outerUpperStart = resolveLineStart(upperRef);
            var outerLowerStart = resolveLineStart(lowerRef);
            var innerUpperStart = resolveLineStart(innerUpperRefPt);
            var innerLowerStart = resolveLineStart(innerLowerRefPt);
            var medianFillStart = resolveLineStart(so);            // fill-only; stroke uses 'so' directly

            var outerUpperPts = clipLine(outerUpperStart, ux, uy);
            var outerLowerPts = clipLine(outerLowerStart, ux, uy);
            var innerUpperPts = clipLine(innerUpperStart, ux, uy);
            var innerLowerPts = clipLine(innerLowerStart, ux, uy);
            var medianFillPts = clipLine(medianFillStart, ux, uy);

            // Sort fill-vertices along direction vector
            function sortAlongDir(a, b) {
                return (a.x * ux + a.y * uy) - (b.x * ux + b.y * uy);
            }

            // 5 lines (outerUpper, innerUpper, median, innerLower, outerLower)
            // bound 4 bands: 2 inner (median-adjacent) + 2 outer (P2/P3-adjacent).
            // All 4 fill polygons use the P2→P3-aligned starts (medianFillPts,
            // not the raw medianPts) so every fill edge begins at the exact
            // same boundary — the fill's leading edge traces the P2→P3 line.
            function fillBetween(ptsA, ptsB) {
                if (!ptsA || !ptsB) return null;
                var a = ptsA.slice().sort(sortAlongDir);
                var b = ptsB.slice().sort(sortAlongDir);
                return [a[0], a[1], b[1], b[0]];
            }
            var fillInnerUpper = fillBetween(medianFillPts, innerUpperPts);
            var fillInnerLower = fillBetween(medianFillPts, innerLowerPts);
            var fillOuterUpper = fillBetween(innerUpperPts, outerUpperPts);
            var fillOuterLower = fillBetween(innerLowerPts, outerLowerPts);

            return {
                median: medianPts,
                innerUpper: innerUpperPts, innerLower: innerLowerPts,
                outerUpper: outerUpperPts, outerLower: outerLowerPts,
                fillInnerUpper: fillInnerUpper, fillInnerLower: fillInnerLower,
                fillOuterUpper: fillOuterUpper, fillOuterLower: fillOuterLower,
                construction2: [p2, p3],
                so: so, p1: p1, p2: p2, p3: p3
            };
        }

        // -----------------------------------------------------------------------
        // Draw — TradingView rendering (colors resolved from style, customizable)
        // -----------------------------------------------------------------------
        draw(ctx, chartState, isSelected) {
            var pixels = this.getPixels(chartState);
            if (!pixels || pixels.length === 0) return;
            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;

            // With only the first anchor placed there's no fork yet, but the
            // user should still see a live solid line from P1 to the cursor
            // (like a trend-line placement preview) so it's clear where P2
            // will land — not just a lone dot with nothing tracking the mouse.
            var style = this.style || {};
            var constructionColor = style.constructionColor || '#F44336';
            function drawSolidRedLine(a, b) {
                ctx.save();
                ctx.setLineDash([]);
                ctx.strokeStyle = constructionColor;
                ctx.lineWidth = 1;
                ctx.globalAlpha = 1;
                ctx.beginPath();
                ctx.moveTo(a.x, a.y);
                ctx.lineTo(b.x, b.y);
                ctx.stroke();
                ctx.restore();
            }

            // Resolve line/fill colors once (customizable, TradingView defaults
            // as fallback) — shared by both the 2-point live preview and the
            // final complete rendering below.
            var fillInnerColor = style.fillInnerColor || '#6edcdc';
            var fillOuterColor = style.fillOuterColor || '#78aaff';
            var fillAlpha = style.fillOpacity != null ? style.fillOpacity : 0.18;
            var medianColor = style.medianColor || '#F44336';
            var medianWidth = style.medianWidth != null ? style.medianWidth : 2;
            var medianOpacity = style.medianOpacity != null ? style.medianOpacity : 1;
            var outerColor = style.upperTineColor || style.tineColor || '#2196F3';
            var innerColor = style.lowerTineColor || '#089981';
            var tineWidth = style.tineWidth != null ? style.tineWidth : 2;
            var tineOpacity = style.tineOpacity != null ? style.tineOpacity : 0.8;

            function fillPoly(poly, color) {
                if (!poly) return;
                ctx.save();
                ctx.globalAlpha = fillAlpha;
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.moveTo(poly[0].x, poly[0].y);
                for (var pi = 1; pi < poly.length; pi++) ctx.lineTo(poly[pi].x, poly[pi].y);
                ctx.closePath();
                ctx.fill();
                ctx.restore();
            }
            function drawLine(pts, color, width, dash, alpha) {
                if (!pts || pts.length < 2) return;
                ctx.save();
                ctx.strokeStyle = color;
                ctx.lineWidth = width;
                ctx.globalAlpha = alpha != null ? alpha : 1;
                if (dash && dash.length) ctx.setLineDash(dash);
                ctx.beginPath();
                ctx.moveTo(pts[0].x, pts[0].y);
                ctx.lineTo(pts[1].x, pts[1].y);
                ctx.stroke();
                ctx.restore();
            }
            // Full fork rendering — fills + the 4 terminated lines + the
            // unclipped median always render; the P1↔P2 / P2↔P3 construction
            // lines are opt-in (only while selected, matching every other
            // pitchfork's construction-line behavior). Shared by the "2 points
            // + live cursor as a stand-in P3" preview and the final complete
            // drawing, so the preview looks exactly like what committing P3
            // there would produce.
            function renderFork(geo, p1p2, showConstruction) {
                fillPoly(geo.fillOuterUpper, fillOuterColor);
                fillPoly(geo.fillInnerUpper, fillInnerColor);
                fillPoly(geo.fillInnerLower, fillInnerColor);
                fillPoly(geo.fillOuterLower, fillOuterColor);
                if (showConstruction) {
                    if (p1p2) drawSolidRedLine(p1p2[0], p1p2[1]);
                    if (geo.construction2) drawSolidRedLine(geo.construction2[0], geo.construction2[1]);
                }
                drawLine(geo.outerUpper, outerColor, tineWidth, null, tineOpacity);
                drawLine(geo.outerLower, outerColor, tineWidth, null, tineOpacity);
                drawLine(geo.innerUpper, innerColor, tineWidth, null, tineOpacity);
                drawLine(geo.innerLower, innerColor, tineWidth, null, tineOpacity);
                drawLine(geo.median, medianColor, medianWidth, null, medianOpacity);
            }

            // STATE: POINT_1_ONLY — no line/fork yet, just the anchor plus a
            // live P1→cursor preview so it's clear where P2 is heading.
            if (pixels.length === 1) {
                this.drawHandle(ctx, pixels[0], true);
                if (this.currentPos) drawSolidRedLine(pixels[0], this.currentPos);
                return;
            }

            // STATE: POINT_2_SELECTED — P1→P2 is confirmed. If the cursor
            // yields a valid (non-degenerate) fork, show the FULL live 5-line/
            // 4-pipe preview using the cursor as a stand-in P3 — exactly what
            // committing P3 there would produce. Falls back to the simple
            // P1→P2 + P2→cursor preview if the geometry momentarily can't be
            // computed (keeps placement visually stable, no blank-frame flicker).
            if (pixels.length === 2) {
                this.drawHandle(ctx, pixels[0], true);
                this.drawHandle(ctx, pixels[1], true);
                var previewGeo = this.currentPos
                    ? this._computeForkGeometry([pixels[0], pixels[1], this.currentPos], cw, ch)
                    : null;
                if (previewGeo) {
                    renderFork(previewGeo, [pixels[0], pixels[1]], true);
                } else {
                    drawSolidRedLine(pixels[0], pixels[1]);
                    if (this.currentPos) drawSolidRedLine(pixels[1], this.currentPos);
                }
                return;
            }

            // STATE: COMPLETE — all 3 anchors are real.
            var geo = this._computeForkGeometry(pixels, cw, ch);
            if (!geo) {
                // Degenerate direction (median direction vector ~0) — extremely
                // rare with 3 fixed real anchors, but stay stable rather than blank.
                this.drawHandle(ctx, pixels[0], true);
                this.drawHandle(ctx, pixels[1], true);
                this.drawHandle(ctx, pixels[2], true);
                drawSolidRedLine(pixels[0], pixels[1]);
                drawSolidRedLine(pixels[1], pixels[2]);
                return;
            }

            this._selected = !!isSelected;
            // Fills/lines always render; the P1↔P2 and P2↔P3 construction
            // lines only while selected (unchanged from before).
            renderFork(geo, [pixels[0], pixels[1]], isSelected);

            // Axis labels (price right, time bottom) — selected only
            if (isSelected && pixels.length >= 3) {
                this.drawAxisLabels(ctx, pixels, chartState);
            }
        }

        // -----------------------------------------------------------------------
        // Hit testing
        // -----------------------------------------------------------------------
        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 3) return [];
            var cw = 100000, ch = 100000;
            if (chartState && chartState.width != null) cw = chartState.width;
            if (chartState && chartState.height != null) ch = chartState.height;
            var geo = this._computeForkGeometry(pixels, cw, ch);
            if (!geo) return [];
            var segs = [];
            if (geo.median) segs.push({ p1: geo.median[0], p2: geo.median[1] });
            if (geo.innerUpper) segs.push({ p1: geo.innerUpper[0], p2: geo.innerUpper[1] });
            if (geo.innerLower) segs.push({ p1: geo.innerLower[0], p2: geo.innerLower[1] });
            if (geo.outerUpper) segs.push({ p1: geo.outerUpper[0], p2: geo.outerUpper[1] });
            if (geo.outerLower) segs.push({ p1: geo.outerLower[0], p2: geo.outerLower[1] });
            // Construction line (P2↔P3) is hit-testable while selected (visible)
            if (this._selected && geo.construction2) {
                segs.push({ p1: geo.construction2[0], p2: geo.construction2[1] });
            }
            return segs;
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 3) return null;
            var geo = this._computeForkGeometry(pixels, 100000, 100000);
            if (!geo) return null;
            // Full band between the outer upper and outer lower parallels (covers all 4 fill regions)
            var up = geo.outerUpper, lp = geo.outerLower;
            if (!up || up.length < 2 || !lp || lp.length < 2) return null;
            return [up[0], up[1], lp[1], lp[0]];
        }
    }

    // --- Modified Schiff Pitchfork (TradingView parity) ---
    // Click 1 = P1 (Origin), Click 2 = P2 (Upper pivot), Click 3 = P3 (Lower pivot)
    // ModifiedOrigin = ((P1.x + P2.x) / 2, (P1.y + P2.y) / 2) — shifts toward P2 in
    //   both time AND price (Schiff shifts the same way; both average x and y).
    // Median through the origin and midpoint(P2,P3). P2/P3 anchor the OUTER upper/
    //   lower lines directly; the inner tines are derived at half that perpendicular
    //   offset from the median — 2 anchors (P2, P3) control all 5 lines. Construction
    //   lines: P1→P2 and P2→P3 only (no P1→P3 — that isn't a real relationship here).
    class ModifiedSchiffPitchfork extends SchiffPitchfork {
        getMedianOrigin(pixels) {
            if (!pixels || pixels.length < 2) return null;
            return {
                x: (pixels[0].x + pixels[1].x) / 2,
                y: (pixels[0].y + pixels[1].y) / 2
            };
        }
        getUpperRef(pixels) { return pixels ? pixels[1] : null; }
        getLowerRef(pixels) { return pixels ? pixels[2] : null; }
    }

    // --- Inside Pitchfork ---
    // Origin M12 = midpoint(P1,P2) (full midpoint, both axes — TradingView Auto
    // Pitchfork: "The median connects the middle of the line between points 1
    // and 2 to point 3" — https://www.tradingview.com/support/solutions/
    // 43000657911-auto-pitchfork/). Median = M12 → P3 directly (NOT toward
    // midpoint(P2,P3) — that's what makes this genuinely distinct from Schiff).
    // Extends SchiffPitchfork purely for the shared 5-line/dual-construction/
    // interactive-preview machinery (_computeForkGeometry, draw(), hit-testing)
    // — same reuse pattern ModifiedSchiffPitchfork already uses.
    //
    // IMPORTANT — tine references are P1/P2, NOT P2/P3 (unlike Schiff): because
    // the median points directly AT P3, P3 sits exactly ON the median by
    // construction (perpendicular distance always exactly 0) — using it as a
    // tine reference collapses that entire side of the fork to zero width.
    // P1 and P2 are the correct references instead: since the origin is their
    // exact midpoint, they're mathematically guaranteed to sit at equal and
    // opposite perpendicular distances from the median
    // (signedDist(P2) === -signedDist(P1), always), which is exactly the
    // symmetric upper/lower split the fork needs.
    //
    // Boundary: unlike Schiff (where upperRef/lowerRef ARE P2/P3, so the
    // P2→P3 line is a natural, symmetric boundary for both), Inside's tine
    // refs are P1/P2 — only P2 sits on the P2→P3 line, so using it as the
    // boundary treats P1 and P2 asymmetrically (one line starts exactly at
    // its anchor, the other starts wherever it happens to cross a line it
    // isn't naturally related to). The origin's cross-section (perpendicular
    // through M12) treats P1 and P2 identically instead, since they're
    // symmetric about M12 by construction.
    class InsidePitchfork extends SchiffPitchfork {
        getMedianOrigin(pixels) {
            if (!pixels || pixels.length < 2) return null;
            return GeometryUtils.midpoint(pixels[0], pixels[1]);
        }
        getDirection(pixels) {
            if (!pixels || pixels.length < 3) return { x: 1, y: 0 };
            var origin = this.getMedianOrigin(pixels);
            return { x: pixels[2].x - origin.x, y: pixels[2].y - origin.y };
        }
        getUpperRef(pixels) { return pixels ? pixels[0] : null; }
        getLowerRef(pixels) { return pixels ? pixels[1] : null; }
        _forkStartsAtOriginBoundary() { return true; }
    }

    // =============================================================================
    // Fibonacci Ratio Engine (Phase 3.4) — Refactored v2
    // =============================================================================
    // Architecture:
    //   RatioDefinitions → GeometryStrategy → GeometryPrimitive[] → GeometryRenderer → Canvas
    //
    // Geometry types: horizontal_levels, parallel_lines, fan, circles, arcs,
    //                 time_levels, spiral, wedge, pitchfan
    //
    // Each tool is a thin config subclass specifying a geometry type.
    // =============================================================================

    // --- Helper: convert coord to pixel via chartState ---
    function _px(chartState, coord) {
        return chartState && chartState.coordToPixel ? chartState.coordToPixel(coord) : null;
    }
    function _priceY(chartState, price) {
        return chartState && chartState.priceToY ? chartState.priceToY(price) : null;
    }
    function _timeX(chartState, time) {
        return chartState && chartState.timeToX ? chartState.timeToX(time) : null;
    }

    // =============================================================================
    // Geometry Strategies — each generates GeometryPrimitive[] from anchors + config
    // =============================================================================
    // GeometryPrimitive types:
    //   { type:'line',   start:{x,y}, end:{x,y}, style }
    //   { type:'circle', center:{x,y}, radius, style }
    //   { type:'arc',    center:{x,y}, radius, startAngle, endAngle, style }
    //   { type:'fill',   vertices:[{x,y}], style }
    //   { type:'label',  pos:{x,y}, text, style:{color,fontFamily,fontSize,align} }
    //
    // style: { color, width, opacity, lineDash, fillColor, fillOpacity }

    var GEOMETRY_STRATEGIES = {};

    // ---- Horizontal Levels (Retracement / Extension) ----
    GEOMETRY_STRATEGIES.horizontal_levels = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < def.anchors) return out;
        var p1 = anchors[0], p2 = anchors[1];

        function makeLabel(r, price) {
            if (style.showLabel === false) return null;
            var prec = style.precision != null ? style.precision : 2;
            return {
                type: 'label',
                text: r.label + ' (' + price.toFixed(prec) + ')',
                style: { color: '#787b86', fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: style.fontSize || 10, align: 'left' }
            };
        }

        function resolveStyle(r) {
            return {
                color: style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86',
                width: style.ratioWidths && style.ratioWidths[r.value] || style.width || 1,
                opacity: style.opacity != null ? style.opacity : 0.8,
                lineDash: style.ratioLineDash && style.ratioLineDash[r.value] || style.lineDash || []
            };
        }

        if (def.anchors === 2) {
            var minP = Math.min(p1.price, p2.price);
            var maxP = Math.max(p1.price, p2.price);
            var rng = maxP - minP;
            if (rng === 0) return out;
            var st = (def.direction === 'right') ? Math.min(p1.time, p2.time) : null;
            var sx = st != null ? _timeX(chartState, st) : 0;
            if (st != null && sx == null) sx = 0;

            // Collect pixel levels
            var pxLevels = [];
            for (var ri = 0; ri < def.ratios.length; ri++) {
                var r = def.ratios[ri];
                if (r.visible === false) continue;
                var price = minP + rng * r.value;
                var y = _priceY(chartState, price);
                if (y == null) continue;
                pxLevels.push({ value: r.value, y: y, startX: sx, endX: cw, price: price, label: r.label, style: resolveStyle(r) });
            }

            // Fill primitives
            if (def.fills) {
                for (var fi = 0; fi < def.fills.length; fi++) {
                    var f = def.fills[fi];
                    var fl = null, tl = null;
                    for (var pi = 0; pi < pxLevels.length; pi++) {
                        if (pxLevels[pi].value === f.from) fl = pxLevels[pi];
                        if (pxLevels[pi].value === f.to) tl = pxLevels[pi];
                    }
                    if (fl && tl) {
                        out.push({
                            type: 'fill',
                            vertices: [{x:fl.startX,y:fl.y},{x:fl.endX,y:fl.y},{x:tl.endX,y:tl.y},{x:tl.startX,y:tl.y}],
                            style: { color: f.color || style.fillColor || '#787b86', opacity: f.opacity != null ? f.opacity : (style.fillOpacity != null ? style.fillOpacity : 0.08) }
                        });
                    }
                }
            }

            // Line + label primitives
            for (var pi = 0; pi < pxLevels.length; pi++) {
                var lv = pxLevels[pi];
                var ls = lv.style;
                out.push({ type: 'line', start: {x:lv.startX,y:lv.y}, end: {x:lv.endX,y:lv.y}, style: { color: ls.color, width: ls.width, opacity: ls.opacity, lineDash: ls.lineDash } });
                var lbl = makeLabel({ label: lv.label, value: lv.value }, lv.price);
                if (lbl) { lbl.pos = { x: lv.endX + 4, y: lv.y }; out.push(lbl); }
            }
        } else if (def.anchors === 3) {
            var p3 = anchors[2];
            var wave = p2.price - p1.price;
            var st = (def.direction === 'right') ? p3.time : null;
            var sx = st != null ? _timeX(chartState, st) : 0;
            if (st != null && sx == null) sx = 0;

            for (var ri = 0; ri < def.ratios.length; ri++) {
                var r = def.ratios[ri];
                if (r.visible === false) continue;
                var price = p3.price + wave * r.value;
                var y = _priceY(chartState, price);
                if (y == null) continue;
                var ls = resolveStyle(r);
                out.push({ type: 'line', start: {x:sx,y:y}, end: {x:cw,y:y}, style: { color: ls.color, width: ls.width, opacity: ls.opacity, lineDash: ls.lineDash } });
                var lbl = makeLabel(r, price);
                if (lbl) { lbl.pos = { x: cw + 4, y: y }; out.push(lbl); }
            }
        }
        return out;
    };

    // ---- Parallel Lines (Fib Channel) ----
    GEOMETRY_STRATEGIES.parallel_lines = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 3) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]), _px(chartState, anchors[2]) ];
        if (!pp[0] || !pp[1] || !pp[2]) return out;
        var vx = pp[1].x - pp[0].x, vy = pp[1].y - pp[0].y;
        var len = Math.sqrt(vx * vx + vy * vy);
        if (len < 1) return out;
        var pux = -vy / len, puy = vx / len;
        // Project p3 onto baseline to get perpendicular distance
        var t = ((pp[2].x - pp[0].x) * vx + (pp[2].y - pp[0].y) * vy) / (len * len);
        var projX = pp[0].x + t * vx, projY = pp[0].y + t * vy;
        var dist = (pp[2].x - projX) * pux + (pp[2].y - projY) * puy;

        // For each ratio, compute offset line parallel to baseline
        for (var ri = 0; ri < def.ratios.length; ri++) {
            var r = def.ratios[ri];
            if (r.visible === false) continue;
            var offset = dist * r.value;
            var ox = offset * pux, oy = offset * puy;
            // Line through (pp[0].x+ox, pp[0].y+oy) parallel to vx,vy, extended across canvas
            // Parametric: P = (pp0+ox, pp0+oy) + s*(vx, vy)
            // Intersect with left/right edges (x=0 and x=cw)
            var lx0 = 0, ly0 = (pp[0].y + oy) + (0 - pp[0].x - ox) * vy / (vx || 1);
            var lx1 = cw, ly1 = (pp[0].y + oy) + (cw - pp[0].x - ox) * vy / (vx || 1);
            if (Math.abs(vx) < 0.001) { // vertical baseline
                lx0 = pp[0].x + ox; lx1 = pp[0].x + ox;
                ly0 = 0; ly1 = ch;
            }
            var lsColor = style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86';
            out.push({
                type: 'line',
                start: { x: lx0, y: ly0 },
                end: { x: lx1, y: ly1 },
                style: { color: lsColor, width: style.ratioWidths && style.ratioWidths[r.value] || style.width || 1, opacity: style.opacity != null ? style.opacity : 0.8, lineDash: style.ratioLineDash && style.ratioLineDash[r.value] || style.lineDash || [] }
            });
            if (style.showLabel !== false) {
                var prec = style.precision != null ? style.precision : 2;
                out.push({ type: 'label', pos: { x: lx1 + 4, y: ly1 }, text: r.label, style: { color: lsColor, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: style.fontSize || 10, align: 'left' } });
            }
        }
        return out;
    };

    // ---- Fan (Fib Fan / Speed Resistance) ----
    GEOMETRY_STRATEGIES.fan = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 2) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]) ];
        if (!pp[0] || !pp[1]) return out;
        var dx = pp[1].x - pp[0].x, dy = pp[1].y - pp[0].y;
        var baseAngle = Math.atan2(dy, dx);

        for (var ri = 0; ri < def.ratios.length; ri++) {
            var r = def.ratios[ri];
            if (r.visible === false) continue;
            
            // Fib Fan divides the vertical distance by the ratio
            var targetX = pp[1].x;
            var targetY = pp[0].y + dy * r.value;
            var tx = targetX - pp[0].x;
            var ty = targetY - pp[0].y;
            var a = Math.atan2(ty, tx);

            // Extend line from pp[0] to canvas edge
            var ex = pp[0].x + Math.cos(a) * (cw + ch);
            var ey = pp[0].y + Math.sin(a) * (cw + ch);
            // Clip to canvas
            var clip = _clipLineToRect(pp[0].x, pp[0].y, ex, ey, 0, 0, cw, ch);
            if (!clip) continue;
            var lsColor = style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86';
            out.push({
                type: 'line',
                start: { x: clip[0], y: clip[1] },
                end: { x: clip[2], y: clip[3] },
                style: { color: lsColor, width: style.ratioWidths && style.ratioWidths[r.value] || style.width || 1, opacity: style.opacity != null ? style.opacity : 0.8, lineDash: style.ratioLineDash && style.ratioLineDash[r.value] || style.lineDash || [] }
            });
            if (style.showLabel !== false) {
                var mid = 0.5;
                var mx = pp[0].x + Math.cos(a) * (cw * mid);
                var my_ = pp[0].y + Math.sin(a) * (cw * mid);
                if (mx > pp[0].x) {
                    out.push({ type: 'label', pos: { x: clip[2] + 4, y: clip[3] }, text: r.label, style: { color: lsColor, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: style.fontSize || 10, align: 'left' } });
                }
            }
        }
        return out;
    };

    // ---- Circles (Fib Circles) ----
    GEOMETRY_STRATEGIES.circles = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 2) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]) ];
        if (!pp[0] || !pp[1]) return out;
        var baseR = GeometryUtils.distance(pp[0], pp[1]);

        for (var ri = 0; ri < def.ratios.length; ri++) {
            var r = def.ratios[ri];
            if (r.visible === false) continue;
            var radius = baseR * r.value;
            var lsColor = style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86';
            out.push({
                type: 'circle',
                center: { x: pp[0].x, y: pp[0].y },
                radius: radius,
                style: { color: lsColor, width: style.ratioWidths && style.ratioWidths[r.value] || style.width || 1, opacity: style.opacity != null ? style.opacity : 0.8, lineDash: style.ratioLineDash && style.ratioLineDash[r.value] || style.lineDash || [] }
            });
            if (style.showLabel !== false) {
                out.push({ type: 'label', pos: { x: pp[0].x + radius + 4, y: pp[0].y }, text: r.label, style: { color: lsColor, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: style.fontSize || 10, align: 'left' } });
            }
        }
        return out;
    };

    // ---- Arcs (Fib Arcs) ----
    GEOMETRY_STRATEGIES.arcs = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 2) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]) ];
        if (!pp[0] || !pp[1]) return out;
        var baseR = GeometryUtils.distance(pp[0], pp[1]);
        var angle = Math.atan2(pp[1].y - pp[0].y, pp[1].x - pp[0].x);

        for (var ri = 0; ri < def.ratios.length; ri++) {
            var r = def.ratios[ri];
            if (r.visible === false) continue;
            var radius = baseR * r.value;
            var lsColor = style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86';
            out.push({
                type: 'arc',
                center: { x: pp[0].x, y: pp[0].y },
                radius: radius,
                startAngle: 0,
                endAngle: Math.PI,
                style: { color: lsColor, width: style.ratioWidths && style.ratioWidths[r.value] || style.width || 1, opacity: style.opacity != null ? style.opacity : 0.8, lineDash: style.ratioLineDash && style.ratioLineDash[r.value] || style.lineDash || [], fillColor: style.fillColor || null, fillOpacity: style.fillOpacity || 0 }
            });
            if (style.showLabel !== false) {
                out.push({ type: 'label', pos: { x: pp[0].x + radius + 4, y: pp[0].y + 4 }, text: r.label, style: { color: lsColor, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: style.fontSize || 10, align: 'left' } });
            }
        }
        return out;
    };

    // ---- Time Levels (Fib Time Zone) ----
    GEOMETRY_STRATEGIES.time_levels = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 2) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]) ];
        if (!pp[0] || !pp[1]) return out;
        var baseDist = pp[1].x - pp[0].x;
        if (def.intervalType === 'price') baseDist = pp[1].y - pp[0].y;

        for (var ri = 0; ri < def.ratios.length; ri++) {
            var r = def.ratios[ri];
            if (r.visible === false) continue;
            var offset = baseDist * r.value;
            var x = def.intervalType === 'price' ? pp[0].x : pp[0].x + offset;
            var y = def.intervalType === 'price' ? pp[0].y + offset : pp[0].y;
            if (x < 0 || x > cw) continue;
            var lsColor = style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86';
            out.push({
                type: 'line',
                start: { x: x, y: 0 },
                end: { x: x, y: ch },
                style: { color: lsColor, width: style.ratioWidths && style.ratioWidths[r.value] || style.width || 1, opacity: style.opacity != null ? style.opacity : 0.5, lineDash: style.ratioLineDash && style.ratioLineDash[r.value] || [2, 4] }
            });
            if (style.showLabel !== false) {
                out.push({ type: 'label', pos: { x: x + 4, y: 12 }, text: r.label, style: { color: lsColor, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: style.fontSize || 9, align: 'left' } });
            }
        }
        return out;
    };

    // ---- Spiral (Fib Spiral) ----
    GEOMETRY_STRATEGIES.spiral = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 2) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]) ];
        if (!pp[0] || !pp[1]) return out;
        // Golden spiral approximation via quarter-circle arcs
        var a = Math.abs(pp[1].x - pp[0].x);
        var b = Math.abs(pp[1].y - pp[0].y);
        var size = Math.max(a, b);
        var cx = pp[0].x, cy = pp[0].y;
        var phi = (1 + Math.sqrt(5)) / 2;
        var fibs = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34];

        for (var ri = 0; ri < def.ratios.length && ri < fibs.length; ri++) {
            var r = def.ratios[ri];
            if (r.visible === false) continue;
            var radius = size * fibs[ri] / (fibs[fibs.length-1] || 1);
            if (radius < 2) continue;
            var lsColor = style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86';
            // Quarter arcs in alternating quadrants
            var q = ri % 4;
            var sa = q * Math.PI / 2;
            var ea = (q + 1) * Math.PI / 2;
            // Approximate center offset for spiral
            var ocx = cx + (q === 1 ? radius : q === 2 ? -radius : 0);
            var ocy = cy + (q === 2 ? radius : q === 3 ? -radius : 0);
            out.push({
                type: 'arc',
                center: { x: cx, y: cy },
                radius: radius,
                startAngle: sa,
                endAngle: ea,
                style: { color: lsColor, width: style.ratioWidths && style.ratioWidths[r.value] || style.width || 1, opacity: style.opacity != null ? style.opacity : 0.6, lineDash: style.ratioLineDash && style.ratioLineDash[r.value] || [] }
            });
            // Update center for next arc
            if (q === 0) { cy -= radius; }
            else if (q === 1) { cx += radius; }
            else if (q === 2) { cy += radius; }
            else { cx -= radius; }
        }
        return out;
    };

    // ---- Wedge (Fib Wedge) ----
    GEOMETRY_STRATEGIES.wedge = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 3) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]), _px(chartState, anchors[2]) ];
        if (!pp[0] || !pp[1] || !pp[2]) return out;
        // Upper line through pp[0]-pp[1], lower line through pp[0]-pp[2] converging
        var clip1 = _clipLineToRect(pp[0].x, pp[0].y, pp[1].x, pp[1].y, 0, 0, cw, ch);
        var clip2 = _clipLineToRect(pp[0].x, pp[0].y, pp[2].x, pp[2].y, 0, 0, cw, ch);
        if (clip1 && clip2) {
            out.push({ type: 'line', start: {x:clip1[0],y:clip1[1]}, end: {x:clip1[2],y:clip1[3]}, style: { color: style.color || '#787b86', width: style.width || 1, opacity: style.opacity != null ? style.opacity : 0.8, lineDash: style.lineDash || [] } });
            out.push({ type: 'line', start: {x:clip2[0],y:clip2[1]}, end: {x:clip2[2],y:clip2[3]}, style: { color: style.color || '#787b86', width: style.width || 1, opacity: style.opacity != null ? style.opacity : 0.8, lineDash: style.lineDash || [] } });
        }
        // Fill between converging lines
        var topP = clip1, botP = clip2;
        if (topP && botP) {
            // Determine which is upper/lower by y
            if (topP[1] < botP[1]) { var tmp = topP; topP = botP; botP = tmp; }
            out.push({ type: 'fill', vertices: [{x:topP[0],y:topP[1]},{x:topP[2],y:topP[3]},{x:botP[2],y:botP[3]},{x:botP[0],y:botP[1]}], style: { color: style.fillColor || '#787b86', opacity: style.fillOpacity != null ? style.fillOpacity : 0.05 } });
        }
        return out;
    };

    // ---- Pitchfan (3-anchor pitchfork with fib inner lines) ----
    GEOMETRY_STRATEGIES.pitchfan = function(anchors, def, chartState, cw, ch, style) {
        var out = [];
        if (!anchors || anchors.length < 3) return out;
        var pp = [ _px(chartState, anchors[0]), _px(chartState, anchors[1]), _px(chartState, anchors[2]) ];
        if (!pp[0] || !pp[1] || !pp[2]) return out;
        // Midpoint of p2-p3
        var midX = (pp[1].x + pp[2].x) / 2, midY = (pp[1].y + pp[2].y) / 2;
        // Median line from p1 through midpoint
        var clip = _clipLineToRect(pp[0].x, pp[0].y, midX, midY, 0, 0, cw, ch);
        if (clip) {
            out.push({ type: 'line', start: {x:clip[0],y:clip[1]}, end: {x:clip[2],y:clip[3]}, style: { color: style.color || '#2962ff', width: style.width || 1.5, opacity: style.opacity != null ? style.opacity : 0.9, lineDash: [] } });
        }
        // Inner lines at ratio offsets from median
        var vx = midX - pp[0].x, vy = midY - pp[0].y;
        var len = Math.sqrt(vx*vx + vy*vy);
        if (len > 1) {
            var pux = -vy / len, puy = vx / len;
            var p2off = (pp[1].x - pp[0].x) * pux + (pp[1].y - pp[0].y) * puy; // half-width
            for (var ri = 0; ri < def.ratios.length; ri++) {
                var r = def.ratios[ri];
                if (r.visible === false) continue;
                var offset = (r.value - 0.5) * 2 * p2off; // map 0/1 to -p2off/+p2off
                var ox = offset * pux, oy = offset * puy;
                var cx = pp[0].x + vx/3 + ox, cy = pp[0].y + vy/3 + oy; // 1/3 along the median
                var cl = _clipLineToRect(cx, cy, cx + vx, cy + vy, 0, 0, cw, ch);
                if (cl) {
                    var lsColor = style.ratioColors && style.ratioColors[r.value] || style.color || '#787b86';
                    out.push({ type: 'line', start: {x:cl[0],y:cl[1]}, end: {x:cl[2],y:cl[3]}, style: { color: lsColor, width: style.width || 1, opacity: 0.6, lineDash: [2, 2] } });
                }
            }
        }
        return out;
    };

    // ---- Clip line segment to rectangle (Cohen-Sutherland style) ----
    function _clipLineToRect(x1, y1, x2, y2, rx, ry, rw, rh) {
        var INSIDE = 0, LEFT = 1, RIGHT = 2, BOTTOM = 4, TOP = 8;
        function code(x, y) {
            var c = INSIDE;
            if (x < rx) c |= LEFT; else if (x > rx + rw) c |= RIGHT;
            if (y < ry) c |= TOP; else if (y > ry + rh) c |= BOTTOM;
            return c;
        }
        var c1 = code(x1, y1), c2 = code(x2, y2);
        // P0 FIX (found by real-browser QA -- gann_box froze the tab for >30s):
        // the RIGHT boundary is at rx + rw, NOT rw. The old code interpolated
        // against `rw` and then assigned `x = rw`, i.e. it used a WIDTH as an
        // X COORDINATE. Whenever rx !== 0 the clipped point therefore still
        // scored as RIGHT, code() never cleared the bit, and `while (true)`
        // spun forever. _box() calls this a second time with rx = x1 (the box's
        // left edge, non-zero), so every Gann tool hung the main thread. The
        // first call passes rx = 0, where rw == rx + rw by coincidence, which
        // is why the bug stayed hidden.
        var xMax = rx + rw, yMax = ry + rh;
        var guard = 0;
        while (true) {
            // Defensive cap: Cohen-Sutherland converges in <= 4 clips (one per
            // edge). Anything beyond that means degenerate input (NaN/Infinity
            // from an extreme projection), so bail out instead of freezing.
            if (++guard > 8) { return null; }
            if (!(c1 | c2)) { return [x1, y1, x2, y2]; }
            if (c1 & c2) { return null; }
            var c = c1 || c2, x, y;
            var dx = (x2 - x1), dy = (y2 - y1);
            if (c & TOP) { x = x1 + dx * (ry - y1) / dy; y = ry; }
            else if (c & BOTTOM) { x = x1 + dx * (yMax - y1) / dy; y = yMax; }
            else if (c & RIGHT) { y = y1 + dy * (xMax - x1) / dx; x = xMax; }
            else if (c & LEFT) { y = y1 + dy * (rx - x1) / dx; x = rx; }
            if (!isFinite(x) || !isFinite(y)) { return null; }
            if (c === c1) { x1 = x; y1 = y; c1 = code(x1, y1); }
            else { x2 = x; y2 = y; c2 = code(x2, y2); }
        }
    }

    // =============================================================================
    // GeometryRenderer — renders GeometryPrimitive[] to canvas
    // =============================================================================
    // PRIMITIVE_RENDERERS — dispatcher registry for GeometryPrimitive types
    var PRIMITIVE_RENDERERS = {
        coordCallout: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var p1 = cs.coordToPixel(p.p1);
            var p2 = cs.coordToPixel(p.p2);
            if (!p1 || !p2) return;

            var text = p.text || '';
            var fs = p.style.fontSize || 13;
            var ff = p.style.fontFamily || '-apple-system, Roboto, sans-serif';
            var color = p.style.color || '#d1d4dc';
            var bg = p.style.background || p.style.fillColor || '#2a2e39';
            var borderColor = p.style.borderColor || p.style.strokeColor || '#5d606b';
            var r = p.style.cornerRadius || 6;

            ctx.save();
            ctx.font = (p.style.bold ? 'bold ' : '') + (p.style.italic ? 'italic ' : '') + fs + 'px ' + ff;

            var lines = text.split('\n');
            var maxW = 0;
            for (var i = 0; i < lines.length; i++) {
                var w = ctx.measureText(lines[i]).width;
                if (w > maxW) maxW = w;
            }
            var lineH = fs * 1.3;
            var pad = 8;
            var bw = maxW + pad * 2;
            var bh = lines.length * lineH + pad * 2;

            var bx = p2.x - bw / 2;
            var by = p2.y - bh / 2;

            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            
            var ix = p2.x;
            var iy = p2.y;
            if (p1.x < bx) {
                ix = bx;
            } else if (p1.x > bx + bw) {
                ix = bx + bw;
            }
            if (p1.y < by) {
                iy = by;
            } else if (p1.y > by + bh) {
                iy = by + bh;
            }
            ctx.lineTo(ix, iy);

            ctx.strokeStyle = borderColor;
            ctx.lineWidth = p.style.width || 1;
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 0.8;
            ctx.stroke();

            ctx.fillStyle = bg;
            ctx.globalAlpha = p.style.fillOpacity != null ? p.style.fillOpacity : 0.9;
            ctx.beginPath();
            ctx.roundRect(bx, by, bw, bh, r);
            ctx.fill();

            ctx.strokeStyle = borderColor;
            ctx.lineWidth = p.style.width || 1;
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 1;
            ctx.beginPath();
            ctx.roundRect(bx, by, bw, bh, r);
            ctx.stroke();

            ctx.fillStyle = color;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            for (var i = 0; i < lines.length; i++) {
                ctx.fillText(lines[i], p2.x, by + pad + i * lineH);
            }

            ctx.restore();
        },
        line: function(ctx, p, cs) {
            ctx.strokeStyle = p.style.color || '#787b86';
            ctx.lineWidth = p.style.width || 1;
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 0.8;
            ctx.setLineDash(p.style.lineDash || []);
            ctx.beginPath(); ctx.moveTo(p.start.x, p.start.y); ctx.lineTo(p.end.x, p.end.y); ctx.stroke();
        },
        circle: function(ctx, p, cs) {
            ctx.strokeStyle = p.style.color || '#787b86';
            ctx.lineWidth = p.style.width || 1;
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 0.8;
            ctx.setLineDash(p.style.lineDash || []);
            ctx.beginPath(); ctx.arc(p.center.x, p.center.y, Math.max(0, p.radius), 0, Math.PI * 2); ctx.stroke();
        },
        arc: function(ctx, p, cs) {
            ctx.strokeStyle = p.style.color || '#787b86';
            ctx.lineWidth = p.style.width || 1;
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 0.8;
            ctx.setLineDash(p.style.lineDash || []);
            ctx.beginPath(); ctx.arc(p.center.x, p.center.y, Math.max(0, p.radius), p.startAngle || 0, p.endAngle || Math.PI); ctx.stroke();
            if (p.style.fillColor) {
                ctx.fillStyle = p.style.fillColor;
                ctx.globalAlpha = p.style.fillOpacity != null ? p.style.fillOpacity : 0.08;
                ctx.fill();
            }
        },
        fill: function(ctx, p, cs) {
            ctx.fillStyle = p.style.color || '#787b86';
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 0.08;
            ctx.beginPath(); ctx.moveTo(p.vertices[0].x, p.vertices[0].y);
            for (var vi = 1; vi < p.vertices.length; vi++) ctx.lineTo(p.vertices[vi].x, p.vertices[vi].y);
            ctx.closePath(); ctx.fill();
        },
        label: function(ctx, p, cs) {
            ctx.fillStyle = p.style.color || '#787b86';
            ctx.font = (p.style.fontSize || 10) + 'px ' + (p.style.fontFamily || '-apple-system, Roboto, sans-serif');
            ctx.textAlign = p.style.align || 'left';
            ctx.textBaseline = 'middle';
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 0.9;
            ctx.fillText(p.text || '', p.pos.x, p.pos.y);
        },
        // Coordinate-space primitives — converted via chartState
        coordLine: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var start = cs.coordToPixel(p.start), end = cs.coordToPixel(p.end);
            if (!start || !end) return;
            PRIMITIVE_RENDERERS.line(ctx, { type:'line', start:start, end:end, style:p.style });
        },
        coordLabel: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var pos = cs.coordToPixel(p.coord);
            if (!pos) return;
            PRIMITIVE_RENDERERS.label(ctx, { type:'label', pos:{x:pos.x+(p.offsetX||8),y:pos.y-(p.offsetY||8)}, text:p.text, style:p.style });
        },
        coordPolygon: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var verts = [];
            for (var vi = 0; vi < p.vertices.length; vi++) {
                var v = cs.coordToPixel(p.vertices[vi]);
                if (v) verts.push(v);
            }
            if (verts.length < 3) return;
            ctx.beginPath(); ctx.moveTo(verts[0].x, verts[0].y);
            for (var vi = 1; vi < verts.length; vi++) ctx.lineTo(verts[vi].x, verts[vi].y);
            ctx.closePath();
            if (p.style.fillColor) {
                ctx.fillStyle = p.style.fillColor;
                ctx.globalAlpha = p.style.fillOpacity != null ? p.style.fillOpacity : 0.08;
                ctx.fill(p.style.fillRule || 'nonzero');
            }
            if (p.style.strokeColor || (p.style.color && p.style.lineWidth)) {
                ctx.strokeStyle = p.style.strokeColor || p.style.color || '#787b86';
                ctx.lineWidth = p.style.lineWidth || 1;
                ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 0.8;
                ctx.setLineDash(p.style.lineDash || []);
                ctx.stroke();
            }
        },
        // Phase 3.9: Rich Object primitives
        coordBounds: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var tl = cs.coordToPixel(p.topLeft);
            var br = cs.coordToPixel(p.bottomRight);
            if (!tl || !br) return;
            var x = Math.min(tl.x, br.x), y = Math.min(tl.y, br.y);
            var w = Math.abs(br.x - tl.x), h = Math.abs(br.y - tl.y);
            if (w < 1 || h < 1) return;
            var rotation = p.rotation || 0;
            ctx.save();
            if (rotation) { var cx = x + w/2, cy = y + h/2; ctx.translate(cx, cy); ctx.rotate(rotation * Math.PI / 180); ctx.translate(-cx, -cy); }
            var r = p.style.cornerRadius || 0;
            if (r > 0 && p.style.fillColor) {
                ctx.beginPath(); ctx.moveTo(x+r, y); ctx.lineTo(x+w-r, y); ctx.quadraticCurveTo(x+w, y, x+w, y+r);
                ctx.lineTo(x+w, y+h-r); ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
                ctx.lineTo(x+r, y+h); ctx.quadraticCurveTo(x, y+h, x, y+h-r);
                ctx.lineTo(x, y+r); ctx.quadraticCurveTo(x, y, x+r, y); ctx.closePath();
                ctx.fillStyle = p.style.fillColor;
                ctx.globalAlpha = p.style.fillOpacity != null ? p.style.fillOpacity : 1;
                ctx.fill();
            } else if (p.style.fillColor) {
                ctx.fillStyle = p.style.fillColor;
                ctx.globalAlpha = p.style.fillOpacity != null ? p.style.fillOpacity : 1;
                ctx.fillRect(x, y, w, h);
            }
            if (p.style.strokeColor || p.style.color) {
                ctx.strokeStyle = p.style.strokeColor || p.style.color || '#787b86';
                ctx.lineWidth = p.style.lineWidth || 1;
                ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 1;
                ctx.setLineDash(p.style.lineDash || []);
                if (r > 0) {
                    ctx.beginPath(); ctx.moveTo(x+r, y); ctx.lineTo(x+w-r, y); ctx.quadraticCurveTo(x+w, y, x+w, y+r);
                    ctx.lineTo(x+w, y+h-r); ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
                    ctx.lineTo(x+r, y+h); ctx.quadraticCurveTo(x, y+h, x, y+h-r);
                    ctx.lineTo(x, y+r); ctx.quadraticCurveTo(x, y, x+r, y); ctx.closePath(); ctx.stroke();
                } else { ctx.strokeRect(x, y, w, h); }
            }
            ctx.restore();
        },
        coordText: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var pos = cs.coordToPixel(p.coord);
            if (!pos) return;
            var text = p.content && p.content.plain;
            if (!text) return;
            var fs = p.style.fontSize || 13;
            var ff = p.style.fontFamily || '-apple-system, Roboto, sans-serif';
            var color = p.style.color || '#d1d4dc';
            var align = p.style.align || 'left';
            var maxW = p.style.maxWidth || 400;
            ctx.save();
            ctx.font = (p.style.bold ? 'bold ' : '') + (p.style.italic ? 'italic ' : '') + fs + 'px ' + ff;
            ctx.fillStyle = color;
            ctx.textAlign = align;
            ctx.textBaseline = 'top';
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 1;
            var x = pos.x + (p.offsetX || 8);
            var y = pos.y + (p.offsetY || -fs - 4);
            // Word-wrap
            var words = text.split(' ');
            var line = '', lines = [], lineH = fs * 1.3;
            for (var wi = 0; wi < words.length; wi++) {
                var test = line ? line + ' ' + words[wi] : words[wi];
                var m = ctx.measureText(test);
                if (m.width > maxW && line) { lines.push(line); line = words[wi]; }
                else { line = test; }
            }
            if (line) lines.push(line);
            // Draw background if specified
            if (p.style.background) {
                var bw = 0;
                for (var li = 0; li < lines.length; li++) {
                    var m2 = ctx.measureText(lines[li]);
                    if (m2.width > bw) bw = m2.width;
                }
                var bx = align === 'center' ? x - bw/2 - 6 : align === 'right' ? x - bw - 12 : x - 4;
                ctx.fillStyle = p.style.background; ctx.globalAlpha = 0.9;
                ctx.fillRect(bx, y - 2, bw + 10, lines.length * lineH + 4);
                ctx.globalAlpha = 1;
            }
            for (var li = 0; li < lines.length; li++) {
                ctx.fillText(lines[li], x, y + li * lineH);
            }
            ctx.restore();
        },
        coordImage: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var tl = cs.coordToPixel(p.topLeft);
            var br = cs.coordToPixel(p.bottomRight);
            if (!tl || !br) return;
            var x = Math.min(tl.x, br.x), y = Math.min(tl.y, br.y);
            var w = Math.abs(br.x - tl.x), h = Math.abs(br.y - tl.y);
            if (w < 1 || h < 1) return;
            var img = p.image || null;
            if (!img) return;
            var rotation = p.rotation || 0;
            ctx.save();
            if (rotation) { var cx = x + w/2, cy = y + h/2; ctx.translate(cx, cy); ctx.rotate(rotation * Math.PI / 180); ctx.translate(-cx, -cy); }
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 1;
            var r = p.style.cornerRadius || 0;
            if (r > 0) {
                ctx.beginPath(); ctx.moveTo(x+r, y); ctx.lineTo(x+w-r, y); ctx.quadraticCurveTo(x+w, y, x+w, y+r);
                ctx.lineTo(x+w, y+h-r); ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
                ctx.lineTo(x+r, y+h); ctx.quadraticCurveTo(x, y+h, x, y+h-r);
                ctx.lineTo(x, y+r); ctx.quadraticCurveTo(x, y, x+r, y); ctx.closePath(); ctx.clip();
            }
            ctx.drawImage(img, x, y, w, h);
            ctx.restore();
        },
        coordEmoji: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var pos = cs.coordToPixel(p.coord);
            if (!pos) return;
            var emoji = p.emoji || '📌';
            var size = p.size || 24;
            ctx.save();
            ctx.font = size + 'px "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 1;
            ctx.fillText(emoji, pos.x, pos.y);
            ctx.restore();
        },
        coordIcon: function(ctx, p, cs) {
            if (!cs || !cs.coordToPixel) return;
            var pos = cs.coordToPixel(p.coord);
            if (!pos) return;
            var pathData = p.path;
            if (!pathData) return;
            var size = p.size || 24;
            var color = p.style.color || '#787b86';
            var viewBox = p.viewBox || '0 0 24 24';
            ctx.save();
            ctx.translate(pos.x - size/2, pos.y - size/2);
            ctx.globalAlpha = p.style.opacity != null ? p.style.opacity : 1;
            // Parse viewBox for scale
            var vb = viewBox.split(' ').map(Number);
            var scaleX = vb[2] > 0 ? size / vb[2] : 1;
            var scaleY = vb[3] > 0 ? size / vb[3] : 1;
            ctx.scale(scaleX, scaleY);
            ctx.translate(-vb[0], -vb[1]);
            ctx.fillStyle = color;
            // Simple SVG path renderer via Path2D
            try {
                var path = new Path2D(pathData);
                ctx.fill(path);
            } catch(e) {
                // Fallback for browsers without Path2D support
                console.warn('coordIcon: Path2D not supported for', p.icon);
            }
            ctx.restore();
        }
    };

    // PatternGeometry — constants to avoid string typos (P0-3)
    var PatternGeometry = {
        HARMONIC: 'harmonic',
        POLYLINE: 'polyline',
        POLYGON: 'polygon',
        WEDGE: 'wedge',
        CHANNEL: 'channel'
    };

    var GeometryRenderer = {
        render: function(ctx, primitives, chartState) {
            if (!primitives || !primitives.length) return;
            ctx.save();
            for (var i = 0; i < primitives.length; i++) {
                var p = primitives[i];
                var renderer = PRIMITIVE_RENDERERS[p.type];
                if (renderer) renderer(ctx, p, chartState || null);
            }
            ctx.restore();
        }
    };

    // =============================================================================
    // RatioDefinitions — per-tool configuration (config only, no rendering props)
    // =============================================================================
    var RATIO_DEFS = {
        retracement: {
            anchors: 2, geometry: 'horizontal_levels', direction: 'right',
            ratios: [
                { value: 0,     label: '0.0%' },
                { value: 0.236, label: '23.6%' },
                { value: 0.382, label: '38.2%' },
                { value: 0.5,   label: '50.0%' },
                { value: 0.618, label: '61.8%' },
                { value: 0.786, label: '78.6%' },
                { value: 1,     label: '100.0%' },
            ],
            fills: [
                { from: 0, to: 0.382, color: '#26a69a', opacity: 0.08 },
                { from: 0.618, to: 1, color: '#ef5350', opacity: 0.08 },
            ]
        },
        trend_based_extension: {
            anchors: 3, geometry: 'horizontal_levels', direction: 'right',
            ratios: [
                { value: 0.382, label: '38.2%' },
                { value: 0.5,   label: '50.0%' },
                { value: 0.618, label: '61.8%' },
                { value: 0.786, label: '78.6%' },
                { value: 1,     label: '100.0%' },
                { value: 1.272, label: '127.2%' },
                { value: 1.618, label: '161.8%' },
                { value: 2,     label: '200.0%' },
                { value: 2.618, label: '261.8%' },
            ]
        },
        fib_channel: {
            anchors: 3, geometry: 'parallel_lines',
            ratios: [
                { value: 0,     label: '0.0%' },
                { value: 0.236, label: '23.6%' },
                { value: 0.382, label: '38.2%' },
                { value: 0.5,   label: '50.0%' },
                { value: 0.618, label: '61.8%' },
                { value: 0.786, label: '78.6%' },
                { value: 1,     label: '100.0%' },
                { value: 1.272, label: '127.2%' },
                { value: 1.618, label: '161.8%' },
            ]
        },
        fib_fan: {
            anchors: 2, geometry: 'fan', fanDirection: 'up',
            ratios: [
                { value: 0,     label: '0/8' },
                { value: 1,     label: '1/8' },
                { value: 2,     label: '2/8' },
                { value: 3,     label: '3/8' },
                { value: 4,     label: '4/8' },
                { value: 5,     label: '5/8' },
                { value: 6,     label: '6/8' },
                { value: 7,     label: '7/8' },
                { value: 8,     label: '8/8' },
            ]
        },
        fib_time_zone: {
            anchors: 2, geometry: 'time_levels',
            ratios: [
                { value: 1,     label: '1' },
                { value: 2,     label: '2' },
                { value: 3,     label: '3' },
                { value: 5,     label: '5' },
                { value: 8,     label: '8' },
                { value: 13,    label: '13' },
                { value: 21,    label: '21' },
                { value: 34,    label: '34' },
                { value: 55,    label: '55' },
                { value: 89,    label: '89' },
            ]
        },
        fib_circles: {
            anchors: 2, geometry: 'circles',
            ratios: [
                { value: 0.236, label: '23.6%' },
                { value: 0.382, label: '38.2%' },
                { value: 0.5,   label: '50.0%' },
                { value: 0.618, label: '61.8%' },
                { value: 0.786, label: '78.6%' },
                { value: 1,     label: '100.0%' },
                { value: 1.272, label: '127.2%' },
                { value: 1.618, label: '161.8%' },
            ]
        },
        fib_arcs: {
            anchors: 2, geometry: 'arcs',
            ratios: [
                { value: 0.236, label: '23.6%' },
                { value: 0.382, label: '38.2%' },
                { value: 0.5,   label: '50.0%' },
                { value: 0.618, label: '61.8%' },
                { value: 0.786, label: '78.6%' },
                { value: 1,     label: '100.0%' },
            ]
        },
        speed_resistance: {
            anchors: 2, geometry: 'fan', fanDirection: 'down',
            ratios: [
                { value: 1,     label: '1/1' },
                { value: 2,     label: '2/1' },
                { value: 3,     label: '3/1' },
            ]
        },
        fib_spiral: {
            anchors: 2, geometry: 'spiral',
            ratios: [
                { value: 1,  label: '' },
                { value: 2,  label: '' },
                { value: 3,  label: '' },
                { value: 5,  label: '' },
                { value: 8,  label: '' },
                { value: 13, label: '' },
                { value: 21, label: '' },
            ]
        },
        fib_wedge: {
            anchors: 3, geometry: 'wedge',
            ratios: [] // Boundaries drawn; no individual ratio lines in wedge
        },
        pitchfan: {
            anchors: 3, geometry: 'pitchfan',
            ratios: [
                { value: 0.382, label: '38.2%' },
                { value: 0.5,   label: '50.0%' },
                { value: 0.618, label: '61.8%' },
            ]
        }
    };

    // =============================================================================
    // BaseRatioDrawing — base class for all Fibonacci/ratio tools
    // =============================================================================
    class BaseRatioDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this._cachedPrimitives = null;
            this._viewportVersion = 0;
        }

        getRatioDef() { return null; }

        _invalidateCache() {
            this._cachedPrimitives = null;
        }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            this._invalidateCache();
            return this.coords.length >= (this.getRatioDef() ? this.getRatioDef().anchors : 2);
        }

        // Invalidate on viewport changes
        onViewportChange() {
            this._invalidateCache();
        }

        _generatePrimitives(chartState) {
            var def = this.getRatioDef();
            if (!def) return null;
            if (!chartState) return null;
            if (!chartState.coordToPixel) return null;
            // Use DPR from chartState if available, else window
            var dpr = chartState.dpr || window.devicePixelRatio || 1;
            var cw = (chartState.canvasWidth || 800) / dpr;
            var ch = (chartState.canvasHeight || 600) / dpr;

            var strategy = GEOMETRY_STRATEGIES[def.geometry];
            if (!strategy) return null;
            return strategy(this.coords, def, chartState, cw, ch, this.style);
        }

        _getPrimitives(chartState) {
            if (!this._cachedPrimitives) {
                this._cachedPrimitives = this._generatePrimitives(chartState);
            }
            return this._cachedPrimitives;
        }

        draw(ctx, chartState, isSelected) {
            if (!chartState || !chartState.coordToPixel) return;
            var primitives = this._getPrimitives(chartState);
            if (!primitives || !primitives.length) {
                // P1-5: Preview for incomplete drawings
                if (this.currentPos && this.coords.length > 0 && this.coords.length < (this.getRatioDef() ? this.getRatioDef().anchors : 2)) {
                    this._drawPreview(ctx, chartState);
                }
                return;
            }
            GeometryRenderer.render(ctx, primitives);
        }

        // P1-5: Preview rendering for incomplete drawings
        _drawPreview(ctx, chartState) {
            var pixels = this.getPixels(chartState);
            if (!pixels || pixels.length === 0) return;
            var lastPx = pixels[pixels.length - 1];
            if (!lastPx) return;
            var curPx = this.currentPos;
            if (!curPx) return;
            ctx.save();
            ctx.strokeStyle = this.style.color || '#787b86';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.globalAlpha = 0.5;
            ctx.beginPath();
            ctx.moveTo(lastPx.x, lastPx.y);
            ctx.lineTo(curPx.x, curPx.y);
            ctx.stroke();
            ctx.restore();
        }

        getAnchorPoints(pixels) {
            if (!pixels) return [];
            return pixels.filter(Boolean);
        }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            return [GeometryUtils.midpoint(pixels[0], pixels[1])];
        }

        getFillShape(pixels) {
            // P0-2: Derive fill polygon from generated primitives
            if (!this._cachedPrimitives || !pixels || pixels.length < 2) return null;
            // Find fill primitives and return their vertices
            var fillPrims = this._cachedPrimitives.filter(function(p) { return p.type === 'fill'; });
            if (fillPrims.length > 0) {
                // Return the largest fill (most vertices)
                var largest = fillPrims[0];
                for (var i = 1; i < fillPrims.length; i++) {
                    if (fillPrims[i].vertices.length > largest.vertices.length) largest = fillPrims[i];
                }
                return largest.vertices;
            }
            // Fallback: use bounding box of all line primitives
            var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (var i = 0; i < this._cachedPrimitives.length; i++) {
                var p = this._cachedPrimitives[i];
                if (p.type === 'line') {
                    if (p.start.x < minX) minX = p.start.x;
                    if (p.start.y < minY) minY = p.start.y;
                    if (p.end.x > maxX) maxX = p.end.x;
                    if (p.end.y > maxY) maxY = p.end.y;
                } else if (p.type === 'circle' || p.type === 'arc') {
                    var r = p.radius || 0;
                    if (p.center.x - r < minX) minX = p.center.x - r;
                    if (p.center.y - r < minY) minY = p.center.y - r;
                    if (p.center.x + r > maxX) maxX = p.center.x + r;
                    if (p.center.y + r > maxY) maxY = p.center.y + r;
                }
            }
            if (minX === Infinity) return null;
            return [
                { x: minX, y: minY },
                { x: maxX, y: minY },
                { x: maxX, y: maxY },
                { x: minX, y: maxY }
            ];
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels) return;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
            }
            var mids = this.getMidpoints(pixels);
            for (var mi = 0; mi < mids.length; mi++) {
                if (mids[mi]) this._drawHandle(ctx, mids[mi], isSelected, true);
            }
        }

        _drawHandle(ctx, p, isSelected, isMidpoint) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            if (isMidpoint) {
                var s = 5;
                ctx.beginPath();
                ctx.moveTo(p.x, p.y - s);
                ctx.lineTo(p.x + s, p.y);
                ctx.lineTo(p.x, p.y + s);
                ctx.lineTo(p.x - s, p.y);
                ctx.closePath();
            } else {
                ctx.beginPath();
                ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            }
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
            this._invalidateCache();
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
            this._invalidateCache();
        }
    }

    // =============================================================================
    // Tool Classes — 11 Fibonacci tools as thin configuration subclasses
    // =============================================================================

    // Fibonacci level definitions (TradingView-parity ratios and colors)
    var FIB_LEVELS = [
        { value: 0,     color: '#787b86' },
        { value: 0.236, color: '#f44336' },
        { value: 0.382, color: '#ff9800' },
        { value: 0.5,   color: '#ffc107' },
        { value: 0.618, color: '#4caf50' },
        { value: 0.786, color: '#00bcd4' },
        { value: 1,     color: '#787b86' },
        { value: 1.618, color: '#2196f3' },
        { value: 2.618, color: '#f44336' },
        { value: 3.618, color: '#9c27b0' },
        { value: 4.236, color: '#e91e63' },
    ];

    // Fill bands between every adjacent level pair
    function _buildFibFills() {
        var arr = [];
        for (var _fi = 0; _fi < FIB_LEVELS.length - 1; _fi++) {
            arr.push({ from: FIB_LEVELS[_fi].value, to: FIB_LEVELS[_fi + 1].value,
                       color: FIB_LEVELS[_fi].color, opacity: 0.08 });
        }
        return arr;
    }
    var FIB_FILLS = _buildFibFills();

    class FibRetracement extends BaseDrawing {
        _minAnchors() { return 2; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var cw = this._cw || 10000;
            var ch = this._ch || 10000;
            var style = this.style || {};
            var startX = (style.extendLeft) ? 0 : Math.min(pixels[0].x, pixels[1].x);
            var endX = (style.extendRight) ? cw : Math.max(pixels[0].x, pixels[1].x);
            var p1y = pixels[0].y, p2y = pixels[1].y;
            var dy = p1y - p2y;
            // Extend beyond anchors for 4.236 / 0 levels using linear proportion
            var topY, botY;
            if (dy >= 0) {
                topY = p1y - 3.236 * Math.abs(dy);
                botY = p2y;
            } else {
                topY = p1y;
                botY = p2y + 3.236 * Math.abs(dy);
            }
            if (topY < 0) topY = 0;
            if (botY > ch) botY = ch;
            return [
                {x: startX, y: topY},
                {x: endX, y: topY},
                {x: endX, y: botY},
                {x: startX, y: botY}
            ];
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c;
            if (this.coords.length >= 2) {
                p2c = this.coords[1];
            } else if (this.currentPos && chartState.pixelToCoord) {
                p2c = chartState.pixelToCoord(this.currentPos.x, this.currentPos.y);
            }
            if (!p1c || !p2c || p1c.price == null || p2c.price == null) return;

            var px1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px2;
            if (this.coords.length >= 2) {
                px2 = chartState.coordToPixel ? chartState.coordToPixel(p2c) : null;
            } else {
                px2 = this.currentPos;
            }
            if (!px1 || !px2) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            this._cw = cw;
            this._ch = ch;

            var style = this.style || {};
            var startX = (style.extendLeft) ? 0 : Math.min(px1.x, px2.x);
            var endX = (style.extendRight) ? cw : Math.max(px1.x, px2.x);

            var range = p1c.price - p2c.price;
            // Build level positions
            var levels = [];
            for (var i = 0; i < FIB_LEVELS.length; i++) {
                var lv = FIB_LEVELS[i];
                var price = p2c.price + lv.value * range;
                var y = chartState.priceToY ? chartState.priceToY(price) : null;
                levels.push({ value: lv.value, price: price, y: y, color: lv.color });
            }
            // Draw fill bands between adjacent levels
            for (var fi = 0; fi < FIB_FILLS.length; fi++) {
                var ff = FIB_FILLS[fi];
                var lo = null, hi = null;
                for (var li = 0; li < levels.length - 1; li++) {
                    if (levels[li].value === ff.from && levels[li + 1].value === ff.to) {
                        lo = levels[li]; hi = levels[li + 1]; break;
                    }
                }
                if (lo && hi && lo.y != null && hi.y != null) {
                    var top = Math.min(lo.y, hi.y);
                    var bot = Math.max(lo.y, hi.y);
                    ctx.fillStyle = ff.color;
                    ctx.globalAlpha = ff.opacity;
                    ctx.fillRect(startX, top, endX - startX, bot - top);
                }
            }
            // Draw dashed connector (only if some level lines overlap anchor X region)
            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = '#787b86';
            ctx.lineWidth = 1;
            ctx.globalAlpha = 0.5;
            ctx.moveTo(px1.x, px1.y);
            ctx.lineTo(px2.x, px2.y);
            ctx.stroke();
            ctx.restore();
            // Draw level lines and labels
            var fontSize = style.fontSize || 11;
            ctx.save();
            ctx.font = fontSize + 'px -apple-system, sans-serif';
            ctx.textBaseline = 'middle';
            for (var li = 0; li < levels.length; li++) {
                var l = levels[li];
                if (l.y == null) continue;
                var lColor = l.color || '#787b86';
                ctx.beginPath();
                ctx.strokeStyle = lColor;
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.8;
                ctx.setLineDash([]);
                ctx.moveTo(startX, l.y);
                ctx.lineTo(endX, l.y);
                ctx.stroke();
                if (style.showLabel !== false) {
                    var ratioStr = l.value.toFixed(3);
                    var priceStr = l.price.toFixed(2);
                    var label = ratioStr + ' (' + priceStr + ')';
                    var textWidth = ctx.measureText(label).width;
                    var labelX = startX + 6;
                    var labelY = l.y;
                    var bgPad = 2;
                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(labelX - bgPad, labelY - fontSize/2 - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lColor;
                    ctx.textAlign = 'left';
                    ctx.fillText(label, labelX, labelY);
                }
            }
            ctx.restore();
        }



        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }
    // Trend-Based Extension level definitions (TradingView-parity ratios and colors)
    var EXTENSION_LEVELS = [
        { value: 0,     color: '#787b86' },
        { value: 0.236, color: '#ff5722' },
        { value: 0.382, color: '#ff9800' },
        { value: 0.5,   color: '#ffc107' },
        { value: 0.618, color: '#4caf50' },
        { value: 0.786, color: '#00bcd4' },
        { value: 1,     color: '#787b86' },
        { value: 1.272, color: '#2196f3' },
        { value: 1.414, color: '#2196f3' },
        { value: 1.618, color: '#2196f3' },
        { value: 2,     color: '#f44336' },
        { value: 2.618, color: '#f44336' },
        { value: 3.618, color: '#9c27b0' },
        { value: 4.236, color: '#e91e63' },
    ];

    function _buildExtFills() {
        var arr = [];
        for (var _fi = 0; _fi < EXTENSION_LEVELS.length - 1; _fi++) {
            arr.push({ from: EXTENSION_LEVELS[_fi].value, to: EXTENSION_LEVELS[_fi + 1].value,
                       color: EXTENSION_LEVELS[_fi].color, opacity: 0.12 });
        }
        return arr;
    }
    var EXTENSION_FILLS = _buildExtFills();
    class FibExtension extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
            this.showAxisLabels = true;
        }

        _minAnchors() { return 3; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 3) return null;
            if (!this._levelCache || this._levelCache.length < 2) return null;
            var minY = Infinity, maxY = -Infinity;
            for (var i = 0; i < this._levelCache.length; i++) {
                var l = this._levelCache[i];
                if (l.y != null) {
                    if (l.y < minY) minY = l.y;
                    if (l.y > maxY) maxY = l.y;
                }
            }
            if (minY === Infinity) return null;
            var startX = Math.min(pixels[1].x, pixels[2].x);
            var endX = Math.max(pixels[1].x, pixels[2].x);
            return [
                { x: startX, y: minY },
                { x: endX, y: minY },
                { x: endX, y: maxY },
                { x: startX, y: maxY }
            ];
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 3) return [];
            if (!this._levelCache) return [];
            var segs = [];
            var startX = Math.min(pixels[1].x, pixels[2].x);
            var endX = Math.max(pixels[1].x, pixels[2].x);
            for (var i = 0; i < this._levelCache.length; i++) {
                var l = this._levelCache[i];
                if (l.y != null && l.y >= -10000 && l.y <= 10000) {
                    segs.push({ p1: { x: startX, y: l.y }, p2: { x: endX, y: l.y } });
                }
            }
            if (pixels.length >= 2) {
                segs.push({ p1: { x: pixels[0].x, y: pixels[0].y }, p2: { x: pixels[1].x, y: pixels[1].y } });
            }
            if (pixels.length >= 3 && pixels[1] && pixels[2]) {
                segs.push({ p1: { x: pixels[1].x, y: pixels[1].y }, p2: { x: pixels[2].x, y: pixels[2].y } });
            }
            return segs;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c = this.coords.length >= 2 ? this.coords[1] : null;
            var p3c = this.coords.length >= 3 ? this.coords[2] : null;

            var px1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px2 = p2c ? (chartState.coordToPixel ? chartState.coordToPixel(p2c) : null) : this.currentPos;
            var px3 = p3c ? (chartState.coordToPixel ? chartState.coordToPixel(p3c) : null) : this.currentPos;

            if (!px1 || !px2) return;

            // 1. Draw trend line between point 1 and point 2
            ctx.save();
            ctx.beginPath();
            ctx.strokeStyle = '#787b86';
            ctx.lineWidth = 1.5;
            ctx.moveTo(px1.x, px1.y);
            ctx.lineTo(px2.x, px2.y);
            ctx.stroke();
            ctx.restore();

            // If we don't have point 2 yet, draw dashed guide to cursor and return
            if (this.coords.length < 2) {
                ctx.save();
                ctx.beginPath();
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = '#787b86';
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.5;
                ctx.moveTo(px1.x, px1.y);
                ctx.lineTo(px2.x, px2.y);
                ctx.stroke();
                ctx.restore();
                return;
            }

            // 2. Draw dashed line between point 2 and point 3 (cursor)
            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = '#787b86';
            ctx.lineWidth = 1;
            ctx.globalAlpha = 0.5;
            ctx.moveTo(px2.x, px2.y);
            ctx.lineTo(px3.x, px3.y);
            ctx.stroke();
            ctx.restore();

            // We have at least 2 points. If px3 is not yet placed, we mock it.
            var p3Coord = p3c;
            if (!p3Coord && this.currentPos && chartState.pixelToCoord) {
                p3Coord = chartState.pixelToCoord(this.currentPos.x, this.currentPos.y);
            }
            if (!p3Coord || p3Coord.price == null) return;

            var style = this.style || {};
            var showLabel = style.showLabel !== false;
            var fontSize = style.fontSize || 11;

            var startX = Math.min(px2.x, px3.x);
            var endX = Math.max(px2.x, px3.x);
            if (Math.abs(endX - startX) < 1) {
                endX = startX + 1; // Prevent zero-width
            }

            var wave = p2c.price - p1c.price;
            var p3Price = p3Coord.price;

            // Build level positions and store on instance for hit testing
            var levels = [];
            for (var i = 0; i < EXTENSION_LEVELS.length; i++) {
                var lv = EXTENSION_LEVELS[i];
                var price = p3Price + wave * lv.value;
                var y = chartState.priceToY ? chartState.priceToY(price) : null;
                levels.push({ value: lv.value, price: price, y: y, color: lv.color });
            }
            this._levelCache = levels;

            // Draw filled bands (back to front)
            for (var fi = EXTENSION_FILLS.length - 1; fi >= 0; fi--) {
                var ff = EXTENSION_FILLS[fi];
                var lo = null, hi = null;
                for (var li = 0; li < levels.length - 1; li++) {
                    if (levels[li].value === ff.from && levels[li + 1].value === ff.to) {
                        lo = levels[li]; hi = levels[li + 1]; break;
                    }
                }
                if (lo && hi && lo.y != null && hi.y != null) {
                    var top = Math.min(lo.y, hi.y);
                    var bot = Math.max(lo.y, hi.y);
                    ctx.fillStyle = ff.color;
                    ctx.globalAlpha = ff.opacity;
                    ctx.fillRect(startX, top, endX - startX, bot - top);
                }
            }
            ctx.globalAlpha = 1;

            // Draw level lines and labels
            ctx.save();
            ctx.font = fontSize + 'px -apple-system, sans-serif';
            ctx.textBaseline = 'middle';
            for (var li = 0; li < levels.length; li++) {
                var l = levels[li];
                if (l.y == null) continue;
                var lColor = l.color || '#787b86';

                // Level line
                ctx.beginPath();
                ctx.strokeStyle = lColor;
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.8;
                ctx.setLineDash([]);
                ctx.moveTo(startX, l.y);
                ctx.lineTo(endX, l.y);
                ctx.stroke();

                // Label inside, left-aligned
                if (showLabel) {
                    var ratioStr = l.value.toFixed(3);
                    if (ratioStr.indexOf('.') >= 0) {
                        ratioStr = ratioStr.replace(/0+$/, '');
                        if (ratioStr.endsWith('.')) ratioStr = ratioStr.slice(0, -1);
                    }
                    var label = ratioStr + ' (' + l.price.toFixed(2) + ')';
                    var textWidth = ctx.measureText(label).width;
                    var labelX = startX + 6;
                    var labelY = l.y;
                    var bgPad = 2;
                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(labelX - bgPad, labelY - fontSize/2 - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lColor;
                    ctx.textAlign = 'left';
                    ctx.fillText(label, labelX, labelY);
                }
            }
            ctx.restore();

            // Draw Y-axis and X-axis projections for the 3 points
            if (this.showAxisLabels && isSelected) {
                var validPxs = [px1, px2];
                if (this.coords.length >= 3) {
                    validPxs.push(px3);
                }
                this.drawAxisLabels(ctx, validPxs, chartState);
            }
        }



        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }
    // Fibonacci channel level definitions (TradingView-parity ratios and colors)
    var CHANNEL_LEVELS = [
        { value: 0,     color: '#787b86' },
        { value: 0.236, color: '#f44336' },
        { value: 0.382, color: '#ff9800' },
        { value: 0.5,   color: '#ffc107' },
        { value: 0.618, color: '#4caf50' },
        { value: 0.786, color: '#00bcd4' },
        { value: 1,     color: '#787b86' },
        { value: 1.618, color: '#2196f3' },
        { value: 2.618, color: '#f44336' },
        { value: 3.618, color: '#9c27b0' },
        { value: 4.236, color: '#e91e63' },
    ];

    // Fill band definitions between every adjacent level pair
    function _buildChannelFills() {
        var arr = [];
        for (var _fi = 0; _fi < CHANNEL_LEVELS.length - 1; _fi++) {
            arr.push({ from: CHANNEL_LEVELS[_fi].value, to: CHANNEL_LEVELS[_fi + 1].value,
                       color: CHANNEL_LEVELS[_fi].color, opacity: 0.12 });
        }
        return arr;
    }
    var CHANNEL_FILLS = _buildChannelFills();

    class FibChannel extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
            this.showAxisLabels = true;
        }

        _minAnchors() { return 3; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 3) return null;
            var dpr = this._dpr || window.devicePixelRatio || 1;
            var cw = this._cw || 800;
            var ch = this._ch || 500;
            var clips = this._clipLevelLines(pixels, 0, cw, ch);
            var clipH = this._clipLevelLines(pixels, 4.236, cw, ch);
            if (!clips || clips.length < 2 || !clipH || clipH.length < 2) return null;
            return [
                { x: clips[0].x, y: clips[0].y },
                { x: clips[1].x, y: clips[1].y },
                { x: clipH[1].x, y: clipH[1].y },
                { x: clipH[0].x, y: clipH[0].y }
            ];
        }
        // Helper: clip a level line to viewport and return the two endpoints
        _clipLevelLine(pixels, level, cw, ch) {
            var vx = pixels[1].x - pixels[0].x;
            var vy = pixels[1].y - pixels[0].y;
            var lp1 = {
                x: pixels[0].x + level * (pixels[2].x - pixels[0].x),
                y: pixels[0].y + level * (pixels[2].y - pixels[0].y)
            };
            var lp2 = {
                x: lp1.x + vx,
                y: lp1.y + vy
            };

            var style = this.style || {};
            if (style.extendLeft || style.extendRight) {
                var pts = GeometryUtils.lineCanvasIntersection(lp1, lp2, cw, ch);
                if (!pts || pts.length < 2) return [lp1, lp2];
                if (style.extendLeft && style.extendRight) {
                    return pts;
                } else if (style.extendLeft) {
                    var leftPt = pts[0].x < pts[1].x ? pts[0] : pts[1];
                    return [leftPt, lp2];
                } else if (style.extendRight) {
                    var rightPt = pts[0].x > pts[1].x ? pts[0] : pts[1];
                    return [lp1, rightPt];
                }
            }
            return [lp1, lp2];
        }

        // Helper: compute clipped endpoints for all levels (used for fills + edges)
        _clipLevelLines(pixels, level, cw, ch) {
            return this._clipLevelLine(pixels, level, cw, ch);
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 3) return [];
            var dpr = this._dpr || window.devicePixelRatio || 1;
            var cw = this._cw || 800;
            var ch = this._ch || 500;
            var segs = [];
            for (var i = 0; i < CHANNEL_LEVELS.length; i++) {
                var clip = this._clipLevelLine(pixels, CHANNEL_LEVELS[i].value, cw, ch);
                if (clip && clip.length >= 2) {
                    segs.push({ p1: clip[0], p2: clip[1] });
                }
            }
            return segs;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p0c = this.coords[0];
            var p1c = this.coords.length >= 2 ? this.coords[1] : null;
            var p2c = this.coords.length >= 3 ? this.coords[2] : null;

            var px0 = chartState.coordToPixel ? chartState.coordToPixel(p0c) : null;
            var px1 = p1c ? (chartState.coordToPixel ? chartState.coordToPixel(p1c) : null) : this.currentPos;
            var px2 = p2c ? (chartState.coordToPixel ? chartState.coordToPixel(p2c) : null) : this.currentPos;

            if (!px0 || !px1) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            this._dpr = dpr;
            this._cw = cw;
            this._ch = ch;

            // If we only have 1 point, draw a dashed connector line to the cursor
            if (this.coords.length < 2) {
                ctx.save();
                ctx.beginPath();
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = '#787b86';
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.6;
                ctx.moveTo(px0.x, px0.y);
                ctx.lineTo(px1.x, px1.y);
                ctx.stroke();
                ctx.restore();
                return;
            }

            // We have at least 2 points (p0, p1). If p2 is not yet placed, px2 is this.currentPos.
            var style = this.style || {};
            var fontSize = style.fontSize || 11;
            var showLabel = style.showLabel !== false;

            // Pre-compute clipped lines for all levels
            var lines = [];
            for (var i = 0; i < CHANNEL_LEVELS.length; i++) {
                var clip = this._clipLevelLine([px0, px1, px2], CHANNEL_LEVELS[i].value, cw, ch);
                lines.push({ level: CHANNEL_LEVELS[i], clip: clip });
            }

            // Draw fill bands between adjacent levels (back to front)
            for (var fi = CHANNEL_FILLS.length - 1; fi >= 0; fi--) {
                var ff = CHANNEL_FILLS[fi];
                var loClip = null, hiClip = null;
                for (var li = 0; li < lines.length - 1; li++) {
                    if (lines[li].level.value === ff.from && lines[li + 1].level.value === ff.to) {
                        loClip = lines[li].clip;
                        hiClip = lines[li + 1].clip;
                        break;
                    }
                }
                if (loClip && loClip.length >= 2 && hiClip && hiClip.length >= 2) {
                    ctx.beginPath();
                    ctx.moveTo(loClip[0].x, loClip[0].y);
                    ctx.lineTo(loClip[1].x, loClip[1].y);
                    ctx.lineTo(hiClip[1].x, hiClip[1].y);
                    ctx.lineTo(hiClip[0].x, hiClip[0].y);
                    ctx.closePath();
                    ctx.fillStyle = ff.color;
                    ctx.globalAlpha = ff.opacity;
                    ctx.fill();
                }
            }
            ctx.globalAlpha = 1;

            // Draw the anchor-to-anchor trend line (full, unclipped, dashed)
            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = '#787b86';
            ctx.lineWidth = 1;
            ctx.globalAlpha = 0.5;
            ctx.moveTo(px0.x, px0.y);
            ctx.lineTo(px1.x, px1.y);
            ctx.stroke();
            ctx.restore();

            // Draw level lines and labels
            if (showLabel) {
                ctx.save();
                ctx.font = fontSize + 'px -apple-system, sans-serif';
                ctx.textBaseline = 'middle';
            }
            for (var li = 0; li < lines.length; li++) {
                var l = lines[li];
                if (!l.clip || l.clip.length < 2) continue;
                var lColor = l.level.color || '#787b86';
                ctx.beginPath();
                ctx.moveTo(l.clip[0].x, l.clip[0].y);
                ctx.lineTo(l.clip[1].x, l.clip[1].y);
                ctx.strokeStyle = lColor;
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.8;
                ctx.setLineDash([]);
                ctx.stroke();

                if (showLabel) {
                    var rightP = l.clip[0].x > l.clip[1].x ? l.clip[0] : l.clip[1];
                    var price = 0;
                    if (p1c) {
                        price = this._priceAtX(rightP.x, l.clip, px0, px1, p0c, p1c);
                    } else if (chartState && chartState.pixelToCoord) {
                        var c = chartState.pixelToCoord(rightP.x, rightP.y);
                        if (c) price = c.price;
                    }
                    var ratioStr = l.level.value.toFixed(3);
                    if (ratioStr.indexOf('.') >= 0) {
                        ratioStr = ratioStr.replace(/0+$/, '');
                        if (ratioStr.endsWith('.')) ratioStr = ratioStr.slice(0, -1);
                    }
                    var label = ratioStr + ' (' + price.toFixed(2) + ')';
                    var textWidth = ctx.measureText(label).width;
                    var labelX = rightP.x - textWidth - 8;
                    if (labelX < 5) labelX = 5;
                    var labelY = rightP.y;
                    var bgPad = 2;
                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(labelX - bgPad, labelY - fontSize/2 - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lColor;
                    ctx.textAlign = 'left';
                    ctx.fillText(label, labelX, labelY);
                }
            }
            if (showLabel) ctx.restore();

            // Draw Y-axis and X-axis projections for the 3 points
            if (this.showAxisLabels && isSelected) {
                var validPxs = [px0, px1];
                if (this.coords.length >= 3) {
                    validPxs.push(px2);
                }
                this.drawAxisLabels(ctx, validPxs, chartState);
            }
        }

        // Compute the price at a given X pixel position along a clipped channel line
        _priceAtX(xPx, clip, px0, px1, p0c, p1c) {
            if (!clip || clip.length < 2) return 0;
            var cdx = clip[1].x - clip[0].x;
            var cdy = clip[1].y - clip[0].y;
            if (Math.abs(cdx) < 0.001) return (p0c.price + p1c.price) / 2;
            var t = (xPx - clip[0].x) / cdx;
            var yPx = clip[0].y + t * cdy;
            // Convert Y pixel to price using chartState priceToY inverse
            var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
            if (series && typeof series.coordinateToPrice === 'function') {
                var p = series.coordinateToPrice(yPx);
                if (p != null) return p;
            }
            return 0;
        }



        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }
    class FibFan extends BaseRatioDrawing { getRatioDef() { return RATIO_DEFS.fib_fan; } }
    // Fibonacci sequence for Time Zones (TradingView defaults, up to 2584)
    var TIMEZONE_FIBS = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597, 2584];

    class FibTimeZone extends BaseDrawing {
        _minAnchors() { return 2; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) { return null; }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return [];
            var dpr = this._dpr || window.devicePixelRatio || 1;
            var cw = this._cw || 800;
            var ch = this._ch || 500;
            if (!this._zoneSegs) return [];
            return this._zoneSegs;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c = this.coords.length >= 2 ? this.coords[1] : null;

            if (!p2c && this.currentPos && chartState.pixelToCoord) {
                var coord = chartState.pixelToCoord(this.currentPos.x, this.currentPos.y);
                if (coord) {
                    p2c = {
                        logical: coord.logical !== undefined ? coord.logical : coord.time,
                        price: coord.price
                    };
                }
            }

            if (!p2c || p1c.logical == null || p2c.logical == null) return;
            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            this._dpr = dpr;
            this._cw = cw;
            this._ch = ch;
            var style = this.style || {};
            var fontSize = style.fontSize || 10;
            var showLabel = style.showLabel !== false;
            var lColor = style.color || '#2196f3';

            var baseBars = p2c.logical - p1c.logical;
            if (baseBars === 0) baseBars = 1;

            // Build zone lines
            var segs = [];
            for (var i = 0; i < TIMEZONE_FIBS.length; i++) {
                var logical = p1c.logical + baseBars * TIMEZONE_FIBS[i];
                var x = chartState.logicalToX ? chartState.logicalToX(logical) : null;
                if (x == null || x < -10 || x > cw + 10) continue;
                segs.push({ idx: i, logical: logical, x: x, fib: TIMEZONE_FIBS[i] });
            }
            this._zoneSegs = null;
            var zoneSegs = [];
            for (var si = 0; si < segs.length; si++) {
                zoneSegs.push({ p1: { x: segs[si].x, y: 0 }, p2: { x: segs[si].x, y: ch } });
            }
            this._zoneSegs = zoneSegs;

            // Draw vertical lines
            ctx.save();
            ctx.setLineDash([]);
            for (var si = 0; si < segs.length; si++) {
                ctx.beginPath();
                ctx.strokeStyle = lColor;
                ctx.lineWidth = style.width || 1;
                ctx.globalAlpha = style.opacity != null ? style.opacity : 1;
                ctx.moveTo(segs[si].x, 0);
                ctx.lineTo(segs[si].x, ch);
                ctx.stroke();
            }

            // Draw labels at bottom
            if (showLabel) {
                ctx.font = fontSize + 'px -apple-system, sans-serif';
                ctx.textBaseline = 'bottom';
                for (var si = 0; si < segs.length; si++) {
                    var label = String(segs[si].fib);
                    var textWidth = ctx.measureText(label).width;
                    var labelX = segs[si].x - textWidth / 2;
                    var labelY = ch - 4;
                    var bgPad = 2;
                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(labelX - bgPad, labelY - fontSize - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lColor;
                    ctx.textAlign = 'left';
                    ctx.fillText(label, labelX, labelY);
                }
            }
            ctx.restore();

            // Draw dashed guide between p1 and p2
            var px1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px2 = chartState.coordToPixel ? chartState.coordToPixel(p2c) : null;
            if (px1 && px2) {
                ctx.save();
                ctx.beginPath();
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = '#787b86';
                ctx.lineWidth = 0.5;
                ctx.globalAlpha = 0.5;
                ctx.moveTo(px1.x, px1.y);
                ctx.lineTo(px2.x, px2.y);
                ctx.stroke();
                ctx.restore();
            }
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2196f3' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2196f3';
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }
    // Fibonacci level definitions (TradingView-parity ratios and colors)
    var CIRCLES_LEVELS = [
        { value: 0.236, color: '#f44336' },
        { value: 0.382, color: '#ff9800' },
        { value: 0.5,   color: '#ffc107' },
        { value: 0.618, color: '#4caf50' },
        { value: 0.786, color: '#00bcd4' },
        { value: 1,     color: '#787b86' },
        { value: 1.618, color: '#2196f3' },
        { value: 2.618, color: '#9c27b0' },
        { value: 3.618, color: '#e91e63' },
        { value: 4.236, color: '#f44336' },
    ];

    // Fill ring definitions between every adjacent level pair
    function _buildCirclesFills() {
        var arr = [];
        for (var _fi = 0; _fi < CIRCLES_LEVELS.length - 1; _fi++) {
            arr.push({ from: CIRCLES_LEVELS[_fi].value, to: CIRCLES_LEVELS[_fi + 1].value,
                       color: CIRCLES_LEVELS[_fi].color, opacity: 0.12 });
        }
        return arr;
    }
    var CIRCLES_FILLS = _buildCirclesFills();

    // Approximate n line segments for an ellipse (used in hit testing)
    function _ellipseSegments(cx, cy, rx, ry, n) {
        var segs = [];
        for (var _si = 0; _si < n; _si++) {
            var a1 = (_si / n) * Math.PI * 2;
            var a2 = ((_si + 1) / n) * Math.PI * 2;
            segs.push({
                p1: { x: cx + rx * Math.cos(a1), y: cy + ry * Math.sin(a1) },
                p2: { x: cx + rx * Math.cos(a2), y: cy + ry * Math.sin(a2) }
            });
        }
        return segs;
    }

    function _ellipsePolygon(cx, cy, rx, ry, n) {
        var verts = [];
        for (var _vi = 0; _vi < n; _vi++) {
            var a = (_vi / n) * Math.PI * 2;
            verts.push({ x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) });
        }
        return verts;
    }

    class FibCircles extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.showAxisLabels = true;
        }

        _minAnchors() { return 2; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var rx = Math.abs(pixels[1].x - pixels[0].x);
            var ry = Math.abs(pixels[1].y - pixels[0].y);
            var outerRx = rx * 4.236;
            var outerRy = ry * 4.236;
            if (outerRx < 1 || outerRy < 1) return null;
            return _ellipsePolygon(pixels[0].x, pixels[0].y, outerRx, outerRy, 20);
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return [];
            var rx = Math.abs(pixels[1].x - pixels[0].x);
            var ry = Math.abs(pixels[1].y - pixels[0].y);
            var segs = [];
            for (var i = 0; i < CIRCLES_LEVELS.length; i++) {
                var rX = rx * CIRCLES_LEVELS[i].value;
                var rY = ry * CIRCLES_LEVELS[i].value;
                if (rX > 1 && rY > 1) {
                    segs = segs.concat(_ellipseSegments(pixels[0].x, pixels[0].y, rX, rY, 16));
                }
            }
            return segs;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c;
            var tempPushed = false;
            if (this.coords.length >= 2) {
                p2c = this.coords[1];
            } else if (this.currentPos && chartState.pixelToCoord) {
                p2c = chartState.pixelToCoord(this.currentPos.x, this.currentPos.y);
                if (p2c) {
                    this.coords.push(p2c);
                    tempPushed = true;
                }
            }
            if (!p1c || !p2c || p1c.price == null || p2c.price == null) {
                if (tempPushed) this.coords.pop();
                return;
            }

            var px1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px2;
            if (tempPushed) {
                px2 = this.currentPos;
            } else {
                px2 = chartState.coordToPixel ? chartState.coordToPixel(p2c) : null;
            }
            if (!px1 || !px2) {
                if (tempPushed) this.coords.pop();
                return;
            }

            var cx = px1.x, cy = px1.y;
            var rx = Math.abs(px2.x - cx);
            var ry = Math.abs(px2.y - cy);
            if (rx < 1 || ry < 1) {
                if (tempPushed) this.coords.pop();
                return;
            }

            var style = this.style || {};
            var showLabel = style.showLabel !== false;
            var fontSize = style.fontSize || 11;

            // Draw ring fills from largest to smallest (back to front)
            for (var fi = CIRCLES_FILLS.length - 1; fi >= 0; fi--) {
                var ff = CIRCLES_FILLS[fi];
                var innerRx = rx * ff.from;
                var innerRy = ry * ff.from;
                var outerRx = rx * ff.to;
                var outerRy = ry * ff.to;
                if (outerRx < 1 || outerRy < 1) continue;

                ctx.beginPath();
                ctx.ellipse(cx, cy, outerRx, outerRy, 0, 0, Math.PI * 2);
                if (innerRx >= 1 && innerRy >= 1) {
                    ctx.ellipse(cx, cy, innerRx, innerRy, 0, 0, Math.PI * 2, true);
                }
                ctx.fillStyle = ff.color;
                ctx.globalAlpha = ff.opacity;
                ctx.fill();
            }
            ctx.globalAlpha = 1;

            // Draw circle outlines (largest to smallest)
            for (var li = 0; li < CIRCLES_LEVELS.length; li++) {
                var lv = CIRCLES_LEVELS[li];
                var rX = rx * lv.value;
                var rY = ry * lv.value;
                if (rX < 1 || rY < 1) continue;
                ctx.beginPath();
                ctx.ellipse(cx, cy, rX, rY, 0, 0, Math.PI * 2);
                ctx.strokeStyle = lv.color;
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.8;
                ctx.stroke();
            }
            ctx.globalAlpha = 1;

            // Draw dashed connector line between p1 and p2 (TradingView-parity)
            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = '#787b86';
            ctx.lineWidth = 1;
            ctx.globalAlpha = 0.6;
            ctx.moveTo(px1.x, px1.y);
            ctx.lineTo(px2.x, px2.y);
            ctx.stroke();
            ctx.restore();

            // Draw labels at left side of each circle
            if (showLabel) {
                ctx.save();
                ctx.font = fontSize + 'px -apple-system, sans-serif';
                ctx.textBaseline = 'middle';
                for (var li = 0; li < CIRCLES_LEVELS.length; li++) {
                    var lv = CIRCLES_LEVELS[li];
                    var rX = rx * lv.value;
                    var rY = ry * lv.value;
                    if (rX < 10) continue;
                    var ratioStr = lv.value.toFixed(3);
                    // Strip trailing zeros after decimal
                    if (ratioStr.indexOf('.') >= 0) {
                        ratioStr = ratioStr.replace(/0+$/, '');
                        if (ratioStr.endsWith('.')) ratioStr = ratioStr.slice(0, -1);
                    }
                    var labelX = cx - rX - 6;
                    var labelY = cy;
                    var textWidth = ctx.measureText(ratioStr).width;
                    var bgPad = 2;
                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(labelX - textWidth - bgPad, labelY - fontSize/2 - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lv.color;
                    ctx.textAlign = 'right';
                    ctx.fillText(ratioStr, labelX - bgPad, labelY);
                }
                ctx.restore();
            }

            // Center marker (small dot always visible at p1)
            ctx.beginPath();
            ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
            ctx.fillStyle = '#787b86';
            ctx.fill();

            // Draw Y-axis and X-axis projections for the 2 points
            if (this.showAxisLabels && (isSelected || tempPushed)) {
                this.drawAxisLabels(ctx, [px1, px2], chartState);
            }

            // Clean up temporary coordinate
            if (tempPushed) {
                this.coords.pop();
            }
        }



        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }
    var ARC_LEVELS = [
        { value: 0.236, color: '#ff5722' },
        { value: 0.382, color: '#ff9800' },
        { value: 0.5,   color: '#ffc107' },
        { value: 0.618, color: '#4caf50' },
        { value: 0.786, color: '#009688' },
        { value: 1,     color: '#2196f3' },
        { value: 1.272, color: '#03a9f4' },
        { value: 1.618, color: '#1976d2' },
        { value: 2.618, color: '#f44336' },
        { value: 3.618, color: '#9c27b0' },
        { value: 4.236, color: '#e91e63' },
    ];

    function _buildArcFills() {
        var arr = [];
        for (var _fi = 0; _fi < ARC_LEVELS.length - 1; _fi++) {
            arr.push({ from: ARC_LEVELS[_fi].value, to: ARC_LEVELS[_fi + 1].value,
                       color: ARC_LEVELS[_fi].color, opacity: 0.12 });
        }
        return arr;
    }
    var ARC_FILLS = _buildArcFills();

    class FibArcs extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 2;
            this.showAxisLabels = true;
        }

        _minAnchors() { return 2; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var cx = pixels[0].x, cy = pixels[0].y;
            var dx = pixels[1].x - cx, dy = pixels[1].y - cy;
            var baseR = Math.sqrt(dx*dx + dy*dy);
            var maxR = baseR * 4.236;
            if (maxR < 1) return [pixels[0]];
            return [
                { x: cx - maxR, y: cy - maxR },
                { x: cx + maxR, y: cy - maxR },
                { x: cx + maxR, y: cy + maxR },
                { x: cx - maxR, y: cy + maxR }
            ];
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return [];
            return this._levelSegs || [];
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c = this.coords.length >= 2 ? this.coords[1] : null;

            var px1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px2 = p2c ? (chartState.coordToPixel ? chartState.coordToPixel(p2c) : null) : this.currentPos;

            if (!px1 || !px2) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;

            var style = this.style || {};
            var showLabel = style.showLabel !== false;
            var fontSize = style.fontSize || 10;

            if (this.coords.length < 2) {
                ctx.save();
                ctx.strokeStyle = '#787b86';
                ctx.lineWidth = 1;
                ctx.setLineDash([4, 4]);
                ctx.globalAlpha = 0.5;
                ctx.beginPath();
                ctx.moveTo(px1.x, px1.y);
                ctx.lineTo(px2.x, px2.y);
                ctx.stroke();
                ctx.restore();
                return;
            }

            var cx = px1.x, cy = px1.y;
            var dx = px2.x - cx, dy = px2.y - cy;
            var baseR = Math.sqrt(dx*dx + dy*dy);
            if (baseR < 1) return;

            // Go only 2 sides vertically: above side (if P2 is above P1) or below side (if P2 is below P1)
            var startAngle = 0;
            var endAngle = Math.PI;
            if (dy < 0) {
                // Above side
                startAngle = Math.PI;
                endAngle = 2 * Math.PI;
            }

            var levels = [];
            for (var i = 0; i < ARC_LEVELS.length; i++) {
                levels.push({
                    value: ARC_LEVELS[i].value,
                    radius: baseR * ARC_LEVELS[i].value,
                    color: ARC_LEVELS[i].color
                });
            }

            for (var fi = ARC_FILLS.length - 1; fi >= 0; fi--) {
                var ff = ARC_FILLS[fi];
                var innerR = baseR * ff.from;
                var outerR = baseR * ff.to;
                if (outerR < 2) continue;
                ctx.save();
                ctx.beginPath();
                ctx.arc(cx, cy, outerR, startAngle, endAngle, false);
                ctx.arc(cx, cy, innerR, endAngle, startAngle, true);
                ctx.closePath();
                ctx.fillStyle = ff.color;
                ctx.globalAlpha = ff.opacity;
                ctx.fill();
                ctx.restore();
            }

            this._levelSegs = [];
            for (var i = 0; i < levels.length; i++) {
                var l = levels[i];
                if (l.radius < 2) continue;

                ctx.save();
                ctx.beginPath();
                ctx.strokeStyle = l.color;
                ctx.lineWidth = 2;
                ctx.globalAlpha = 1;
                ctx.arc(cx, cy, l.radius, startAngle, endAngle);
                ctx.stroke();
                ctx.restore();

                var numSegs = Math.max(4, Math.min(32, Math.round(l.radius / 10)));
                for (var s = 0; s < numSegs; s++) {
                    var a1 = startAngle + (s / numSegs) * (endAngle - startAngle);
                    var a2 = startAngle + ((s + 1) / numSegs) * (endAngle - startAngle);
                    this._levelSegs.push({
                        p1: { x: cx + l.radius * Math.cos(a1), y: cy + l.radius * Math.sin(a1) },
                        p2: { x: cx + l.radius * Math.cos(a2), y: cy + l.radius * Math.sin(a2) }
                    });
                }
            }

            if (showLabel) {
                ctx.save();
                ctx.font = fontSize + 'px -apple-system, sans-serif';
                ctx.textBaseline = 'bottom';
                for (var i = 0; i < levels.length; i++) {
                    var l = levels[i];
                    if (l.radius < 10) continue;

                    var ratioStr = l.value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');

                    var frac = levels.length > 1 ? i / (levels.length - 1) : 0.5;
                    var labelAngle = startAngle + frac * (endAngle - startAngle);
                    var labelX = cx + l.radius * Math.cos(labelAngle);
                    var labelY = cy + l.radius * Math.sin(labelAngle);

                    if (labelX < -50 || labelX > cw + 50 || labelY < -50 || labelY > ch + 50) continue;

                    ctx.textAlign = 'center';
                    var textWidth = ctx.measureText(ratioStr).width;
                    var bgPad = 2;

                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(labelX - textWidth/2 - bgPad, labelY - fontSize - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = l.color;
                    ctx.fillText(ratioStr, labelX, labelY - 2);
                }
                ctx.restore();
            }

            ctx.save();
            ctx.strokeStyle = '#787b86';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.globalAlpha = 0.7;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(px2.x, px2.y);
            ctx.stroke();
            ctx.restore();

            if (this.showAxisLabels && isSelected) {
                this.drawAxisLabels(ctx, [px1, px2], chartState);
            }
        }
    }
    // Speed Resistance level definitions (TradingView-parity ratios and colors)
    var SPEED_LEVELS = [
        { value: 0,     color: '#787b86' },
        { value: 0.25,  color: '#ff9800' },
        { value: 0.382, color: '#00bcd4' },
        { value: 0.5,   color: '#4caf50' },
        { value: 0.618, color: '#009688' },
        { value: 0.75,  color: '#2196f3' },
        { value: 1,     color: '#787b86' },
    ];

    function _buildSpeedFills() {
        var arr = [];
        for (var _fi = 0; _fi < SPEED_LEVELS.length - 1; _fi++) {
            arr.push({ from: SPEED_LEVELS[_fi].value, to: SPEED_LEVELS[_fi + 1].value,
                       color: SPEED_LEVELS[_fi].color, opacity: 0.12 });
        }
        return arr;
    }
    var SPEED_FILLS = _buildSpeedFills();

    class FibSpeedResistance extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 2;
            this.showAxisLabels = true;
        }

        _minAnchors() { return 2; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            return [
                { x: pixels[0].x, y: pixels[0].y },
                { x: pixels[1].x, y: pixels[0].y },
                { x: pixels[1].x, y: pixels[1].y },
                { x: pixels[0].x, y: pixels[1].y }
            ];
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return [];
            var segs = [];
            segs.push({ p1: { x: pixels[0].x, y: pixels[0].y }, p2: { x: pixels[1].x, y: pixels[0].y } });
            segs.push({ p1: { x: pixels[1].x, y: pixels[0].y }, p2: { x: pixels[1].x, y: pixels[1].y } });
            segs.push({ p1: { x: pixels[1].x, y: pixels[1].y }, p2: { x: pixels[0].x, y: pixels[1].y } });
            segs.push({ p1: { x: pixels[0].x, y: pixels[1].y }, p2: { x: pixels[0].x, y: pixels[0].y } });
            if (this._fanLines) {
                segs = segs.concat(this._fanLines);
            }
            return segs;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c = this.coords.length >= 2 ? this.coords[1] : null;

            var px0 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px1 = p2c ? (chartState.coordToPixel ? chartState.coordToPixel(p2c) : null) : this.currentPos;
            if (!px0 || !px1) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            this._dpr = dpr;
            this._cw = cw;
            this._ch = ch;
            var style = this.style || {};
            var fontSize = style.fontSize || 10;
            var showLabel = style.showLabel !== false;

            var ox = px0.x, oy = px0.y;
            var tx = px1.x, ty = px1.y;

            // 1. Draw Grid Box (horizontal & vertical lines)
            ctx.save();
            ctx.strokeStyle = 'rgba(120, 123, 134, 0.4)';
            ctx.lineWidth = 1;

            for (var i = 0; i < SPEED_LEVELS.length; i++) {
                var lv = SPEED_LEVELS[i];
                var y = oy + (ty - oy) * lv.value;
                ctx.beginPath();
                ctx.moveTo(ox, y);
                ctx.lineTo(tx, y);
                ctx.stroke();

                var x = ox + (tx - ox) * lv.value;
                ctx.beginPath();
                ctx.moveTo(x, oy);
                ctx.lineTo(x, ty);
                ctx.stroke();
            }
            ctx.restore();

            // 2. Generate and Draw Rays (Extend only in positive direction towards infinity)
            var fanLines = [];
            var rayColors = [];

            function getRayDir(targetX, targetY) {
                var dx = targetX - ox;
                var dy = targetY - oy;
                var len = Math.sqrt(dx * dx + dy * dy);
                if (len < 0.1) return null;
                return { ux: dx / len, uy: dy / len };
            }

            function clipRay(ux, uy) {
                var tMin = Infinity;
                var endP = null;

                if (ux < -0.0001) {
                    var t = -ox / ux;
                    if (t > 0 && t < tMin) {
                        var y = oy + t * uy;
                        if (y >= 0 && y <= ch) { tMin = t; endP = { x: 0, y: y }; }
                    }
                }
                if (ux > 0.0001) {
                    var t = (cw - ox) / ux;
                    if (t > 0 && t < tMin) {
                        var y = oy + t * uy;
                        if (y >= 0 && y <= ch) { tMin = t; endP = { x: cw, y: y }; }
                    }
                }
                if (uy < -0.0001) {
                    var t = -oy / uy;
                    if (t > 0 && t < tMin) {
                        var x = ox + t * ux;
                        if (x >= 0 && x <= cw) { tMin = t; endP = { x: x, y: 0 }; }
                    }
                }
                if (uy > 0.0001) {
                    var t = (ch - oy) / uy;
                    if (t > 0 && t < tMin) {
                        var x = ox + t * ux;
                        if (x >= 0 && x <= cw) { tMin = t; endP = { x: x, y: ch }; }
                    }
                }
                if (endP) {
                    return { p1: { x: ox, y: oy }, p2: endP };
                }
                return { p1: { x: ox, y: oy }, p2: { x: ox + ux * 2000, y: oy + uy * 2000 } };
            }

            var vRays = [];
            for (var i = 0; i < SPEED_LEVELS.length; i++) {
                var lv = SPEED_LEVELS[i];
                var yVal = oy + (ty - oy) * lv.value;
                var dir = getRayDir(tx, yVal);
                if (dir) {
                    var ray = clipRay(dir.ux, dir.uy);
                    vRays.push(ray);
                    fanLines.push(ray);
                    rayColors.push(lv.color);
                } else {
                    vRays.push(null);
                }
            }

            var hRays = [];
            for (var i = 0; i < SPEED_LEVELS.length; i++) {
                var lv = SPEED_LEVELS[i];
                var xVal = ox + (tx - ox) * lv.value;
                var dir = getRayDir(xVal, ty);
                if (dir) {
                    var ray = clipRay(dir.ux, dir.uy);
                    hRays.push(ray);
                    fanLines.push(ray);
                    rayColors.push(lv.color);
                } else {
                    hRays.push(null);
                }
            }

            this._fanLines = fanLines;

            // 3. Draw Fills between adjacent rays
            for (var fi = 0; fi < SPEED_FILLS.length; fi++) {
                var ff = SPEED_FILLS[fi];
                var r1 = vRays[fi];
                var r2 = vRays[fi + 1];
                if (r1 && r2) {
                    ctx.beginPath();
                    ctx.moveTo(ox, oy);
                    ctx.lineTo(r1.p2.x, r1.p2.y);
                    ctx.lineTo(r2.p2.x, r2.p2.y);
                    ctx.closePath();
                    ctx.fillStyle = ff.color;
                    ctx.globalAlpha = ff.opacity;
                    ctx.fill();
                }
            }
            for (var fi = 0; fi < SPEED_FILLS.length; fi++) {
                var ff = SPEED_FILLS[fi];
                var r1 = hRays[fi];
                var r2 = hRays[fi + 1];
                if (r1 && r2) {
                    ctx.beginPath();
                    ctx.moveTo(ox, oy);
                    ctx.lineTo(r1.p2.x, r1.p2.y);
                    ctx.lineTo(r2.p2.x, r2.p2.y);
                    ctx.closePath();
                    ctx.fillStyle = ff.color;
                    ctx.globalAlpha = ff.opacity;
                    ctx.fill();
                }
            }
            ctx.globalAlpha = 1;

            // 4. Draw Rays
            for (var i = 0; i < fanLines.length; i++) {
                var lc = rayColors[i] || '#787b86';
                ctx.beginPath();
                ctx.strokeStyle = lc;
                ctx.lineWidth = style.width || 1.5;
                ctx.globalAlpha = 0.85;
                ctx.moveTo(fanLines[i].p1.x, fanLines[i].p1.y);
                ctx.lineTo(fanLines[i].p2.x, fanLines[i].p2.y);
                ctx.stroke();
            }

            // 5. Draw Labels on boundaries
            if (showLabel) {
                ctx.save();
                ctx.font = fontSize + 'px -apple-system, sans-serif';
                ctx.textBaseline = 'middle';

                for (var i = 0; i < SPEED_LEVELS.length; i++) {
                    var lv = SPEED_LEVELS[i];
                    var xVal = ox + (tx - ox) * lv.value;
                    var label = lv.value.toFixed(2);
                    if (label.indexOf('.') >= 0) {
                        label = label.replace(/0+$/, '').replace(/\.$/, '');
                    }
                    var textWidth = ctx.measureText(label).width;
                    var bgPad = 2;
                    var lx = xVal - textWidth / 2;
                    var ly = ty + (ty >= oy ? 8 : -8);
                    
                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(lx - bgPad, ly - fontSize/2 - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lv.color;
                    ctx.fillText(label, lx, ly);
                }

                for (var i = 0; i < SPEED_LEVELS.length; i++) {
                    var lv = SPEED_LEVELS[i];
                    var yVal = oy + (ty - oy) * lv.value;
                    var label = lv.value.toFixed(2);
                    if (label.indexOf('.') >= 0) {
                        label = label.replace(/0+$/, '').replace(/\.$/, '');
                    }
                    var textWidth = ctx.measureText(label).width;
                    var bgPad = 2;
                    var lx = tx + (tx >= ox ? 6 : -textWidth - 6);
                    var ly = yVal;
                    
                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(lx - bgPad, ly - fontSize/2 - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lv.color;
                    ctx.fillText(label, lx, ly);
                }
                ctx.restore();
            }

            // 6. Draw dashed origin-to-end connector guide
            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = '#787b86';
            ctx.lineWidth = 1;
            ctx.globalAlpha = 0.6;
            ctx.moveTo(ox, oy);
            ctx.lineTo(tx, ty);
            ctx.stroke();
            ctx.restore();

            // 7. Y-axis and X-axis highlights for the anchors
            if (this.showAxisLabels && isSelected) {
                var validPxs = [px0];
                if (p2c) {
                    validPxs.push(px1);
                }
                this.drawAxisLabels(ctx, validPxs, chartState);
            }
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }

        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }
    class FibSpiral extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 2;
            this.showAxisLabels = true;
        }
        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }
        getFillShape(pixels) { return null; }
        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 2) return [];
            var segs = [];
            var cx = pixels[0].x, cy = pixels[0].y;
            var px = pixels[1].x, py = pixels[1].y;
            var a = Math.sqrt((px - cx) * (px - cx) + (py - cy) * (py - cy));
            if (a < 2) return segs;
            var theta0 = Math.atan2(py - cy, px - cx);
            var b = 0.128;
            var maxR = 5000;
            var theta = theta0 - 15;
            var r = a * Math.exp(b * (theta - theta0));
            if (r < 1) { theta = theta0 + Math.log(1 / a) / b; r = 1; }
            var prevX = cx + r * Math.cos(theta);
            var prevY = cy + r * Math.sin(theta);
            var maxTheta = theta0 + 40;
            var step = 0.02;
            while (theta < maxTheta) {
                theta += step;
                r = a * Math.exp(b * (theta - theta0));
                if (r > maxR) break;
                var x = cx + r * Math.cos(theta);
                var y = cy + r * Math.sin(theta);
                segs.push({ p1: { x: prevX, y: prevY }, p2: { x: x, y: y } });
                prevX = x; prevY = y;
                step = Math.max(0.005, Math.min(0.08, 0.3 / Math.max(r, a)));
            }
            return segs;
        }
        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 2 || !chartState) return;
            var p1c = this.coords[0];
            var p2c = this.coords[1];
            var px0 = _px(chartState, p1c);
            var px1 = _px(chartState, p2c);
            if (!px0 || !px1) return;
            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            var cx = px0.x, cy = px0.y;
            var px = px1.x, py = px1.y;
            var a = Math.sqrt((px - cx) * (px - cx) + (py - cy) * (py - cy));
            if (a < 1) return;
            var theta0 = Math.atan2(py - cy, px - cx);
            var b = 0.128;
            var viewportDiag = Math.sqrt(cw * cw + ch * ch);
            var maxR = viewportDiag * 1.2;
            var style = this.style || {};
            var color = style.color || '#00BCD4';
            var width = style.width != null ? style.width : 2;
            var opacity = style.opacity != null ? style.opacity : 1;
            ctx.save();
            ctx.beginPath();
            var theta = theta0 - 15;
            var r = a * Math.exp(b * (theta - theta0));
            if (r < 1) { theta = theta0 + Math.log(1 / a) / b; r = 1; }
            var x = cx + r * Math.cos(theta);
            var y = cy + r * Math.sin(theta);
            ctx.moveTo(x, y);
            var maxTheta = theta0 + 40;
            var step = 0.02;
            var outOfBounds = 0;
            while (theta < maxTheta) {
                theta += step;
                r = a * Math.exp(b * (theta - theta0));
                if (r > maxR) break;
                x = cx + r * Math.cos(theta);
                y = cy + r * Math.sin(theta);
                if (x >= -cw && x <= cw * 2 && y >= -ch && y <= ch * 2) { outOfBounds = 0; }
                else { outOfBounds++; if (outOfBounds > 5) break; }
                ctx.lineTo(x, y);
                step = Math.max(0.005, Math.min(0.08, 0.3 / Math.max(r, a)));
            }
            ctx.strokeStyle = color;
            ctx.lineWidth = width;
            ctx.globalAlpha = opacity;
            ctx.stroke();
            ctx.restore();
            if (this.showAxisLabels && isSelected) {
                this.drawAxisLabels(ctx, [px0, px1], chartState);
            }
        }
        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                var newCoord = chartState.pixelToCoord(pixel.x + dx, pixel.y + dy);
                return newCoord || c;
            });
        }
        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }
    // Fibonacci wedge level definitions (TradingView-parity ratios and colors)
    var WEDGE_LEVELS = [
        { value: 0,     color: '#787b86' },
        { value: 0.236, color: '#f44336' },
        { value: 0.382, color: '#ff9800' },
        { value: 0.5,   color: '#4caf50' },
        { value: 0.618, color: '#00bcd4' },
        { value: 0.786, color: '#2196f3' },
        { value: 1,     color: '#787b86' }
    ];

    // Fill band definitions between every adjacent level pair
    function _buildWedgeFills() {
        var arr = [];
        for (var _fi = 0; _fi < WEDGE_LEVELS.length - 1; _fi++) {
            arr.push({ from: WEDGE_LEVELS[_fi].value, to: WEDGE_LEVELS[_fi + 1].value,
                       color: WEDGE_LEVELS[_fi].color, opacity: 0.12 });
        }
        return arr;
    }
    var WEDGE_FILLS = _buildWedgeFills();

    class FibWedge extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
            this.showAxisLabels = false;
        }

        _minAnchors() { return 3; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 3) return null;
            var px1 = pixels[0], px2 = pixels[1], px3 = pixels[2];
            var ux1 = px1.x - px2.x, uy1 = px1.y - px2.y;
            var ux3 = px3.x - px2.x, uy3 = px3.y - px2.y;
            var R1 = Math.sqrt(ux1 * ux1 + uy1 * uy1);
            var R2 = Math.sqrt(ux3 * ux3 + uy3 * uy3);
            if (R1 < 0.1 && R2 < 0.1) return [px2];

            var theta1 = Math.atan2(uy1, ux1);
            var theta3 = Math.atan2(uy3, ux3);
            var diff = theta3 - theta1;
            while (diff < -Math.PI) diff += 2 * Math.PI;
            while (diff > Math.PI) diff -= 2 * Math.PI;

            var lvMax = WEDGE_LEVELS[WEDGE_LEVELS.length - 1].value;
            var pts = [px2];
            var numPoints = 16;
            for (var j = 0; j <= numPoints; j++) {
                var t = j / numPoints;
                var theta = theta1 + t * diff;
                var radius = lvMax * (R1 + t * (R2 - R1));
                pts.push({
                    x: px2.x + radius * Math.cos(theta),
                    y: px2.y + radius * Math.sin(theta)
                });
            }
            return pts;
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 3) return [];
            return this._levelSegs || [];
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c = this.coords.length >= 2 ? this.coords[1] : null;
            var p3c = this.coords.length >= 3 ? this.coords[2] : null;

            var px1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px2 = p2c ? (chartState.coordToPixel ? chartState.coordToPixel(p2c) : null) : this.currentPos;
            var px3 = p3c ? (chartState.coordToPixel ? chartState.coordToPixel(p3c) : null) : (p2c ? this.currentPos : null);

            if (!px1 || !px2) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            this._dpr = dpr;
            this._cw = cw;
            this._ch = ch;

            var style = this.style || {};
            var showLabel = style.showLabel !== false;
            var fontSize = style.fontSize || 10;

            if (!p2c) {
                // Placing 2nd point: draw dashed P1 -> cursor
                ctx.save();
                ctx.strokeStyle = style.color || '#787b86';
                ctx.lineWidth = style.width || 1.5;
                ctx.beginPath();
                ctx.setLineDash([4, 4]);
                ctx.moveTo(px1.x, px1.y);
                ctx.lineTo(px2.x, px2.y);
                ctx.stroke();
                ctx.restore();
                return;
            }

            if (!px3) return;

            // ux1, uy1 is V1 (vector from P2 to P1)
            // ux3, uy3 is V3 (vector from P2 to P3)
            var ux1 = px1.x - px2.x, uy1 = px1.y - px2.y;
            var ux3 = px3.x - px2.x, uy3 = px3.y - px2.y;

            // Generate arc points and peaks using polar coordinate interpolation
            var R1 = Math.sqrt(ux1 * ux1 + uy1 * uy1);
            var R2 = Math.sqrt(ux3 * ux3 + uy3 * uy3);

            var theta1 = Math.atan2(uy1, ux1);
            var theta3 = Math.atan2(uy3, ux3);

            var diff = theta3 - theta1;
            while (diff < -Math.PI) diff += 2 * Math.PI;
            while (diff > Math.PI) diff -= 2 * Math.PI;

            var levelArcs = [];
            var arcPeaks = [];

            for (var i = 0; i < WEDGE_LEVELS.length; i++) {
                var lv = WEDGE_LEVELS[i];
                var pts = [];
                var numPoints = 40;

                if (R1 > 0.1 || R2 > 0.1) {
                    for (var j = 0; j <= numPoints; j++) {
                        var t = j / numPoints;
                        var theta = theta1 + t * diff;
                        var radius = lv.value * (R1 + t * (R2 - R1));
                        var cx = px2.x + radius * Math.cos(theta);
                        var cy = px2.y + radius * Math.sin(theta);
                        pts.push({ x: cx, y: cy });
                    }
                    
                    var thetaMid = theta1 + 0.5 * diff;
                    var radMid = lv.value * (R1 + R2) / 2;
                    arcPeaks.push({
                        x: px2.x + radMid * Math.cos(thetaMid),
                        y: px2.y + radMid * Math.sin(thetaMid)
                    });
                } else {
                    pts.push(px2);
                    arcPeaks.push(px2);
                }
                levelArcs.push(pts);
            }

            var maxLevel = WEDGE_LEVELS[WEDGE_LEVELS.length - 1].value;

            // 1. Draw boundary rays (start at P2, end at outermost arc ends)
            ctx.save();
            ctx.strokeStyle = style.color || '#787b86';
            ctx.lineWidth = style.width || 1.5;

            // Ray 1 (left side)
            ctx.beginPath();
            ctx.moveTo(px2.x, px2.y);
            if (levelArcs[levelArcs.length - 1].length > 0) {
                var pEnd1 = levelArcs[levelArcs.length - 1][0];
                ctx.lineTo(pEnd1.x, pEnd1.y);
            } else {
                ctx.lineTo(px1.x, px1.y);
            }
            ctx.stroke();

            // Ray 2 (right side)
            ctx.beginPath();
            if (!p3c) {
                ctx.setLineDash([4, 4]);
            }
            ctx.moveTo(px2.x, px2.y);
            if (levelArcs[levelArcs.length - 1].length > 0) {
                var pEnd2 = levelArcs[levelArcs.length - 1][levelArcs[levelArcs.length - 1].length - 1];
                ctx.lineTo(pEnd2.x, pEnd2.y);
            } else {
                ctx.lineTo(px3.x, px3.y);
            }
            ctx.stroke();
            ctx.restore();

            // 3. Draw fills between adjacent arcs
            for (var fi = WEDGE_FILLS.length - 1; fi >= 0; fi--) {
                var ff = WEDGE_FILLS[fi];
                var arc1 = null, arc2 = null;
                for (var li = 0; li < WEDGE_LEVELS.length - 1; li++) {
                    if (WEDGE_LEVELS[li].value === ff.from && WEDGE_LEVELS[li + 1].value === ff.to) {
                        arc1 = levelArcs[li];
                        arc2 = levelArcs[li + 1];
                        break;
                    }
                }
                if (arc1 && arc2 && arc1.length > 0 && arc2.length > 0) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.moveTo(arc2[arc2.length - 1].x, arc2[arc2.length - 1].y);
                    for (var k = arc2.length - 2; k >= 0; k--) {
                        ctx.lineTo(arc2[k].x, arc2[k].y);
                    }
                    ctx.lineTo(arc1[0].x, arc1[0].y);
                    for (var k = 1; k < arc1.length; k++) {
                        ctx.lineTo(arc1[k].x, arc1[k].y);
                    }
                    ctx.closePath();
                    ctx.fillStyle = ff.color;
                    ctx.globalAlpha = ff.opacity;
                    ctx.fill();
                    ctx.restore();
                }
            }

            // 4. Draw arcs
            this._levelSegs = [];
            for (var i = 0; i < WEDGE_LEVELS.length; i++) {
                var lv = WEDGE_LEVELS[i];
                var pts = levelArcs[i];
                if (!pts || pts.length < 2) continue;

                ctx.save();
                ctx.beginPath();
                ctx.strokeStyle = lv.color;
                ctx.lineWidth = style.width || 1.5;
                ctx.globalAlpha = 0.85;
                ctx.moveTo(pts[0].x, pts[0].y);
                for (var k = 1; k < pts.length; k++) {
                    ctx.lineTo(pts[k].x, pts[k].y);
                }
                ctx.stroke();
                ctx.restore();

                for (var k = 0; k < pts.length - 1; k++) {
                    this._levelSegs.push({ p1: pts[k], p2: pts[k+1] });
                }
            }

            // 5. Draw Labels
            if (showLabel) {
                ctx.save();
                ctx.font = fontSize + 'px -apple-system, sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'bottom';

                for (var i = 0; i < WEDGE_LEVELS.length; i++) {
                    var lv = WEDGE_LEVELS[i];
                    var peak = arcPeaks[i];
                    if (!peak) continue;

                    var ratioStr = lv.value.toFixed(3);
                    if (ratioStr.indexOf('.') >= 0) {
                        ratioStr = ratioStr.replace(/0+$/, '').replace(/\.$/, '');
                    }
                    
                    var textWidth = ctx.measureText(ratioStr).width;
                    var bgPad = 2;
                    var lx = peak.x;
                    var xyY = peak.y - 4;

                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(lx - textWidth/2 - bgPad, xyY - fontSize - bgPad, textWidth + 2*bgPad, fontSize + 2*bgPad);
                    ctx.fillStyle = lv.color;
                    ctx.fillText(ratioStr, lx, xyY);
                }
                ctx.restore();
            }

            // 6. Draw axis labels for active anchors
            if (this.showAxisLabels && isSelected) {
                var validPxs = [px1, px2];
                if (p3c) validPxs.push(px3);
                this.drawAxisLabels(ctx, validPxs, chartState);
            }

            // Draw in-progress anchor point handles
            if (!p3c) {
                this.drawHandle(ctx, px1, false);
                this.drawHandle(ctx, px2, false);
            }
        }
    }
    var PITCHFAN_LEVELS = [
        { value: 0,     color: '#2196f3', name: '0' },
        { value: 0.25,  color: '#00bcd4', name: '0.25' },
        { value: 0.382, color: '#009688', name: '0.382' },
        { value: 0.5,   color: '#787b86', name: '0.5' }, // Center ray is neutral gray
        { value: 0.618, color: '#ff9800', name: '0.618' },
        { value: 0.75,  color: '#f44336', name: '0.75' },
        { value: 1,     color: '#787b86', name: '1' }
    ];

    class Pitchfan extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 3;
            this.showAxisLabels = true;
        }

        _minAnchors() { return 3; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 3) return null;
            if (this._rayEnds && this._rayEnds.length >= 2) {
                var first = this._rayEnds[0];
                var last = this._rayEnds[this._rayEnds.length - 1];
                if (first && last) {
                    return [pixels[0], first, last];
                }
            }
            return [pixels[0], pixels[1], pixels[2]];
        }

        getEdgeSegments(pixels, chartState) {
            if (!pixels || pixels.length < 3) return [];
            return this._levelSegs || [];
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState) return;
            var p1c = this.coords[0];
            var p2c = this.coords.length >= 2 ? this.coords[1] : null;
            var p3c = this.coords.length >= 3 ? this.coords[2] : null;

            var px1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var px2 = p2c ? (chartState.coordToPixel ? chartState.coordToPixel(p2c) : null) : this.currentPos;
            var px3 = p3c ? (chartState.coordToPixel ? chartState.coordToPixel(p3c) : null) : (p2c ? this.currentPos : null);

            if (!px1 || !px2) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            this._dpr = dpr;
            this._cw = cw;
            this._ch = ch;

            var style = this.style || {};
            var showLabel = style.showLabel !== false;
            var fontSize = style.fontSize || 10;

            // 1. Draw baseline preview P1 -> P2 -> P3
            ctx.save();
            ctx.strokeStyle = style.color || '#787b86';
            ctx.lineWidth = style.width || 1.5;

            if (!p2c) {
                // Placing 2nd point: draw dashed P1 -> cursor
                 // Placing 2nd point: draw dashed P1 -> cursor
                 ctx.beginPath();
                 ctx.setLineDash([4, 4]);
                 ctx.moveTo(px1.x, px1.y);
                 ctx.lineTo(px2.x, px2.y);
                 ctx.stroke();
                 ctx.restore();
                 this.drawHandle(ctx, px1, false);
                 return;
             }

             if (!px3) {
                 // Placing 3rd point: draw solid P1 -> P2 and dashed P2 -> cursor
                 ctx.beginPath();
                 ctx.moveTo(px1.x, px1.y);
                 ctx.lineTo(px2.x, px2.y);
                 ctx.stroke();

                 ctx.beginPath();
                 ctx.setLineDash([4, 4]);
                 ctx.moveTo(px2.x, px2.y);
                 ctx.lineTo(px2.x, px2.y); // just cursor placeholder
                 ctx.stroke();
                 ctx.restore();
                 this.drawHandle(ctx, px1, false);
                 this.drawHandle(ctx, px2, false);
                 return;
             }

            // Draw solid baseline connector lines between anchors
            ctx.beginPath();
            ctx.moveTo(px2.x, px2.y);
            ctx.lineTo(px1.x, px1.y);
            ctx.lineTo(px3.x, px3.y);
            ctx.stroke();
            ctx.restore();

            // 2. Compute vectors & directions
            var lowerVector = { x: px3.x - px1.x, y: px3.y - px1.y };
            var upperVector = { x: px2.x - px1.x, y: px2.y - px1.y };

            // Generate infinite ray endpoints
            var rayEnds = [];
            var rayDirs = [];
            for (var i = 0; i < PITCHFAN_LEVELS.length; i++) {
                var r = PITCHFAN_LEVELS[i].value;
                // direction = normalize(lowerVector + r * (upperVector - lowerVector))
                var dx = lowerVector.x + r * (upperVector.x - lowerVector.x);
                var dy = lowerVector.y + r * (upperVector.y - lowerVector.y);
                var len = Math.sqrt(dx * dx + dy * dy);
                if (len < 0.1) {
                    rayEnds.push(null);
                    rayDirs.push(null);
                    continue;
                }
                var ux = dx / len;
                var uy = dy / len;
                rayDirs.push({ ux: ux, uy: uy });

                var farPt = { x: px1.x + ux * 10000, y: px1.y + uy * 10000 };
                var clipped = GeometryUtils.rayCanvasIntersection(px1, farPt, cw, ch);
                rayEnds.push(clipped || farPt);
            }
            this._rayEnds = rayEnds;

            // 3. Draw fills between adjacent rays
            for (var i = 0; i < PITCHFAN_LEVELS.length - 1; i++) {
                var e1 = rayEnds[i];
                var e2 = rayEnds[i + 1];
                if (e1 && e2) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.moveTo(px1.x, px1.y);
                    ctx.lineTo(e1.x, e1.y);
                    ctx.lineTo(e2.x, e2.y);
                    ctx.closePath();
                    ctx.fillStyle = PITCHFAN_LEVELS[i].color;
                    ctx.globalAlpha = 0.12; // 10–15% opacity
                    ctx.fill();
                    ctx.restore();
                }
            }

            // 4. Draw infinite rays
            this._levelSegs = [];
            for (var i = 0; i < PITCHFAN_LEVELS.length; i++) {
                var lv = PITCHFAN_LEVELS[i];
                var endPt = rayEnds[i];
                if (!endPt) continue;

                ctx.save();
                ctx.beginPath();
                ctx.strokeStyle = lv.color;
                ctx.lineWidth = style.width || 1.5;
                ctx.globalAlpha = 0.85;
                ctx.moveTo(px1.x, px1.y);
                ctx.lineTo(endPt.x, endPt.y);
                ctx.stroke();
                ctx.restore();

                this._levelSegs.push({ p1: { x: px1.x, y: px1.y }, p2: endPt });
            }

            // 5. Draw Labels
            if (showLabel) {
                ctx.save();
                ctx.font = fontSize + 'px -apple-system, sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                for (var i = 0; i < PITCHFAN_LEVELS.length; i++) {
                    var lv = PITCHFAN_LEVELS[i];
                    var dir = rayDirs[i];
                    var endPt = rayEnds[i];
                    if (!dir || !endPt) continue;

                    // Offset 24px back from the end of the ray (at canvas edge) to prevent clutter near the apex
                    var lx = endPt.x - dir.ux * 24;
                    var ly = endPt.y - dir.uy * 24;
                    var ratioStr = lv.name;

                    var textWidth = ctx.measureText(ratioStr).width;
                    var bgPad = 2;

                    ctx.fillStyle = 'rgba(0,0,0,0.55)';
                    ctx.fillRect(lx - textWidth / 2 - bgPad, ly - fontSize / 2 - bgPad, textWidth + 2 * bgPad, fontSize + 2 * bgPad);
                    ctx.fillStyle = lv.color;
                    ctx.fillText(ratioStr, lx, ly);
                }
                ctx.restore();
            }

            // 6. Draw axis labels for active anchors
            if (this.showAxisLabels && (isSelected || (this.coords.length > 0 && this.coords.length < 3))) {
                var validPxs = [px1, px2];
                if (p3c) validPxs.push(px3);
                if (this.coords.length < 3 && this.currentPos) {
                    validPxs.push(this.currentPos);
                }
                this.drawAxisLabels(ctx, validPxs, chartState);
            }
        }
    }

    // =============================================================================
    // Gann Tools (Phase 3.5) — Shared Gann Geometry Engine
    // =============================================================================
    // Architecture: same as Fibonacci — GANN_DEFS → GannGeometryGenerator →
    // GeometryPrimitive[] → GeometryRenderer → canvas
    // =============================================================================

    // GannGeometryGenerator — generates line/fill/label primitives from anchors
    var GannGeometryGenerator = {
        generate: function(anchors, def, chartState, cw, ch, style) {
            if (!anchors || anchors.length < def.anchors) return [];
            var out = [];
            switch (def.geometry) {
                case 'gann_box': this._box(out, anchors, def, chartState, cw, ch, style); break;
                case 'gann_square': this._square(out, anchors, def, chartState, cw, ch, style); break;
                case 'gann_fan': this._fan(out, anchors, def, chartState, cw, ch, style); break;
            }
            return out;
        },

        // ---- Gann Box ----
        _box: function(out, anchors, def, chartState, cw, ch, style) {
            if (!anchors || anchors.length < 2) return;
            var p1c = anchors[0], p2c = anchors[1];

            var pp1 = chartState.coordToPixel ? chartState.coordToPixel(p1c) : null;
            var pp2 = chartState.coordToPixel ? chartState.coordToPixel(p2c) : null;
            if (!pp1 || !pp2) return;

            var x1 = Math.min(pp1.x, pp2.x), y1 = Math.min(pp1.y, pp2.y);
            var x2 = Math.max(pp1.x, pp2.x), y2 = Math.max(pp1.y, pp2.y);
            var boxW = x2 - x1, boxH = y2 - y1;
            if (boxW < 1 || boxH < 1) return;

            var ratios = def.ratios || [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1];
            var fillColors = def.fillColors || [];
            var gridColor = '#90a4ae';
            var lc = style.color || '#90a4ae';

            // 1. Fill regions between horizontal ratio lines
            for (var fi = 0; fi < fillColors.length; fi++) {
                var fc = fillColors[fi];
                var fy1 = y1 + boxH * fc.from;
                var fy2 = y1 + boxH * fc.to;
                out.push({
                    type: 'fill',
                    vertices: [{x:x1,y:fy1},{x:x2,y:fy1},{x:x2,y:fy2},{x:x1,y:fy2}],
                    style: {color: fc.color, opacity: fc.opacity}
                });
            }

            // 2. Outer rectangle border (2px dark gray)
            var borderColor = style.outerColor || '#555';
            var bw = 2;
            out.push({ type: 'line', start: {x:x1,y:y1}, end: {x:x2,y:y1}, style: {color: borderColor, width: bw, opacity: 1, lineDash: []} });
            out.push({ type: 'line', start: {x:x2,y:y1}, end: {x:x2,y:y2}, style: {color: borderColor, width: bw, opacity: 1, lineDash: []} });
            out.push({ type: 'line', start: {x:x2,y:y2}, end: {x:x1,y:y2}, style: {color: borderColor, width: bw, opacity: 1, lineDash: []} });
            out.push({ type: 'line', start: {x:x1,y:y2}, end: {x:x1,y:y1}, style: {color: borderColor, width: bw, opacity: 1, lineDash: []} });

            // 3. Grid lines at ratio boundaries (skip edges 0 and 1)
            for (var ri = 0; ri < ratios.length; ri++) {
                var r = ratios[ri];
                if (r === 0 || r === 1) continue;
                var gx = x1 + boxW * r;
                var gy = y1 + boxH * r;
                out.push({ type: 'line', start: {x:gx,y:y1}, end: {x:gx,y:y2}, style: {color: gridColor, width: 1, opacity: 0.4, lineDash: [2, 4]} });
                out.push({ type: 'line', start: {x:x1,y:gy}, end: {x:x2,y:gy}, style: {color: gridColor, width: 1, opacity: 0.4, lineDash: [2, 4]} });
            }

            // 4. Gann angle lines from P1 — computed in coordinate space
            var widthBars = p2c.logical - p1c.logical;
            var heightPrice = p2c.price - p1c.price;
            var gannRatios = def.gannRatios || [[1,1]];

            for (var gi = 0; gi < gannRatios.length; gi++) {
                var m = gannRatios[gi][0], n = gannRatios[gi][1];
                var dirLogical = widthBars * m;
                var dirPrice = heightPrice * n;
                if (Math.abs(dirLogical) < 0.0001 && Math.abs(dirPrice) < 0.0001) continue;

                var endCoord = { logical: p1c.logical + dirLogical, price: p1c.price + dirPrice };
                var endPx = chartState.coordToPixel ? chartState.coordToPixel(endCoord) : null;
                if (!endPx) continue;

                var rdx = endPx.x - pp1.x, rdy = endPx.y - pp1.y;
                if (Math.abs(rdx) < 0.5 && Math.abs(rdy) < 0.5) continue;

                // Extend ray far enough for _clipLineToRect
                var farX = pp1.x + rdx * 10000;
                var farY = pp1.y + rdy * 10000;
                var clip = _clipLineToRect(pp1.x, pp1.y, farX, farY, 0, 0, cw, ch);
                if (!clip) continue;

                // Clip again to box boundary
                var boxClip = _clipLineToRect(clip[0], clip[1], clip[2], clip[3], x1, y1, x2 - x1, y2 - y1);
                if (!boxClip) continue;

                var isMain = (m === 1 && n === 1);
                var angleColor = isMain ? lc : gridColor;
                out.push({
                    type: 'line',
                    start: {x:boxClip[0], y:boxClip[1]},
                    end: {x:boxClip[2], y:boxClip[3]},
                    style: {
                        color: angleColor,
                        width: isMain ? 1.5 : 1,
                        opacity: isMain ? 0.8 : 0.5,
                        lineDash: isMain ? [] : [2, 4]
                    }
                });
            }

            // 5. Labels on all four sides
            if (style.showLabel !== false) {
                var fontSize = style.fontSize || 9;
                for (var ri = 0; ri < ratios.length; ri++) {
                    var r = ratios[ri];
                    var rStr = String(r);
                    if (rStr.indexOf('.') >= 0) {
                        rStr = rStr.replace(/0+$/, '').replace(/\.$/, '');
                        if (rStr === '' || rStr === '-') rStr = '0';
                    }
                    var gx = x1 + boxW * r;
                    var gy = y1 + boxH * r;

                    // Top
                    out.push({ type: 'label', pos: {x: gx, y: y1 - 4}, text: rStr,
                        style: {color: gridColor, fontSize: fontSize, align: 'center', fontFamily: '-apple-system, Roboto, sans-serif'} });
                    // Bottom
                    out.push({ type: 'label', pos: {x: gx, y: y2 + 2}, text: rStr,
                        style: {color: gridColor, fontSize: fontSize, align: 'center', fontFamily: '-apple-system, Roboto, sans-serif'} });
                    // Left
                    out.push({ type: 'label', pos: {x: x1 - 6, y: gy}, text: rStr,
                        style: {color: gridColor, fontSize: fontSize, align: 'right', fontFamily: '-apple-system, Roboto, sans-serif'} });
                    // Right
                    out.push({ type: 'label', pos: {x: x2 + 6, y: gy}, text: rStr,
                        style: {color: gridColor, fontSize: fontSize, align: 'left', fontFamily: '-apple-system, Roboto, sans-serif'} });
                }
            }
        },

        // ---- Gann Square (equal width/height) ----
        _square: function(out, anchors, def, chartState, cw, ch, style) {
            var pp = [_px(chartState, anchors[0]), _px(chartState, anchors[1])];
            if (!pp[0] || !pp[1]) return;
            var cx = (pp[0].x + pp[1].x) / 2, cy = (pp[0].y + pp[1].y) / 2;
            var boxW = Math.abs(pp[1].x - pp[0].x), boxH = Math.abs(pp[1].y - pp[0].y);
            var size = Math.max(boxW, boxH);
            // Constrain to fixed aspect if enabled
            if (def.fixedAspect) {
                size = boxW; // Use width as the anchor dimension
            }
            var half = size / 2;
            var x1 = cx - half, y1 = cy - half, x2 = cx + half, y2 = cy + half;
            // Reuse box logic with forced square
            var squareDef = { anchors: def.anchors, geometry: 'gann_box', diagonals: def.diagonals, divisions: def.divisions };
            this._box(out, [{time:0,price:0},{time:0,price:0}], squareDef, chartState, cw, ch, style);
            // Overwrite the last out entries — instead let's just call _box with fake anchors for the square
            // Actually easier: just use the computed square coords directly.
            // Remove any primitives added by the fake _box call... hmm, cleaner to inline.
            // Let me just inline the box logic with our square coordinates.
            out.length = 0; // Clear and redo
            var lc = style.color || '#2196f3', lw = style.width || 1, lo = style.opacity != null ? style.opacity : 0.8;
            var rectColor = style.outerColor || lc;
            out.push({ type: 'line', start: {x:x1,y:y1}, end: {x:x2,y:y1}, style: {color: rectColor, width: lw, opacity: lo, lineDash: []} });
            out.push({ type: 'line', start: {x:x2,y:y1}, end: {x:x2,y:y2}, style: {color: rectColor, width: lw, opacity: lo, lineDash: []} });
            out.push({ type: 'line', start: {x:x2,y:y2}, end: {x:x1,y:y2}, style: {color: rectColor, width: lw, opacity: lo, lineDash: []} });
            out.push({ type: 'line', start: {x:x1,y:y2}, end: {x:x1,y:y1}, style: {color: rectColor, width: lw, opacity: lo, lineDash: []} });
            if (style.fillColor) {
                out.push({ type: 'fill', vertices: [{x:x1,y:y1},{x:x2,y:y1},{x:x2,y:y2},{x:x1,y:y2}], style: {color: style.fillColor, opacity: style.fillOpacity != null ? style.fillOpacity : 0.05} });
            }
            if (def.diagonals !== false) {
                var diagColor = style.diagonalColor || lc;
                out.push({ type: 'line', start: {x:x1,y:y1}, end: {x:x2,y:y2}, style: {color: diagColor, width: 0.5, opacity: 0.4, lineDash: []} });
                out.push({ type: 'line', start: {x:x2,y:y1}, end: {x:x1,y:y2}, style: {color: diagColor, width: 0.5, opacity: 0.4, lineDash: []} });
            }
            var divs = def.divisions || [0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875];
            var gridColor = style.gridColor || lc;
            for (var i = 0; i < divs.length; i++) {
                var r = divs[i];
                var gx = x1 + size * r, gy = y1 + size * r;
                out.push({ type: 'line', start: {x:gx,y:y1}, end: {x:gx,y:y2}, style: {color: gridColor, width: r === 0.5 ? 0.5 : 0.3, opacity: 0.3, lineDash: []} });
                out.push({ type: 'line', start: {x:x1,y:gy}, end: {x:x2,y:gy}, style: {color: gridColor, width: r === 0.5 ? 0.5 : 0.3, opacity: 0.3, lineDash: []} });
            }
        },

        // ---- Gann Fan ----
        _fan: function(out, anchors, def, chartState, cw, ch, style) {
            var pp = [_px(chartState, anchors[0]), _px(chartState, anchors[1])];
            if (!pp[0] || !pp[1]) return;
            var dx = pp[1].x - pp[0].x, dy = pp[1].y - pp[0].y;
            if (Math.abs(dx) < 1) dx = 1;
            var baseSlope = dy / dx;
            var lc = style.color || '#2196f3';

            var ratios = def.ratios || [1/8, 1/4, 1/3, 1/2, 1, 2, 3, 4, 8];
            for (var i = 0; i < ratios.length; i++) {
                var r = ratios[i];
                var slope = baseSlope * r;
                // Extend ray from p1 in direction of p2
                var ex = pp[1].x, ey = pp[1].y;
                // Extend far enough
                var far = cw + ch;
                if (dx > 0) {
                    ex = pp[0].x + far;
                    ey = pp[0].y + slope * far;
                } else {
                    ex = pp[0].x - far;
                    ey = pp[0].y - slope * far;
                }
                var clip = _clipLineToRect(pp[0].x, pp[0].y, ex, ey, 0, 0, cw, ch);
                if (!clip) continue;
                var ri = Math.round(r * 8);
                var op = 0.4 + (r === 1 ? 0.4 : 0.2);
                out.push({
                    type: 'line',
                    start: {x:clip[0], y:clip[1]},
                    end: {x:clip[2], y:clip[3]},
                    style: {color: style.ratioColors && style.ratioColors[i] || style.fanColors && style.fanColors[i] || lc, width: r === 1 ? (style.width || 1.5) : 0.7, opacity: op, lineDash: r === 1 ? [] : (style.lineDash || [])}
                });
                // Label near the end
                if (style.showLabel !== false) {
                    var labelText = def.ratioLabels ? def.ratioLabels[i] : (r >= 1 ? r+'×1' : '1×'+Math.round(1/r));
                    out.push({ type: 'label', pos: {x:clip[2]+4, y:clip[3]}, text: labelText, style: {color: style.ratioColors && style.ratioColors[i] || lc, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: style.fontSize || 9, align: 'left'} });
                }
            }
        }
    };

    // GANN_DEFS — per-tool configuration
    var GANN_DEFS = {
        gann_box: {
            anchors: 2, geometry: 'gann_box',
            ratios: [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1],
            gannRatios: [[1,8], [1,4], [1,3], [1,2], [1,1], [2,1], [3,1], [4,1], [8,1]],
            fillColors: [
                { from: 0, to: 0.25, color: '#ff9800', opacity: 0.12 },
                { from: 0.25, to: 0.382, color: '#00bcd4', opacity: 0.10 },
                { from: 0.382, to: 0.5, color: '#4caf50', opacity: 0.10 },
                { from: 0.5, to: 0.618, color: '#009688', opacity: 0.10 },
                { from: 0.618, to: 0.75, color: '#2196f3', opacity: 0.10 },
                { from: 0.75, to: 1, color: '#90a4ae', opacity: 0.08 },
            ]
        },
        gann_square: {
            anchors: 2, fixedAspect: false,
            arcRatios: [0.25, 0.382, 0.5, 0.618, 0.75, 1.0],
            arcColors: ['#FF9800','#00BCD4','#4CAF50','#009688','#2196F3','#2196F3'],
            arcWidths: [1, 1, 1, 1, 1, 1.5],
            gannRatios: [[1,8],[1,4],[1,3],[1,2],[1,1],[2,1],[3,1],[4,1],[8,1]],
            fanColors: ['#FF9800','#009688','#4CAF50','#00BCD4','#90A4AE','#2196F3','#9C27B0','#F44336','#D32F2F'],
            gridDivs: [0.25, 0.5, 0.75],
            fillColor: 'rgba(120,170,255,0.10)'
        },
        gann_square_fixed: {
            anchors: 2, fixedAspect: true,
            arcRatios: [0.25, 0.382, 0.5, 0.618, 0.75, 1.0],
            arcColors: ['#FF9800','#00BCD4','#4CAF50','#009688','#2196F3','#2196F3'],
            arcWidths: [1, 1, 1, 1, 1, 1.5],
            gannRatios: [[1,8],[1,4],[1,3],[1,2],[1,1],[2,1],[3,1],[4,1],[8,1]],
            fanColors: ['#FF9800','#009688','#4CAF50','#00BCD4','#90A4AE','#2196F3','#9C27B0','#F44336','#D32F2F'],
            gridDivs: [0.25, 0.5, 0.75],
            fillColor: 'rgba(120,170,255,0.10)'
        },
        gann_fan: {
            anchors: 2,
            gannRatios: [[1,8],[1,4],[1,3],[1,2],[1,1],[2,1],[3,1],[4,1],[8,1]],
            ratioLabels: ['1/8','1/4','1/3','1/2','1/1','2/1','3/1','4/1','8/1'],
            colors: ['#FF9800','#4CAF50','#4CAF50','#009688','#00BCD4','#2196F3','#9C27B0','#F44336','#D32F2F'],
            fillAlpha: 0.12
        }
    };

    // BaseGannDrawing — base class for all Gann tools
    class BaseGannDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this._cachedPrimitives = null;
            this.showAxisLabels = true;
        }

        getGannDef() { return null; }

        _invalidateCache() { this._cachedPrimitives = null; }

        onViewportChange() { this._invalidateCache(); }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            this._invalidateCache();
            return this.coords.length >= (this.getGannDef() ? this.getGannDef().anchors : 2);
        }

        _generatePrimitives(chartState) {
            var def = this.getGannDef();
            if (!def || !chartState || !chartState.coordToPixel) return null;
            var dpr = chartState.dpr || window.devicePixelRatio || 1;
            var cw = (chartState.canvasWidth || 800) / dpr;
            var ch = (chartState.canvasHeight || 600) / dpr;
            return GannGeometryGenerator.generate(this.coords, def, chartState, cw, ch, this.style);
        }

        _getPrimitives(chartState) {
            if (!this._cachedPrimitives) {
                this._cachedPrimitives = this._generatePrimitives(chartState);
            }
            return this._cachedPrimitives;
        }

        draw(ctx, chartState, isSelected) {
            if (!chartState || !chartState.coordToPixel) return;
            var primitives = this._getPrimitives(chartState);
            if (!primitives || !primitives.length) {
                // Preview for incomplete drawings
                if (this.currentPos && this.coords.length > 0 && this.coords.length < (this.getGannDef() ? this.getGannDef().anchors : 2)) {
                    this._drawPreview(ctx, chartState);
                }
                return;
            }
            GeometryRenderer.render(ctx, primitives);
            
            // Draw axis highlights for placed/in-progress points if selected or in-progress
            if (this.showAxisLabels && (isSelected || (this.coords.length > 0 && this.coords.length < (this.getGannDef() ? this.getGannDef().anchors : 2)))) {
                var pxs = this.getPixels(chartState);
                if (this.coords.length > 0 && this.coords.length < (this.getGannDef() ? this.getGannDef().anchors : 2) && this.currentPos) {
                    pxs.push(this.currentPos);
                }
                this.drawAxisLabels(ctx, pxs, chartState);
            }
        }

        _drawPreview(ctx, chartState) {
            // Generate full live box preview during creation
            if (this.coords.length > 0 && this.currentPos) {
                var c1 = this.coords[0];
                var c2 = chartState.pixelToCoord(this.currentPos.x, this.currentPos.y);
                if (c1 && c2) {
                    var dpr = chartState.dpr || window.devicePixelRatio || 1;
                    var cw = (chartState.canvasWidth || 800) / dpr;
                    var ch = (chartState.canvasHeight || 600) / dpr;
                    var previewCoords = [c1, c2];
                    var primitives = GannGeometryGenerator.generate(previewCoords, this.getGannDef(), chartState, cw, ch, this.style);
                    if (primitives && primitives.length) {
                        ctx.save();
                        // Render preview with dashed line style and 50% opacity
                        GeometryRenderer.render(ctx, primitives);
                        ctx.restore();
                    }
                }
            }
        }

        getAnchorPoints(pixels) {
            return pixels ? pixels.filter(Boolean) : [];
        }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            return [GeometryUtils.midpoint(pixels[0], pixels[1])];
        }

        getFillShape(pixels) {
            if (!this._cachedPrimitives || !pixels || pixels.length < 2) return null;
            var fillPrims = this._cachedPrimitives.filter(function(p) { return p.type === 'fill'; });
            if (fillPrims.length > 0) return fillPrims[0].vertices;
            // Bounding box of all lines
            var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (var i = 0; i < this._cachedPrimitives.length; i++) {
                var p = this._cachedPrimitives[i];
                if (p.type === 'line') {
                    if (p.start.x < minX) minX = p.start.x;
                    if (p.start.y < minY) minY = p.start.y;
                    if (p.end.x > maxX) maxX = p.end.x;
                    if (p.end.y > maxY) maxY = p.end.y;
                }
            }
            if (minX === Infinity) return null;
            return [{x:minX,y:minY},{x:maxX,y:minY},{x:maxX,y:maxY},{x:minX,y:maxY}];
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels) return;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
            }
            var mids = this.getMidpoints(pixels);
            for (var mi = 0; mi < mids.length; mi++) {
                if (mids[mi]) this._drawHandle(ctx, mids[mi], isSelected, true);
            }
        }

        _drawHandle(ctx, p, isSelected, isMidpoint) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            if (isMidpoint) {
                var s = 5;
                ctx.beginPath(); ctx.moveTo(p.x, p.y - s); ctx.lineTo(p.x + s, p.y);
                ctx.lineTo(p.x, p.y + s); ctx.lineTo(p.x - s, p.y); ctx.closePath();
            } else {
                ctx.beginPath(); ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            }
            ctx.fill();
            ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 1; ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                return chartState.pixelToCoord(pixel.x + dx, pixel.y + dy) || c;
            });
            this._invalidateCache();
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
            this._invalidateCache();
        }
    }

    // --- Tool Subclasses ---
    class GannBox extends BaseGannDrawing {
        getGannDef() { return GANN_DEFS.gann_box; }

        getAnchorPoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var x1 = Math.min(pixels[0].x, pixels[1].x);
            var y1 = Math.min(pixels[0].y, pixels[1].y);
            var x2 = Math.max(pixels[0].x, pixels[1].x);
            var y2 = Math.max(pixels[0].y, pixels[1].y);
            return [
                { x: x1, y: y1 },
                { x: x2, y: y1 },
                { x: x2, y: y2 },
                { x: x1, y: y2 }
            ];
        }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var x1 = Math.min(pixels[0].x, pixels[1].x);
            var y1 = Math.min(pixels[0].y, pixels[1].y);
            var x2 = Math.max(pixels[0].x, pixels[1].x);
            var y2 = Math.max(pixels[0].y, pixels[1].y);
            var cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
            return [
                { x: cx, y: y1 },
                { x: cx, y: y2 },
                { x: x1, y: cy },
                { x: x2, y: cy },
            ];
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || !chartState) return;

            if (draggedHandle.hitType === 'midpoint') {
                var idx = draggedHandle.handleIndex;
                var p1c = this.coords[0], p2c = this.coords[1];
                var mapper = (chartState.xToLogical || chartState.xToTime);
                var newLogical = mapper ? mapper(pixelX) : p2c.logical;

                if (idx === 2 || idx === 3) {
                    // Left/right — change logical only
                    var p1Px = chartState.coordToPixel(p1c);
                    var p2Px = chartState.coordToPixel(p2c);
                    if (!p1Px || !p2Px) return;
                    if (idx === 2) {
                        // Left midpoint → change anchor with lower pixel X
                        if (p1Px.x <= p2Px.x) {
                            this.coords[0] = { logical: newLogical, price: p1c.price };
                        } else {
                            this.coords[1] = { logical: newLogical, price: p2c.price };
                        }
                    } else {
                        // Right midpoint → change anchor with higher pixel X
                        if (p1Px.x >= p2Px.x) {
                            this.coords[0] = { logical: newLogical, price: p1c.price };
                        } else {
                            this.coords[1] = { logical: newLogical, price: p2c.price };
                        }
                    }
                } else {
                    // Top/bottom — change price only
                    var p1Px = chartState.coordToPixel(p1c);
                    var p2Px = chartState.coordToPixel(p2c);
                    if (!p1Px || !p2Px) return;
                    if (idx === 0) {
                        // Top midpoint → change anchor with lower pixel Y
                        if (p1Px.y <= p2Px.y) {
                            this.coords[0] = { logical: p1c.logical, price: newPrice };
                        } else {
                            this.coords[1] = { logical: p2c.logical, price: newPrice };
                        }
                    } else {
                        // Bottom midpoint → change anchor with higher pixel Y
                        if (p1Px.y >= p2Px.y) {
                            this.coords[0] = { logical: p1c.logical, price: newPrice };
                        } else {
                            this.coords[1] = { logical: p2c.logical, price: newPrice };
                        }
                    }
                }
                this._invalidateCache();
                return;
            }

            // For anchor handles (corners)
            var idx = draggedHandle.handleIndex;
            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            var logical = null, price = newPrice;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    logical = snapInfo.logical;
                    price = snapInfo.price;
                }
            }
            if (logical == null) {
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
                if (mapper) logical = mapper(pixelX);
            }
            if (logical == null) return;

            // Anchor indices mapping based on getAnchorPoints:
            // 0: p1, 1: {p1.x, p2.y}, 2: p2, 3: {p2.x, p1.y}
            if (idx === 0) { // Top/Left (p1)
                this.coords[0] = { logical: logical, price: price };
            } else if (idx === 1) { // Bottom/Left
                this.coords[0].logical = logical;
                this.coords[1].price = price;
            } else if (idx === 2) { // Bottom/Right (p2)
                this.coords[1] = { logical: logical, price: price };
            } else if (idx === 3) { // Top/Right
                this.coords[1].logical = logical;
                this.coords[0].price = price;
            }
            this._invalidateCache();
        }
    }
    class GannSquare extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 2;
            this.showAxisLabels = false;
        }

        _minAnchors() { return 2; }

        getAnchorPoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var sq = this._computeSquare(pixels);
            if (!sq) return pixels;
            return [
                { x: sq.x1, y: sq.y1 },
                { x: sq.x2, y: sq.y1 },
                { x: sq.x2, y: sq.y2 },
                { x: sq.x1, y: sq.y2 }
            ];
        }

        getMidpoints(pixels) { return []; }

        getEdgeSegments(pixels, chartState) {
            return this._edgeSegs || [];
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var sq = this._computeSquare(pixels);
            if (!sq) return null;
            return [{x:sq.x1,y:sq.y1},{x:sq.x2,y:sq.y1},{x:sq.x2,y:sq.y2},{x:sq.x1,y:sq.y2}];
        }

        _computeSquare(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var def = this._getDef();
            var x1 = Math.min(pixels[0].x, pixels[1].x);
            var y1 = Math.min(pixels[0].y, pixels[1].y);
            var bw = Math.abs(pixels[1].x - pixels[0].x);
            var bh = Math.abs(pixels[1].y - pixels[0].y);
            var size;
            if (def && def.fixedAspect) {
                size = bw;
            } else {
                size = Math.max(bw, bh);
            }
            if (size < 2) return null;
            return { x1: x1, y1: y1, x2: x1 + size, y2: y1 + size, size: size };
        }

        _getDef() { return GANN_DEFS.gann_square; }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                return chartState.pixelToCoord(pixel.x + dx, pixel.y + dy) || c;
            });
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            var logical = null, price = newPrice;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    logical = snapInfo.logical;
                    price = snapInfo.price;
                }
            }
            if (logical == null) {
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
                if (mapper) logical = mapper(pixelX);
            }
            if (logical == null) return;

            // Anchor indices mapping based on getAnchorPoints:
            // 0: TL, 1: TR, 2: BR, 3: BL
            if (idx === 0) { // Top/Left (p1)
                this.coords[0] = { logical: logical, price: price };
            } else if (idx === 1) { // Top/Right
                this.coords[1].logical = logical;
                this.coords[0].price = price;
            } else if (idx === 2) { // Bottom/Right (p2)
                this.coords[1] = { logical: logical, price: price };
            } else if (idx === 3) { // Bottom/Left
                this.coords[0].logical = logical;
                this.coords[1].price = price;
            }
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState || !chartState.coordToPixel) return;
            var pixels = this.getPixels(chartState);
            var isPreview = false;
            if (pixels.length < 2) {
                if (this.currentPos) {
                    pixels = [pixels[0], this.currentPos];
                    isPreview = true;
                } else {
                    return;
                }
            }
            var sq = this._computeSquare(pixels);
            if (!sq) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            var style = this.style || {};
            var def = this._getDef();
            if (!def) return;

            var x1 = sq.x1, y1 = sq.y1, x2 = sq.x2, y2 = sq.y2, size = sq.size;
            var bl = { x: x1, y: y2 }; // bottom-left (arc origin)
            var self = this;
            this._edgeSegs = [];

            // -- 1. Background fill --
            ctx.save();
            ctx.fillStyle = def.fillColor || 'rgba(120,170,255,0.10)';
            ctx.fillRect(x1, y1, size, size);
            ctx.restore();

            // -- 2. Grid lines (4x4: 0%, 25%, 50%, 75%, 100%) --
            var gridDivs = def.gridDivs || [0.25, 0.5, 0.75];
            var gridColor = style.gridColor || '#90a4ae';
            ctx.save();
            ctx.strokeStyle = gridColor;
            ctx.lineWidth = 0.5;
            ctx.globalAlpha = 0.4;
            // Horizontal grid lines
            for (var gi = 0; gi < gridDivs.length; gi++) {
                var gy = y1 + size * gridDivs[gi];
                ctx.beginPath(); ctx.moveTo(x1, gy); ctx.lineTo(x2, gy); ctx.stroke();
                self._edgeSegs.push({ p1: {x:x1,y:gy}, p2: {x:x2,y:gy} });
            }
            // Vertical grid lines
            for (var gi = 0; gi < gridDivs.length; gi++) {
                var gx = x1 + size * gridDivs[gi];
                ctx.beginPath(); ctx.moveTo(gx, y1); ctx.lineTo(gx, y2); ctx.stroke();
                self._edgeSegs.push({ p1: {x:gx,y:y1}, p2: {x:gx,y:y2} });
            }
            ctx.restore();

            // -- 3. Gann fan lines from bottom-left, clipped to square --
            var gannRatios = def.gannRatios || [[1,8],[1,4],[1,3],[1,2],[1,1],[2,1],[3,1],[4,1],[8,1]];
            var fanColors = def.fanColors || ['#FF9800','#009688','#4CAF50','#00BCD4','#90A4AE','#2196F3','#9C27B0','#F44336','#D32F2F'];
            ctx.save();
            for (var fi = 0; fi < gannRatios.length; fi++) {
                var m = gannRatios[fi][0], n = gannRatios[fi][1];
                // Ray from bottom-left (x1, y2) toward square interior
                var endX, endY;
                if (n >= m) {
                    // Steeper: hits top edge
                    endX = x1 + size * m / n;
                    endY = y1;
                } else {
                    // Shallower: hits right edge
                    endX = x2;
                    endY = y2 - size * n / m;
                }
                var clip = _clipLineToRect(bl.x, bl.y, endX, endY, x1, y1, size, size);
                if (clip) {
                    ctx.beginPath();
                    ctx.strokeStyle = fanColors[fi] || '#90a4ae';
                    ctx.lineWidth = fi === 4 ? 1 : 0.7; // 1×1 slightly thicker
                    ctx.globalAlpha = fi === 4 ? 0.6 : 0.45;
                    ctx.moveTo(clip[0], clip[1]);
                    ctx.lineTo(clip[2], clip[3]);
                    ctx.stroke();
                    self._edgeSegs.push({ p1: {x:clip[0],y:clip[1]}, p2: {x:clip[2],y:clip[3]} });
                }
            }
            ctx.restore();

            // -- 4. Diagonals --
            ctx.save();
            var diagColor = style.diagonalColor || '#90a4ae';
            ctx.strokeStyle = diagColor;
            ctx.lineWidth = 0.5;
            ctx.globalAlpha = 0.35;
            // Main diagonal: bottom-left → top-right
            ctx.beginPath(); ctx.moveTo(x1, y2); ctx.lineTo(x2, y1); ctx.stroke();
            self._edgeSegs.push({ p1: {x:x1,y:y2}, p2: {x:x2,y:y1} });
            // Counter diagonal: top-left → bottom-right
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
            self._edgeSegs.push({ p1: {x:x1,y:y1}, p2: {x:x2,y:y2} });
            ctx.restore();

            // -- 5. Quarter-circle arcs from bottom-left --
            var arcRatios = def.arcRatios || [0.25, 0.382, 0.5, 0.618, 0.75, 1.0];
            var arcColors = def.arcColors || ['#FF9800','#00BCD4','#4CAF50','#009688','#2196F3','#2196F3'];
            var arcWidths = def.arcWidths || [1, 1, 1, 1, 1, 1.5];
            ctx.save();
            for (var ai = 0; ai < arcRatios.length; ai++) {
                var r = size * arcRatios[ai];
                if (r < 2) continue;
                ctx.beginPath();
                ctx.arc(bl.x, bl.y, r, 0, -Math.PI / 2, true);
                ctx.strokeStyle = arcColors[ai];
                ctx.lineWidth = arcWidths[ai];
                ctx.globalAlpha = 0.55;
                ctx.stroke();
                // Approximate arc as line segments for hit testing
                var segs = 12;
                for (var sj = 0; sj < segs; sj++) {
                    var a1 = -Math.PI / 2 * (sj / segs);
                    var a2 = -Math.PI / 2 * ((sj + 1) / segs);
                    self._edgeSegs.push({
                        p1: { x: bl.x + r * Math.cos(a1), y: bl.y + r * Math.sin(a1) },
                        p2: { x: bl.x + r * Math.cos(a2), y: bl.y + r * Math.sin(a2) }
                    });
                }
            }
            ctx.restore();

            // -- 6. Outer border --
            ctx.save();
            var borderColor = style.color || '#555';
            ctx.strokeStyle = borderColor;
            ctx.lineWidth = 2;
            ctx.globalAlpha = 1;
            ctx.strokeRect(x1, y1, size, size);
            // Border edges for hit testing (overwrite arc/grid/fan segments on border)
            self._edgeSegs.push({ p1: {x:x1,y:y1}, p2: {x:x2,y:y1} });
            self._edgeSegs.push({ p1: {x:x2,y:y1}, p2: {x:x2,y:y2} });
            self._edgeSegs.push({ p1: {x:x2,y:y2}, p2: {x:x1,y:y2} });
            self._edgeSegs.push({ p1: {x:x1,y:y2}, p2: {x:x1,y:y1} });
            ctx.restore();

            // -- 7. Labels --
            if (style.showLabel !== false) {
                ctx.save();
                ctx.fillStyle = '#787b86';
                ctx.globalAlpha = 0.7;
                ctx.font = (style.fontSize || 9) + 'px ' + (style.fontFamily || '-apple-system, Roboto, sans-serif');
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                // Top edge: 0 (left), 1 (right)
                ctx.fillText('0', x1, y1 - 14);
                ctx.fillText('1', x2, y1 - 14);
                // Left edge: 1 (bottom), 0 (top)
                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                ctx.fillText('1', x1 - 8, y2);
                ctx.fillText('0', x1 - 8, y1);
                ctx.restore();
            }
        }
    }
    class GannSquareFixed extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 2;
            this.showAxisLabels = false;
        }

        _minAnchors() { return 2; }

        _getDef() { return GANN_DEFS.gann_square_fixed; }

        getAnchorPoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var sq = this._computeSquare(pixels);
            if (!sq) return [];
            // Origin at bottom-left, corner at top-right
            return [
                {x: sq.x1, y: sq.y2},
                {x: sq.x2, y: sq.y1}
            ];
        }

        getMidpoints(pixels) { return []; }

        getEdgeSegments(pixels, chartState) {
            return this._edgeSegs || [];
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var sq = this._computeSquare(pixels);
            if (!sq) return null;
            return [{x:sq.x1,y:sq.y1},{x:sq.x2,y:sq.y1},{x:sq.x2,y:sq.y2},{x:sq.x1,y:sq.y2}];
        }

        _computeSquare(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var ox = pixels[0].x, oy = pixels[0].y;
            var sx = pixels[1].x, sy = pixels[1].y;
            var side = Math.max(Math.abs(sx - ox), Math.abs(sy - oy));
            if (side < 2) return null;
            var left = ox;
            var right = ox + side;
            var top = oy - side;
            var bottom = oy;
            return { x1: left, y1: top, x2: right, y2: bottom, size: side };
        }

        drawHandles(ctx, pixels, isSelected) {
            var drawPx = pixels ? pixels.slice() : [];
            if (drawPx.length < 2 && this.currentPos) drawPx.push(this.currentPos);
            var sq = this._computeSquare(drawPx);
            if (!sq) return;
            var origin = {x: sq.x1, y: sq.y2};
            var corner = {x: sq.x2, y: sq.y1};
            this._drawHandle(ctx, origin, isSelected);
            if (pixels && pixels.length >= 2) this._drawHandle(ctx, corner, isSelected);
        }

        _drawHandle(ctx, p, isSelected) {
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                return chartState.pixelToCoord(pixel.x + dx, pixel.y + dy) || c;
            });
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;
            if (idx < 0 || idx >= this.coords.length) return;

            if (idx === 0) {
                // Origin handle: translate entire square
                var oldP1 = this.coords[0];
                var newLogical = oldP1.logical;
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime));
                if (mapper) {
                    var nl = mapper(pixelX);
                    if (nl != null) newLogical = nl;
                }
                var dLog = newLogical - oldP1.logical;
                var dPrice = newPrice - oldP1.price;
                for (var i = 0; i < this.coords.length; i++) {
                    this.coords[i] = {
                        logical: this.coords[i].logical + dLog,
                        price: this.coords[i].price + dPrice
                    };
                }
            } else {
                // Corner handle: recompute side and snap P2 to corner
                if (!chartState || !chartState.coordToPixel || !chartState.pixelToCoord || !chartState.priceToY) return;
                var originPx = chartState.coordToPixel(this.coords[0]);
                if (!originPx) return;
                var mouseY = chartState.priceToY(newPrice);
                if (mouseY == null) return;
                var side = Math.max(Math.abs(pixelX - originPx.x), Math.abs(mouseY - originPx.y));
                if (side < 2) return;
                var cornerCoord = chartState.pixelToCoord(originPx.x + side, originPx.y - side);
                if (cornerCoord) this.coords[1] = cornerCoord;
            }
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState || !chartState.coordToPixel) return;
            var pixels = this.getPixels(chartState);
            var isPreview = false;
            if (pixels.length < 2) {
                if (this.currentPos) {
                    pixels = [pixels[0], this.currentPos];
                    isPreview = true;
                } else {
                    return;
                }
            }
            var sq = this._computeSquare(pixels);
            if (!sq) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;
            var style = this.style || {};
            var def = this._getDef();
            if (!def) return;

            var x1 = sq.x1, y1 = sq.y1, x2 = sq.x2, y2 = sq.y2, size = sq.size;
            var bl = { x: x1, y: y2 };
            var self = this;
            this._edgeSegs = [];

            // -- 1. Background fill --
            ctx.save();
            ctx.fillStyle = def.fillColor || 'rgba(120,170,255,0.10)';
            ctx.fillRect(x1, y1, size, size);
            ctx.restore();

            // -- 2. Grid lines (4x4: 0%, 25%, 50%, 75%, 100%) --
            var gridDivs = def.gridDivs || [0.25, 0.5, 0.75];
            var gridColor = style.gridColor || '#90a4ae';
            ctx.save();
            ctx.strokeStyle = gridColor;
            ctx.lineWidth = 0.5;
            ctx.globalAlpha = 0.4;
            for (var gi = 0; gi < gridDivs.length; gi++) {
                var gy = y1 + size * gridDivs[gi];
                ctx.beginPath(); ctx.moveTo(x1, gy); ctx.lineTo(x2, gy); ctx.stroke();
                self._edgeSegs.push({ p1: {x:x1,y:gy}, p2: {x:x2,y:gy} });
            }
            for (var gi = 0; gi < gridDivs.length; gi++) {
                var gx = x1 + size * gridDivs[gi];
                ctx.beginPath(); ctx.moveTo(gx, y1); ctx.lineTo(gx, y2); ctx.stroke();
                self._edgeSegs.push({ p1: {x:gx,y:y1}, p2: {x:gx,y:y2} });
            }
            ctx.restore();

            // -- 3. Gann fan lines from bottom-left, clipped to square boundary --
            var gannRatios = def.gannRatios || [[1,8],[1,4],[1,3],[1,2],[1,1],[2,1],[3,1],[4,1],[8,1]];
            var fanColors = def.fanColors || ['#FF9800','#009688','#4CAF50','#00BCD4','#90A4AE','#2196F3','#9C27B0','#F44336','#D32F2F'];
            ctx.save();
            for (var fi = 0; fi < gannRatios.length; fi++) {
                var m = gannRatios[fi][0], n = gannRatios[fi][1];
                var endX, endY;
                if (Math.abs(n) >= Math.abs(m)) {
                    endX = x1 + size * m / n;
                    endY = y1;
                } else {
                    endX = x2;
                    endY = y2 - size * n / m;
                }
                var clip = _clipLineToRect(bl.x, bl.y, endX, endY, x1, y1, size, size);
                if (clip) {
                    ctx.beginPath();
                    ctx.strokeStyle = fanColors[fi] || '#90a4ae';
                    ctx.lineWidth = fi === 4 ? 1 : 0.7;
                    ctx.globalAlpha = fi === 4 ? 0.6 : 0.45;
                    ctx.moveTo(clip[0], clip[1]);
                    ctx.lineTo(clip[2], clip[3]);
                    ctx.stroke();
                    self._edgeSegs.push({ p1: {x:clip[0],y:clip[1]}, p2: {x:clip[2],y:clip[3]} });
                }
            }
            ctx.restore();

            // -- 4. Diagonals --
            ctx.save();
            var diagColor = style.diagonalColor || '#90a4ae';
            ctx.strokeStyle = diagColor;
            ctx.lineWidth = 0.5;
            ctx.globalAlpha = 0.35;
            ctx.beginPath(); ctx.moveTo(x1, y2); ctx.lineTo(x2, y1); ctx.stroke();
            self._edgeSegs.push({ p1: {x:x1,y:y2}, p2: {x:x2,y:y1} });
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
            self._edgeSegs.push({ p1: {x:x1,y:y1}, p2: {x:x2,y:y2} });
            ctx.restore();

            // -- 5. Quarter-circle arcs from bottom-left --
            var arcRatios = def.arcRatios || [0.25, 0.382, 0.5, 0.618, 0.75, 1.0];
            var arcColors = def.arcColors || ['#FF9800','#00BCD4','#4CAF50','#009688','#2196F3','#2196F3'];
            var arcWidths = def.arcWidths || [1, 1, 1, 1, 1, 1.5];
            ctx.save();
            for (var ai = 0; ai < arcRatios.length; ai++) {
                var r = size * arcRatios[ai];
                if (r < 2) continue;
                ctx.beginPath();
                ctx.arc(bl.x, bl.y, r, 0, -Math.PI / 2, true);
                ctx.strokeStyle = arcColors[ai];
                ctx.lineWidth = arcWidths[ai];
                ctx.globalAlpha = 0.55;
                ctx.stroke();
                var segs = 12;
                for (var sj = 0; sj < segs; sj++) {
                    var a1 = -Math.PI / 2 * (sj / segs);
                    var a2 = -Math.PI / 2 * ((sj + 1) / segs);
                    self._edgeSegs.push({
                        p1: { x: bl.x + r * Math.cos(a1), y: bl.y + r * Math.sin(a1) },
                        p2: { x: bl.x + r * Math.cos(a2), y: bl.y + r * Math.sin(a2) }
                    });
                }
            }
            ctx.restore();

            // -- 6. Outer border --
            ctx.save();
            var borderColor = style.color || '#555';
            ctx.strokeStyle = borderColor;
            ctx.lineWidth = 2;
            ctx.globalAlpha = 1;
            ctx.strokeRect(x1, y1, size, size);
            self._edgeSegs.push({ p1: {x:x1,y:y1}, p2: {x:x2,y:y1} });
            self._edgeSegs.push({ p1: {x:x2,y:y1}, p2: {x:x2,y:y2} });
            self._edgeSegs.push({ p1: {x:x2,y:y2}, p2: {x:x1,y:y2} });
            self._edgeSegs.push({ p1: {x:x1,y:y2}, p2: {x:x1,y:y1} });
            ctx.restore();

            // -- 7. Labels: Top 0 / Left 1 / Bottom 1 / Right 0 --
            if (style.showLabel !== false) {
                ctx.save();
                ctx.fillStyle = '#787b86';
                ctx.globalAlpha = 0.7;
                ctx.font = (style.fontSize || 9) + 'px ' + (style.fontFamily || '-apple-system, Roboto, sans-serif');
                // Top edge, left end: "0"
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillText('0', x1, y1 - 14);
                // Left edge, bottom end: "1"
                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                ctx.fillText('1', x1 - 8, y2);
                // Bottom edge, right end: "1"
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillText('1', x2, y2 + 10);
                // Right edge, top end: "0"
                ctx.textAlign = 'left';
                ctx.textBaseline = 'middle';
                ctx.fillText('0', x2 + 8, y1);
                ctx.restore();
            }
        }
    }
    class GannFan extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.pointCount = 2;
            this.showAxisLabels = true;
        }

        _minAnchors() { return 2; }

        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            if (this._clipPoints && this._clipPoints.length >= 2) {
                var pts = [{x: pixels[0].x, y: pixels[0].y}];
                for (var i = 0; i < this._clipPoints.length; i++) {
                    if (this._clipPoints[i]) pts.push({x: this._clipPoints[i].x, y: this._clipPoints[i].y});
                }
                if (pts.length >= 3) return pts;
            }
            var x1 = Math.min(pixels[0].x, pixels[1].x);
            var y1 = Math.min(pixels[0].y, pixels[1].y);
            var x2 = Math.max(pixels[0].x, pixels[1].x);
            var y2 = Math.max(pixels[0].y, pixels[1].y);
            return [{x:x1,y:y1},{x:x2,y:y1},{x:x2,y:y2},{x:x1,y:y2}];
        }

        getEdgeSegments(pixels, chartState) {
            return this._fanLines || [];
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels) return;
            if (pixels[0]) this._drawHandle(ctx, pixels[0], isSelected);
            if (pixels[1]) this._drawHandle(ctx, pixels[1], isSelected);
        }

        _drawHandle(ctx, p, isSelected) {
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.fill();
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                return chartState.pixelToCoord(pixel.x + dx, pixel.y + dy) || c;
            });
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!this.coords || this.coords.length < 1 || !chartState || !chartState.coordToPixel) return;
            var p1c = this.coords[0];
            var px1 = chartState.coordToPixel(p1c);
            if (!px1) return;

            var dpr = window.devicePixelRatio || 1;
            var cw = ctx.canvas.width / dpr;
            var ch = ctx.canvas.height / dpr;

            var def = GANN_DEFS.gann_fan;
            var gannRatios = def.gannRatios;
            var colors = def.colors;
            var labels = def.ratioLabels;
            var fillAlpha = def.fillAlpha;
            var style = this.style || {};
            var showLabel = style.showLabel !== false;

            if (this.coords.length < 2) {
                var curPx = this.currentPos;
                if (curPx) {
                    ctx.save();
                    ctx.strokeStyle = style.color || '#787b86';
                    ctx.lineWidth = style.width || 1.5;
                    ctx.setLineDash([4, 4]);
                    ctx.globalAlpha = 0.6;
                    ctx.beginPath();
                    ctx.moveTo(px1.x, px1.y);
                    ctx.lineTo(curPx.x, curPx.y);
                    ctx.stroke();
                    ctx.restore();
                }
                return;
            }

            var p2c = this.coords[1];
            var px2 = chartState.coordToPixel(p2c);
            if (!px2) return;

            var dxBars = p2c.logical - p1c.logical;
            var dyPrice = p2c.price - p1c.price;

            if (Math.abs(dxBars) < 0.0001) dxBars = dxBars < 0 ? -0.0001 : 0.0001;
            if (Math.abs(dyPrice) < 0.0001) dyPrice = dyPrice < 0 ? -0.0001 : 0.0001;

            var rays = [];
            this._fanLines = [];
            this._clipPoints = [];

            for (var i = 0; i < gannRatios.length; i++) {
                var m = gannRatios[i][0], n = gannRatios[i][1];
                var timeStep = dxBars * m;
                var priceStep = dyPrice * n;

                var dirCoord = { logical: p1c.logical + timeStep, price: p1c.price + priceStep };
                var dirPx = chartState.coordToPixel(dirCoord);
                if (!dirPx) { rays.push(null); this._clipPoints.push(null); continue; }

                var ddx = dirPx.x - px1.x;
                var ddy = dirPx.y - px1.y;
                var dist = Math.sqrt(ddx * ddx + ddy * ddy);
                var farPx = dirPx;
                if (dist < 50) {
                    var scale = 50 / (dist || 1);
                    farPx = { x: px1.x + ddx * scale, y: px1.y + ddy * scale };
                }

                var end = GeometryUtils.rayCanvasIntersection(px1, farPx, cw, ch);
                if (end) {
                    rays.push({ p1: px1, p2: end, color: colors[i], label: labels[i], ratioIdx: i });
                    this._fanLines.push({ p1: px1, p2: end });
                    this._clipPoints.push(end);
                } else {
                    rays.push(null);
                    this._clipPoints.push(null);
                }
            }

            // Draw fills between adjacent rays
            for (var fi = 0; fi < rays.length - 1; fi++) {
                var r1 = rays[fi], r2 = rays[fi + 1];
                if (r1 && r2) {
                    ctx.save();
                    ctx.beginPath();
                    ctx.moveTo(px1.x, px1.y);
                    ctx.lineTo(r1.p2.x, r1.p2.y);
                    ctx.lineTo(r2.p2.x, r2.p2.y);
                    ctx.closePath();
                    ctx.fillStyle = r1.color;
                    ctx.globalAlpha = fillAlpha;
                    ctx.fill();
                    ctx.restore();
                }
            }

            // Draw rays
            ctx.save();
            for (var i = 0; i < rays.length; i++) {
                var ray = rays[i];
                if (!ray) continue;
                ctx.beginPath();
                ctx.strokeStyle = ray.color;
                ctx.lineWidth = i === 4 ? (style.width || 1.5) : 0.8;
                ctx.globalAlpha = i === 4 ? 0.9 : 0.7;
                ctx.moveTo(ray.p1.x, ray.p1.y);
                ctx.lineTo(ray.p2.x, ray.p2.y);
                ctx.stroke();
            }
            ctx.restore();

            // Draw labels
            if (showLabel) {
                ctx.save();
                for (var i = 0; i < rays.length; i++) {
                    var ray = rays[i];
                    if (!ray) continue;
                    var endP = ray.p2;
                    var dx = endP.x - px1.x;
                    var dy = endP.y - px1.y;
                    var len = Math.sqrt(dx * dx + dy * dy);
                    if (len < 1) continue;
                    var ux = dx / len, uy = dy / len;
                    var offset = 14;
                    var lx = endP.x + ux * offset;
                    var ly = endP.y + uy * offset;
                    ctx.fillStyle = ray.color;
                    ctx.globalAlpha = 0.9;
                    ctx.font = (style.fontSize || 10) + 'px ' + (style.fontFamily || '-apple-system, Roboto, sans-serif');
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(ray.label, lx, ly);
                }
                ctx.restore();
            }

            if (this.showAxisLabels && isSelected) {
                this.drawAxisLabels(ctx, [px1, px2], chartState);
            }
        }
    }

    // =============================================================================
    // Elliott Wave Tools (Phase 3.6) — Shared Wave Geometry Engine
    // =============================================================================
    // Architecture: WAVE_DEFS → WaveGeometryGenerator → coordLine/coordLabel
    //               → GeometryRenderer (coord→pixel via chartState) → canvas
    // =============================================================================

    // WAVE_DEFS — per-tool configuration
    var WAVE_DEFS = {
        impulse: {
            anchors: 6, geometry: 'wave',
            anchorLabels: ['', '1', '2', '3', '4', '5'],
            colors: ['#26a69a','#ef5350','#26a69a','#ef5350','#26a69a']
        },
        corrective: {
            anchors: 4, geometry: 'wave',
            anchorLabels: ['', 'A', 'B', 'C'],
            colors: ['#ef5350','#ff9800','#ef5350']
        },
        triangle: {
            anchors: 6, geometry: 'wave',
            anchorLabels: ['', 'A', 'B', 'C', 'D', 'E'],
            colors: ['#2196f3','#4caf50','#ff9800','#9c27b0','#f44336']
        },
        double_combo: {
            anchors: 5, geometry: 'wave',
            anchorLabels: ['', 'W', 'X', 'Y', ''],
            colors: ['#ff9800','#2196f3','#ff9800','#787b86']
        },
        triple_combo: {
            anchors: 7, geometry: 'wave',
            anchorLabels: ['', 'W', 'X', 'Y', 'X', 'Z', ''],
            colors: ['#ff9800','#2196f3','#ff9800','#2196f3','#ff9800','#787b86']
        },
        flat: {
            anchors: 4, geometry: 'wave',
            anchorLabels: ['', 'A', 'B', 'C'],
            colors: ['#ef5350','#4caf50','#ef5350']
        },
        zigzag: {
            anchors: 4, geometry: 'wave',
            anchorLabels: ['', 'A', 'B', 'C'],
            colors: ['#f44336','#ff9800','#f44336']
        },
        combination: {
            anchors: 2, geometry: 'wave',
            anchorLabels: ['', ''],
            colors: ['#787b86']
        }
    };

    // WaveGeometryGenerator — coordinate-space primitive generator
    // Input: anchor coords [{time,price}], def, style
    // Output: [coordLine, coordLabel, ...]
    var WaveGeometryGenerator = {
        generate: function(anchors, def, chartState, cw, ch, style) {
            var out = [];
            if (!anchors || anchors.length < 2) return out;
            var lc = style.color || '#787b86';
            var lw = style.width || 2;
            var lo = style.opacity != null ? style.opacity : 0.8;

            // Connect each adjacent pair
            var maxSegs = Math.min(anchors.length - 1, def.colors ? def.colors.length : anchors.length - 1);
            for (var i = 0; i < maxSegs; i++) {
                var sc = (def.colors && def.colors[i]) || lc;
                out.push({
                    type: 'coordLine',
                    start: anchors[i],
                    end: anchors[i + 1],
                    style: { color: sc, width: lw, opacity: lo, lineDash: [] }
                });
            }

            // Labels at each anchor (coordinate-space)
            if (style.showLabel !== false && def.anchorLabels) {
                for (var i = 0; i < def.anchorLabels.length && i < anchors.length; i++) {
                    var lbl = def.anchorLabels[i];
                    if (!lbl && lbl !== '0') continue; // skip empty labels but allow "0"
                    var sc = (def.colors && def.colors[i]) || lc;
                    if (i > 0 && def.colors && def.colors[i - 1]) sc = def.colors[i - 1];
                    out.push({
                        type: 'coordLabel',
                        coord: anchors[i],
                        text: lbl,
                        offsetX: style.labelOffsetX != null ? style.labelOffsetX : 8,
                        offsetY: style.labelOffsetY != null ? style.labelOffsetY : 8,
                        style: { color: sc, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif', fontSize: (style.fontSize || 12) + 2, align: 'left' }
                    });
                }
            }

            return out;
        }
    };

    // BaseWaveDrawing — base class for all Elliott Wave tools
    class BaseWaveDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this._cachedPrimitives = null;
        }

        getWaveDef() { return null; }

        _invalidateCache() { this._cachedPrimitives = null; }

        onViewportChange() { this._invalidateCache(); }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            this._invalidateCache();
            return this.coords.length >= (this.getWaveDef() ? this.getWaveDef().anchors : 2);
        }

        _generatePrimitives(chartState) {
            var def = this.getWaveDef();
            if (!def || !chartState || !chartState.coordToPixel) return null;
            var dpr = chartState.dpr || window.devicePixelRatio || 1;
            var cw = (chartState.canvasWidth || 800) / dpr;
            var ch = (chartState.canvasHeight || 600) / dpr;
            return WaveGeometryGenerator.generate(this.coords, def, chartState, cw, ch, this.style);
        }

        _getPrimitives(chartState) {
            if (!this._cachedPrimitives) {
                this._cachedPrimitives = this._generatePrimitives(chartState);
            }
            return this._cachedPrimitives;
        }

        draw(ctx, chartState, isSelected) {
            if (!chartState || !chartState.coordToPixel) return;
            var primitives = this._getPrimitives(chartState);
            if (!primitives || !primitives.length) {
                if (this.currentPos && this.coords.length > 0 && this.coords.length < (this.getWaveDef() ? this.getWaveDef().anchors : 2)) {
                    this._drawPreview(ctx, chartState);
                }
                return;
            }
            // Pass chartState for coordLine/coordLabel conversion
            GeometryRenderer.render(ctx, primitives, chartState);
        }

        _drawPreview(ctx, chartState) {
            var pixels = this.getPixels(chartState);
            if (!pixels || pixels.length === 0) return;
            var lastPx = pixels[pixels.length - 1];
            if (!lastPx) return;
            var curPx = this.currentPos;
            if (!curPx) return;
            ctx.save();
            ctx.strokeStyle = this.style.color || '#787b86';
            ctx.lineWidth = 1; ctx.setLineDash([4, 4]); ctx.globalAlpha = 0.5;
            ctx.beginPath(); ctx.moveTo(lastPx.x, lastPx.y); ctx.lineTo(curPx.x, curPx.y); ctx.stroke();
            ctx.restore();
        }

        getAnchorPoints(pixels) { return pixels ? pixels.filter(Boolean) : []; }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var mids = [];
            for (var i = 0; i < pixels.length - 1; i++) {
                mids.push(GeometryUtils.midpoint(pixels[i], pixels[i + 1]));
            }
            return mids;
        }

        getFillShape(pixels) {
            if (!this._cachedPrimitives || !pixels || pixels.length < 2) return null;
            var fillPrims = this._cachedPrimitives.filter(function(p) { return p.type === 'fill'; });
            if (fillPrims.length > 0) return fillPrims[0].vertices;
            var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i].x < minX) minX = pixels[i].x;
                if (pixels[i].y < minY) minY = pixels[i].y;
                if (pixels[i].x > maxX) maxX = pixels[i].x;
                if (pixels[i].y > maxY) maxY = pixels[i].y;
            }
            if (minX === Infinity) return null;
            return [{x:minX,y:minY},{x:maxX,y:minY},{x:maxX,y:maxY},{x:minX,y:maxY}];
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels) return;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
            }
            var mids = this.getMidpoints(pixels);
            for (var mi = 0; mi < mids.length; mi++) {
                if (mids[mi]) this._drawHandle(ctx, mids[mi], isSelected, true);
            }
        }

        _drawHandle(ctx, p, isSelected, isMidpoint) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            if (isMidpoint) {
                var s = 5;
                ctx.beginPath(); ctx.moveTo(p.x, p.y - s); ctx.lineTo(p.x + s, p.y);
                ctx.lineTo(p.x, p.y + s); ctx.lineTo(p.x - s, p.y); ctx.closePath();
            } else {
                ctx.beginPath(); ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            }
            ctx.fill();
            ctx.strokeStyle = '#2962ff'; ctx.lineWidth = 1; ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords) return;
            this.coords = this.coords.map(function(c) {
                var pixel = chartState.coordToPixel(c);
                if (!pixel) return c;
                return chartState.pixelToCoord(pixel.x + dx, pixel.y + dy) || c;
            });
            this._invalidateCache();
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
            this._invalidateCache();
        }
    }

    // --- Tool Subclasses ---
    class ImpulseWave extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.impulse; } }
    class CorrectiveWave extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.corrective; } }
    class ElliottTriangle extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.triangle; } }
    class ElliottDoubleCombo extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.double_combo; } }
    class ElliottTripleCombo extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.triple_combo; } }
    class ElliottFlat extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.flat; } }
    class ElliottZigZag extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.zigzag; } }
    class ElliottCombination extends BaseWaveDrawing { getWaveDef() { return WAVE_DEFS.combination; } }

    // =============================================================================
    // Pattern Tools (Phase 3.7) — Shared Pattern Geometry Engine
    // =============================================================================
    // Architecture: PATTERN_DEFS → PatternGeometryGenerator →
    // coordLine/coordLabel/coordPolygon → GeometryRenderer → canvas
    // =============================================================================

    // PATTERN_DEFS — per-tool configuration
    var PATTERN_DEFS = {
        // === Harmonic Patterns (5-point XABCD) ===
        // PHASE D2 (BUG-007): generic XABCD. A plain XABCD is an
        // UNCONSTRAINED 5-point harmonic skeleton -- it deliberately carries
        // NO fibRules, because the label renderer only emits ratio labels
        // when def.fibRules is present. Previously `xabcd_pattern` aliased
        // the Gartley class and therefore displayed Gartley's specific
        // ratios (XA 0.618, CD 0.786, ...) on a pattern that has no such
        // requirement.
        xabcd: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },
        gartley: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'},{label:'D'}],
            fibRules: {
                XA: { retrace: [0.618] },
                AB: { retrace: [0.382,0.5,0.618,0.786,0.886] },
                BC: { extension: [1.13,1.272,1.414,1.618] },
                CD: { retrace: [0.786] },
                AD: { retrace: [0.786] }
            }
        },
        butterfly: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'},{label:'D'}],
            fibRules: {
                XA: { retrace: [0.786] },
                AB: { retrace: [0.382,0.5,0.618,0.786,0.886] },
                BC: { extension: [1.618,2.0,2.24,2.618] },
                CD: { extension: [1.27,1.618] },
                AD: { extension: [1.27] }
            }
        },
        bat: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'},{label:'D'}],
            fibRules: {
                XA: { retrace: [0.382,0.5] },
                AB: { retrace: [0.382,0.5,0.618,0.786,0.886] },
                BC: { extension: [1.618,2.0] },
                CD: { retrace: [0.886] }
            }
        },
        crab: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'},{label:'D'}],
            fibRules: {
                XA: { retrace: [0.382,0.618] },
                AB: { retrace: [0.382,0.5,0.618,0.786,0.886] },
                BC: { extension: [2.618,3.618] },
                CD: { extension: [1.618] },
                AD: { extension: [1.618] }
            }
        },
        deep_crab: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'},{label:'D'}],
            fibRules: {
                XA: { retrace: [0.886] },
                AB: { retrace: [0.382,0.5,0.618,0.786,0.886] },
                BC: { extension: [1.618,2.0] },
                CD: { extension: [1.618] },
                AD: { extension: [1.618] }
            }
        },
        shark: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'0'},{label:'X'},{label:'A'},{label:'B'},{label:'C'}],
            fibRules: {
                XA: { retrace: [0.5,0.886] },
                AB: { extension: [1.13,1.618] },
                BC: { extension: [1.618,2.24] }
            }
        },
        cypher: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'},{label:'D'}],
            fibRules: {
                XA: { retrace: [0.382,0.618] },
                AB: { extension: [1.13,1.414] },
                CD: { retrace: [0.786] }
            }
        },
        abcd: {
            geometry: PatternGeometry.HARMONIC,
            anchorMeta: [{label:'X'},{label:'A'},{label:'B'},{label:'C'}],
            fibRules: {
                AB: { retrace: [0.618,0.786] },
                BC: { extension: [1.272,1.618] },
                AC: { retrace: [1.0] }
            }
        },

        // === Chart Patterns ===
        head_and_shoulders: {
            geometry: PatternGeometry.POLYLINE,
            anchorMeta: [{label:'LS'},{label:'H'},{label:'RS'},{label:'N1'},{label:'N2'}]
        },
        inverse_head_and_shoulders: {
            geometry: PatternGeometry.POLYLINE,
            anchorMeta: [{label:'LB'},{label:'V'},{label:'RB'},{label:'N1'},{label:'N2'}]
        },
        double_top: {
            geometry: PatternGeometry.POLYLINE,
            anchorMeta: [{label:'T1'},{label:'V'},{label:'T2'}]
        },
        double_bottom: {
            geometry: PatternGeometry.POLYLINE,
            anchorMeta: [{label:'B1'},{label:'P'},{label:'B2'}]
        },
        triple_top: {
            geometry: PatternGeometry.POLYLINE,
            anchorMeta: [{label:'T1'},{label:'V1'},{label:'T2'},{label:'V2'},{label:'T3'}]
        },
        triple_bottom: {
            geometry: PatternGeometry.POLYLINE,
            anchorMeta: [{label:'B1'},{label:'P1'},{label:'B2'},{label:'P2'},{label:'B3'}]
        },

        // === Triangles ===
        ascending_triangle: {
            geometry: PatternGeometry.POLYGON,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },
        descending_triangle: {
            geometry: PatternGeometry.POLYGON,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },
        symmetrical_triangle: {
            geometry: PatternGeometry.POLYGON,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },
        expanding_triangle: {
            geometry: PatternGeometry.POLYGON,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },

        // === Wedges ===
        rising_wedge: {
            geometry: PatternGeometry.WEDGE,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },
        falling_wedge: {
            geometry: PatternGeometry.WEDGE,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },

        // === Channels ===
        ascending_channel: {
            geometry: PatternGeometry.CHANNEL,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        },
        descending_channel: {
            geometry: PatternGeometry.CHANNEL,
            anchorMeta: [{label:'A'},{label:'B'},{label:'C'},{label:'D'}]
        }
    };

    // PatternGeometryGenerator — coordinate-space primitive generator
    var PatternGeometryGenerator = {
        generate: function(anchors, def, chartState, cw, ch, style) {
            if (!anchors || anchors.length < 2) return [];
            switch (def.geometry) {
                case PatternGeometry.HARMONIC: return this._harmonic(anchors, def, style);
                case PatternGeometry.POLYLINE: return this._polyline(anchors, def, style);
                case PatternGeometry.POLYGON:  return this._polygon(anchors, def, style);
                case PatternGeometry.WEDGE:    return this._wedge(anchors, def, style);
                case PatternGeometry.CHANNEL:  return this._channel(anchors, def, style);
            }
            return [];
        },

        // Shared helpers
        _segColor: function(i, def, fallback) {
            return (def.colors && def.colors[i]) || fallback || '#787b86';
        },

        // Build label from fibRules entry
        _fibLabel: function(rules) {
            if (!rules) return '';
            var parts = [];
            if (rules.retrace && rules.retrace.length) {
                var r = rules.retrace;
                parts.push(r.length === 1 ? r[0].toFixed(3).replace(/0+$/,'') : r[0].toFixed(3).replace(/0+$/,'')+'\u2013'+r[r.length-1].toFixed(3).replace(/0+$/,''));
            }
            if (rules.extension && rules.extension.length) {
                var e = rules.extension;
                parts.push(e.length === 1 ? e[0].toFixed(3).replace(/0+$/,'') : e[0].toFixed(3).replace(/0+$/,'')+'\u2013'+e[e.length-1].toFixed(3).replace(/0+$/,''));
            }
            return parts.join(' / ');
        },

        // ---- Harmonic: sequential polyline + Fib ratio labels between legs ----
        _harmonic: function(anchors, def, style) {
            var out = [], lc = style.color || '#787b86', lw = style.width || 2, lo = style.opacity != null ? style.opacity : 0.9;
            for (var i = 0; i < anchors.length - 1; i++) {
                var sc = this._segColor(i, def, lc);
                out.push({ type:'coordLine', start:anchors[i], end:anchors[i+1], style:{color:sc,width:lw,opacity:lo,lineDash:[]} });
            }
            // Labels at anchors
            if (style.showLabel !== false && def.anchorMeta) {
                for (var i = 0; i < def.anchorMeta.length && i < anchors.length; i++) {
                    var meta = def.anchorMeta[i];
                    if (!meta || !meta.label) continue;
                    var sc = this._segColor(Math.max(0,i-1), def, lc);
                    out.push({ type:'coordLabel', coord:anchors[i], text:meta.label,
                        offsetX:style.labelOffsetX||8, offsetY:style.labelOffsetY||8,
                        style:{color:sc,fontFamily:style.fontFamily||'-apple-system, Roboto, sans-serif',fontSize:(style.fontSize||12)+2,align:'left'} });
                }
            }
            // Fib ratio labels on each segment
            if (style.showLabel !== false && def.fibRules) {
                var legNames = Object.keys(def.fibRules);
                for (var i = 0; i < legNames.length && i < anchors.length - 1; i++) {
                    var leg = legNames[i];
                    var mid = { time: (anchors[i].time + anchors[i+1].time) / 2, price: (anchors[i].price + anchors[i+1].price) / 2 };
                    var lbl = this._fibLabel(def.fibRules[leg]);
                    if (lbl) {
                        out.push({ type:'coordLabel', coord:mid, text:lbl,
                            offsetX:style.ratioOffsetX||0, offsetY:style.ratioOffsetY||-16,
                            style:{color:'#787b86',fontFamily:style.fontFamily||'-apple-system, Roboto, sans-serif',fontSize:(style.fontSize||9),align:'center'} });
                    }
                }
            }
            return out;
        },

        // ---- Polyline: connect points sequentially ----
        _polyline: function(anchors, def, style) {
            var out = [], lc = style.color || '#787b86', lw = style.width || 2, lo = style.opacity != null ? style.opacity : 0.9;
            for (var i = 0; i < anchors.length - 1; i++) {
                out.push({ type:'coordLine', start:anchors[i], end:anchors[i+1], style:{color:lc,width:lw,opacity:lo,lineDash:[]} });
            }
            if (style.showLabel !== false && def.anchorMeta) {
                for (var i = 0; i < def.anchorMeta.length && i < anchors.length; i++) {
                    var meta = def.anchorMeta[i];
                    if (!meta || !meta.label) continue;
                    out.push({ type:'coordLabel', coord:anchors[i], text:meta.label,
                        offsetX:style.labelOffsetX||8, offsetY:style.labelOffsetY||8,
                        style:{color:lc,fontFamily:style.fontFamily||'-apple-system, Roboto, sans-serif',fontSize:(style.fontSize||11)+2,align:'left'} });
                }
            }
            return out;
        },

        // ---- Polygon: closed shape with fill ----
        _polygon: function(anchors, def, style) {
            var out = this._polyline(anchors, def, style);
            // Close loop
            if (anchors.length >= 3) {
                out.push({ type:'coordLine', start:anchors[anchors.length-1], end:anchors[0],
                    style:{color:style.color||'#787b86',width:style.width||1,opacity:(style.opacity!=null?style.opacity:0.6),lineDash:[]} });
                out.push({ type:'coordPolygon', vertices:anchors,
                    style:{fillColor:style.fillColor||style.color||'#787b86',fillOpacity:style.fillOpacity!=null?style.fillOpacity:0.08,
                           strokeColor:style.color,lineWidth:style.width||1,opacity:style.opacity!=null?style.opacity:0.8,lineDash:style.lineDash||[]} });
            }
            return out;
        },

        // ---- Wedge: two converging lines ----
        _wedge: function(anchors, def, style) {
            var out = [], lc = style.color || '#787b86', lw = style.width || 1.5;
            if (anchors.length >= 4) {
                // Upper line: anchor[0]→anchor[1], Lower line: anchor[2]→anchor[3]
                out.push({ type:'coordLine', start:anchors[0], end:anchors[1], style:{color:lc,width:lw,opacity:0.8,lineDash:[]} });
                out.push({ type:'coordLine', start:anchors[2], end:anchors[3], style:{color:lc,width:lw,opacity:0.8,lineDash:[]} });
                // Fill between
                out.push({ type:'coordPolygon', vertices:[anchors[0],anchors[1],anchors[3],anchors[2]],
                    style:{fillColor:style.fillColor||lc,fillOpacity:style.fillOpacity!=null?style.fillOpacity:0.06,lineWidth:0} });
            }
            // Labels
            if (style.showLabel !== false && def.anchorMeta) {
                for (var i = 0; i < def.anchorMeta.length && i < anchors.length; i++) {
                    var meta = def.anchorMeta[i];
                    if (!meta || !meta.label) continue;
                    out.push({ type:'coordLabel', coord:anchors[i], text:meta.label,
                        offsetX:style.labelOffsetX||8, offsetY:style.labelOffsetY||8,
                        style:{color:lc,fontFamily:style.fontFamily||'-apple-system, Roboto, sans-serif',fontSize:(style.fontSize||10)+1,align:'left'} });
                }
            }
            return out;
        },

        // ---- Channel: parallel lines ----
        _channel: function(anchors, def, style) {
            // Same as wedge but with parallel intent — for now same rendering
            return this._wedge(anchors, def, style);
        }
    };

    // BasePatternDrawing — base class for all pattern tools
    class BasePatternDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this._cachedPrimitives = null;
        }

        getPatternDef() { return null; }

        _invalidateCache() { this._cachedPrimitives = null; }

        onViewportChange() { this._invalidateCache(); }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            this._invalidateCache();
            var def = this.getPatternDef();
            var n = def ? def.anchorMeta.length : 2;
            return this.coords.length >= n;
        }

        _generatePrimitives(chartState) {
            var def = this.getPatternDef();
            if (!def || !chartState || !chartState.coordToPixel) return null;
            var dpr = chartState.dpr || window.devicePixelRatio || 1;
            var cw = (chartState.canvasWidth || 800) / dpr;
            var ch = (chartState.canvasHeight || 600) / dpr;
            return PatternGeometryGenerator.generate(this.coords, def, chartState, cw, ch, this.style);
        }

        _getPrimitives(chartState) {
            if (!this._cachedPrimitives) this._cachedPrimitives = this._generatePrimitives(chartState);
            return this._cachedPrimitives;
        }

        draw(ctx, chartState, isSelected) {
            if (!chartState || !chartState.coordToPixel) return;
            var primitives = this._getPrimitives(chartState);
            if (!primitives || !primitives.length) {
                if (this.currentPos && this.coords.length > 0 && this.coords.length < (this.getPatternDef() ? this.getPatternDef().anchorMeta.length : 2)) {
                    this._drawPreview(ctx, chartState);
                }
                return;
            }
            GeometryRenderer.render(ctx, primitives, chartState);
        }

        _drawPreview(ctx, chartState) {
            var pixels = this.getPixels(chartState);
            if (!pixels || pixels.length === 0) return;
            var lastPx = pixels[pixels.length - 1];
            if (!lastPx || !this.currentPos) return;
            ctx.save();
            ctx.strokeStyle = this.style.color || '#787b86';
            ctx.lineWidth = 1; ctx.setLineDash([4,4]); ctx.globalAlpha = 0.5;
            ctx.beginPath(); ctx.moveTo(lastPx.x, lastPx.y); ctx.lineTo(this.currentPos.x, this.currentPos.y); ctx.stroke();
            ctx.restore();
        }

        getAnchorPoints(pixels) { return pixels ? pixels.filter(Boolean) : []; }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var mids = [];
            for (var i = 0; i < pixels.length - 1; i++) mids.push(GeometryUtils.midpoint(pixels[i], pixels[i+1]));
            return mids;
        }

        getFillShape(pixels) {
            if (!this._cachedPrimitives || !pixels || pixels.length < 2) return null;
            for (var i = 0; i < this._cachedPrimitives.length; i++) {
                if (this._cachedPrimitives[i].type === 'coordPolygon') {
                    var verts = this._cachedPrimitives[i].vertices;
                    if (verts.length >= 3) return verts; // return coordinate-space verts
                }
            }
            var minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
            for (var i=0;i<pixels.length;i++) {
                if(pixels[i].x<minX)minX=pixels[i].x; if(pixels[i].y<minY)minY=pixels[i].y;
                if(pixels[i].x>maxX)maxX=pixels[i].x; if(pixels[i].y>maxY)maxY=pixels[i].y;
            }
            return minX===Infinity ? null : [{x:minX,y:minY},{x:maxX,y:minY},{x:maxX,y:maxY},{x:minX,y:maxY}];
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels) return;
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
            }
            var mids = this.getMidpoints(pixels);
            for (var mi = 0; mi < mids.length; mi++) {
                if (mids[mi]) this._drawHandle(ctx, mids[mi], isSelected, true);
            }
        }

        _drawHandle(ctx, p, isSelected, isMidpoint) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            if (isMidpoint) {
                var s = 5;
                ctx.beginPath(); ctx.moveTo(p.x,p.y-s); ctx.lineTo(p.x+s,p.y);
                ctx.lineTo(p.x,p.y+s); ctx.lineTo(p.x-s,p.y); ctx.closePath();
            } else {
                ctx.beginPath(); ctx.arc(p.x,p.y,isSelected?5:4,0,Math.PI*2);
            }
            ctx.fill(); ctx.strokeStyle='#2962ff'; ctx.lineWidth=1; ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked||!chartState||!this.coords) return;
            this.coords = this.coords.map(function(c){
                var p = chartState.coordToPixel(c);
                return p ? (chartState.pixelToCoord(p.x+dx,p.y+dy)||c) : c;
            });
            this._invalidateCache();
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            super.updateHandle(draggedHandle,newPrice,chartState,pixelX);
            this._invalidateCache();
        }
    }

    // --- Tool Subclasses (22 patterns) ---
    // Harmonic
    class Gartley extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.gartley; } }
    // PHASE D2 (BUG-007): generic XABCD gets its own class/def so it no
    // longer inherits Gartley-specific Fibonacci labelling.
    class XabcdPattern extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.xabcd; } }
    class Butterfly extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.butterfly; } }
    class Bat extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.bat; } }
    class Crab extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.crab; } }
    class DeepCrab extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.deep_crab; } }
    class Shark extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.shark; } }
    class Cypher extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.cypher; } }
    class Abcd extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.abcd; } }
    // Chart Patterns
    class HeadAndShoulders extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.head_and_shoulders; } }
    class InverseHeadAndShoulders extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.inverse_head_and_shoulders; } }
    class DoubleTop extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.double_top; } }
    class DoubleBottom extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.double_bottom; } }
    class TripleTop extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.triple_top; } }
    class TripleBottom extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.triple_bottom; } }
    // Triangles
    class AscendingTriangle extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.ascending_triangle; } }
    class DescendingTriangle extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.descending_triangle; } }
    class SymmetricalTriangle extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.symmetrical_triangle; } }
    class ExpandingTriangle extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.expanding_triangle; } }
    // Wedges
    class RisingWedge extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.rising_wedge; } }
    class FallingWedge extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.falling_wedge; } }
    // Channels
    class AscendingChannel extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.ascending_channel; } }
    class DescendingChannel extends BasePatternDrawing { getPatternDef() { return PATTERN_DEFS.descending_channel; } }

    // =============================================================================
    // Phase 3.9 — Rich Objects: Text, Icons, Emojis, Images, Stickers
    // Architecture: OBJECT_DEFS → ObjectGeometryGenerator →
    // coordBounds/coordText/coordImage/coordEmoji/coordIcon → PRIMITIVE_RENDERERS → canvas
    // =============================================================================

    // RichGeometry — constants for geometry strategies
    var RichGeometry = {
        TEXT:   'text',
        IMAGE:  'image',
        EMOJI:  'emoji',
        ICON:   'icon',
        STICKER:'sticker'
    };

    // OBJECT_DEFS — per-object configuration
    var OBJECT_DEFS = {
        // Group 1: Text
        text_note: {
            geometry: RichGeometry.TEXT,
            points: 1,
            anchorMeta: [{label:''}],
            defaults: { layer: Layer.TEXT, fontSize: 14, background: '#2a2e39', color: '#d1d4dc' }
        },
        anchored_text: {
            geometry: RichGeometry.TEXT,
            points: 1,
            anchorMeta: [{label:''}],
            defaults: { layer: Layer.TEXT, fontSize: 14, align: 'center' }
        },
        callout: {
            geometry: RichGeometry.TEXT,
            points: 2,
            anchorMeta: [{label:''},{label:''}],
            defaults: { layer: Layer.TEXT, background: '#2a2e39', cornerRadius: 6, color: '#d1d4dc', hasTail: true }
        },
        balloon: {
            geometry: RichGeometry.TEXT,
            points: 2,
            anchorMeta: [{label:''},{label:''}],
            defaults: { layer: Layer.TEXT, background: '#2a2e39', cornerRadius: 12, color: '#d1d4dc', hasTail: true }
        },
        arrow_label: {
            geometry: RichGeometry.TEXT,
            points: 2,
            anchorMeta: [{label:''},{label:'→'}],
            defaults: { layer: Layer.TEXT, fontSize: 13, color: '#2962ff' }
        },
        // Group 2: Icons & Emojis
        emoji: {
            geometry: RichGeometry.EMOJI,
            points: 1,
            anchorMeta: [{label:''}],
            defaults: { layer: Layer.DRAWINGS_BELOW, emoji: '📌', fontSize: 28, behindCandles: true }
        },
        icon: {
            geometry: RichGeometry.ICON,
            points: 1,
            anchorMeta: [{label:''}],
            defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'info', size: 24, color: '#787b86' }
        },
        symbol: {
            geometry: RichGeometry.ICON,
            points: 1,
            anchorMeta: [{label:''}],
            defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'star', size: 20, color: '#ff9800' }
        },
        // Group 3: Images
        image: {
            geometry: RichGeometry.IMAGE,
            points: 2,
            anchorMeta: [{label:'TL'},{label:'BR'}],
            defaults: { layer: Layer.DRAWINGS_ABOVE }
        },
        watermark: {
            geometry: RichGeometry.IMAGE,
            points: 2,
            anchorMeta: [{label:'TL'},{label:'BR'}],
            defaults: { layer: Layer.BACKGROUND, opacity: 0.15 }
        },
        logo: {
            geometry: RichGeometry.IMAGE,
            points: 2,
            anchorMeta: [{label:'TL'},{label:'BR'}],
            defaults: { layer: Layer.DRAWINGS_ABOVE }
        },
        // Group 4: Stickers
        sticker_buy:     { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'buy', size: 32, color: '#26a69a' } },
        sticker_sell:    { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'sell', size: 32, color: '#ef5350' } },
        sticker_long:    { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'long', size: 28, color: '#26a69a' } },
        sticker_short:   { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'short', size: 28, color: '#ef5350' } },
        sticker_target:  { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'target', size: 28, color: '#4caf50' } },
        sticker_stop:    { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'stop', size: 28, color: '#f44336' } },
        sticker_star:    { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'star', size: 28, color: '#ff9800' } },
        sticker_pin:     { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'pin', size: 28, color: '#f44336' } },
        sticker_check:   { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'check', size: 28, color: '#4caf50' } },
        sticker_warning: { geometry: RichGeometry.STICKER, points: 1, anchorMeta: [{label:''}], defaults: { layer: Layer.DRAWINGS_ABOVE, icon: 'warning', size: 28, color: '#ff9800' } },
    };

    // ObjectGeometryGenerator — generates coordinate-space primitives
    var ObjectGeometryGenerator = {
        generate: function(anchors, def, chartState, style, content, rotation, assetManager) {
            if (!anchors || anchors.length < def.points) return [];
            switch (def.geometry) {
                case RichGeometry.TEXT:    return this._text(anchors, def, style, content, rotation);
                case RichGeometry.IMAGE:   return this._image(anchors, def, style, content, rotation, assetManager);
                case RichGeometry.EMOJI:   return this._emoji(anchors, def, style, content);
                case RichGeometry.ICON:    return this._icon(anchors, def, style, content);
                case RichGeometry.STICKER: return this._sticker(anchors, def, style, content);
            }
            return [];
        },

        _text: function(anchors, def, style, content, rotation) {
            if (def.defaults && def.defaults.hasTail && anchors.length >= 2) {
                return [{
                    type: 'coordCallout',
                    p1: anchors[0],
                    p2: anchors[1],
                    text: content && content.plain ? content.plain : 'Callout',
                    style: Object.assign({}, def.defaults, style)
                }];
            }
            var out = [];
            var bg = style.background || def.defaults.background;
            var color = style.color || def.defaults.color || '#d1d4dc';
            var fs = style.fontSize || def.defaults.fontSize || 13;
            var hasBg = bg || style.fillColor;
            // If 2 anchors, draw bounds background
            if (anchors.length >= 2 && hasBg) {
                out.push({ type:'coordBounds', topLeft:anchors[0], bottomRight:anchors[1],
                    style:{ fillColor: style.fillColor || bg, fillOpacity: style.fillOpacity || 0.9, cornerRadius: style.cornerRadius || 0 }, rotation: rotation || 0 });
            }
            out.push({ type:'coordText', coord:anchors[0],
                content: content || { plain: 'Text' },
                offsetX: style.offsetX || 8, offsetY: style.offsetY || -(fs + 4),
                style: { color: color, fontSize: fs, fontFamily: style.fontFamily || '-apple-system, Roboto, sans-serif',
                    bold: !!style.bold, italic: !!style.italic, align: style.align || 'left',
                    background: hasBg ? null : (style.background || null), maxWidth: style.maxWidth || 400,
                    opacity: style.opacity != null ? style.opacity : 1 } });
            return out;
        },

        _image: function(anchors, def, style, content, rotation, assetManager) {
            if (anchors.length < 2) return [];
            var out = [];
            // Load image from AssetManager if available
            var img = null;
            if (assetManager && style.assetId) { img = assetManager.getImage(style.assetId); }
            var opacity = def.defaults.opacity != null ? def.defaults.opacity : (style.opacity != null ? style.opacity : 1);
            var cornerRadius = style.cornerRadius || 0;
            out.push({ type:'coordImage', topLeft:anchors[0], bottomRight:anchors[1],
                image: img, rotation: rotation || 0,
                style: { opacity: opacity, cornerRadius: cornerRadius } });
            // Border
            if (style.color) {
                out.push({ type:'coordBounds', topLeft:anchors[0], bottomRight:anchors[1],
                    style:{ strokeColor: style.color, lineWidth: style.width || 1, opacity: 0.6, cornerRadius: cornerRadius } });
            }
            return out;
        },

        _emoji: function(anchors, def, style, content) {
            var out = [];
            var emoji = (content && content.plain) || def.defaults.emoji || '📌';
            var size = style.fontSize || def.defaults.fontSize || 24;
            out.push({ type:'coordEmoji', coord:anchors[0], emoji: emoji, size: size,
                style:{ opacity: style.opacity != null ? style.opacity : 1 } });
            return out;
        },

        _icon: function(anchors, def, style, content) {
            var out = [];
            var iconName = def.defaults.icon || 'info';
            var size = style.size || def.defaults.size || 24;
            var color = style.color || def.defaults.color || '#787b86';
            // Try to get icon path from AssetManager
            var iconData = null;
            var assetMgr = window._drawingEngine ? window._drawingEngine.assetManager : null;
            if (assetMgr) iconData = assetMgr.getIcon(iconName);
            out.push({ type:'coordIcon', coord:anchors[0], icon: iconName,
                path: iconData ? iconData.path : null, viewBox: iconData ? iconData.viewBox : '0 0 24 24',
                size: size,
                style:{ color: color, opacity: style.opacity != null ? style.opacity : 1 } });
            return out;
        },

        _sticker: function(anchors, def, style, content) {
            // Stickers are essentially icons with presets
            return this._icon(anchors, def, style, content);
        }
    };

    // Global function to draw visible candles overlay over background/below-layer drawings
    window.drawVisibleCandlesOverlay = function(ctx, chartState) {
        if (!window.bigCandleSeries || !window.bigChart) return;
        var timeScale = window.bigChart.timeScale();
        if (!timeScale) return;
        var data = null;
        try {
            if (typeof window.bigCandleSeries.data === 'function') {
                data = window.bigCandleSeries.data();
            }
        } catch(e) {}
        if (!data || !data.length) return;

        var chartOpt = window.bigCandleSeries.options ? window.bigCandleSeries.options() : {};
        var upColor = chartOpt.upColor || '#089981';
        var downColor = chartOpt.downColor || '#f23645';
        var wickUpColor = chartOpt.wickUpColor || upColor;
        var wickDownColor = chartOpt.wickDownColor || downColor;

        var tsOpt = (timeScale.options && timeScale.options()) || {};
        var barSpacing = tsOpt.barSpacing || 6;
        var candleWidth = Math.max(1, Math.floor(barSpacing * 0.75));
        if (candleWidth % 2 === 0) candleWidth += 1; // odd width for crisp centering
        var halfWidth = Math.floor(candleWidth / 2);

        ctx.save();
        for (var i = 0; i < data.length; i++) {
            var c = data[i];
            var x = timeScale.timeToCoordinate(c.time);
            if (x === null || x === undefined || x < -30 || x > ctx.canvas.width + 30) continue;

            var openY = window.bigCandleSeries.priceToCoordinate(c.open);
            var highY = window.bigCandleSeries.priceToCoordinate(c.high);
            var lowY = window.bigCandleSeries.priceToCoordinate(c.low);
            var closeY = window.bigCandleSeries.priceToCoordinate(c.close);

            if (openY === null || highY === null || lowY === null || closeY === null) continue;

            var isUp = c.close >= c.open;
            var bodyColor = isUp ? upColor : downColor;
            var wickColor = isUp ? wickUpColor : wickDownColor;

            var px = Math.round(x);

            // Draw Wick
            ctx.strokeStyle = wickColor;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(px + 0.5, Math.round(highY));
            ctx.lineTo(px + 0.5, Math.round(lowY));
            ctx.stroke();

            // Draw Body
            var bodyTop = Math.round(Math.min(openY, closeY));
            var bodyHeight = Math.max(1, Math.round(Math.abs(closeY - openY)));
            ctx.fillStyle = bodyColor;
            ctx.fillRect(px - halfWidth, bodyTop, candleWidth, bodyHeight);
        }
        ctx.restore();
    };

    // BaseRichDrawing — base class for all rich objects
    class BaseRichDrawing extends BaseDrawing {
        constructor(startPos, chartState, options = {}) {
            super(startPos, chartState, options);
            this._cachedPrimitives = null;
            var def = this.getObjectDef();
            var defaultEmoji = def && def.defaults && def.defaults.emoji;
            this.content = { plain: options.emoji || (this.model && this.model.content && this.model.content.plain) || window.selectedEmoji || defaultEmoji || '📌' };
            this.rotation = options.rotation || 0;
        }

        getObjectDef() { return null; }

        _invalidateCache() { this._cachedPrimitives = null; }

        onViewportChange() { this._invalidateCache(); }

        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            this._invalidateCache();
            var def = this.getObjectDef();
            return this.coords.length >= (def ? def.points : 1);
        }

        _generatePrimitives(chartState) {
            var def = this.getObjectDef();
            if (!def || !chartState || !chartState.coordToPixel) return null;
            var content = (this.model && this.model.content) || this.content || null;
            var rotation = this.rotation || ((this.model && this.model.rotation) || 0);
            var am = (window._drawingEngine && window._drawingEngine.assetManager) || null;
            return ObjectGeometryGenerator.generate(this.coords, def, chartState, this.style, content, rotation, am);
        }

        _getPrimitives(chartState) {
            if (!this._cachedPrimitives) this._cachedPrimitives = this._generatePrimitives(chartState);
            return this._cachedPrimitives;
        }

        draw(ctx, chartState, isSelected) {
            if (!chartState || !chartState.coordToPixel) return;
            var primitives = this._getPrimitives(chartState);
            if (!primitives || !primitives.length) {
                if (this.currentPos && this.coords.length > 0 && this.coords.length < (this.getObjectDef() ? this.getObjectDef().points : 1)) {
                    this._drawPreview(ctx, chartState);
                }
                return;
            }
            GeometryRenderer.render(ctx, primitives, chartState);
        }

        _drawPreview(ctx, chartState) {
            var pixels = this.getPixels(chartState);
            if (!pixels || pixels.length === 0) return;
            var lastPx = pixels[pixels.length - 1];
            if (!lastPx || !this.currentPos) return;
            ctx.save();
            ctx.strokeStyle = this.style.color || '#787b86';
            ctx.lineWidth = 1; ctx.setLineDash([4,4]); ctx.globalAlpha = 0.5;
            ctx.beginPath(); ctx.moveTo(lastPx.x, lastPx.y); ctx.lineTo(this.currentPos.x, this.currentPos.y); ctx.stroke();
            ctx.restore();
        }

        getAnchorPoints(pixels) { return pixels ? pixels.filter(Boolean) : []; }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var mids = [];
            for (var i = 0; i < pixels.length - 1; i++) mids.push(GeometryUtils.midpoint(pixels[i], pixels[i+1]));
            return mids;
        }

        // 8-direction resize handle API (Phase 3.9)
        // Reserve: NW, N, NE, W, E, SW, S, SE
        // Returns pixel positions for each handle, or null if not applicable
        getResizeHandles(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var box = GeometryUtils.boundingBox(pixels);
            if (!box) return null;
            var cx = (box.left + box.right) / 2, cy = (box.top + box.bottom) / 2;
            return {
                NW: { x: box.left, y: box.top },
                N:  { x: cx, y: box.top },
                NE: { x: box.right, y: box.top },
                W:  { x: box.left, y: cy },
                E:  { x: box.right, y: cy },
                SW: { x: box.left, y: box.bottom },
                S:  { x: cx, y: box.bottom },
                SE: { x: box.right, y: box.bottom }
            };
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length === 0) return null;
            var def = this.getObjectDef();
            if (def && (def.geometry === RichGeometry.EMOJI || this.constructor.name === 'EmojiDrawing' || (def.defaults && def.defaults.emoji))) {
                var size = (this.style && this.style.fontSize) || (def.defaults && def.defaults.fontSize) || 28;
                var p = pixels[0];
                if (!p) return null;
                var half = size / 2 + 5;
                return [
                    { x: p.x - half, y: p.y - half },
                    { x: p.x + half, y: p.y - half },
                    { x: p.x + half, y: p.y + half },
                    { x: p.x - half, y: p.y + half }
                ];
            }
            if (def && def.geometry === RichGeometry.TEXT) {
                var fontSize = this.style.fontSize || 14;
                var text = this.text || (this.model && this.model.content && this.model.content.plain) || '';
                if (!text && this.model && this.model.text && this.model.text.content) {
                    text = this.model.text.content;
                }
                if (!text) text = 'Text';
                var lines = text.split('\n');
                var maxW = 0;
                for (var i = 0; i < lines.length; i++) {
                    var w = lines[i].length * fontSize * 0.6; // approximation
                    if (w > maxW) maxW = w;
                }
                var lineH = fontSize * 1.3;
                var pad = 8;
                var bw = maxW + pad * 2;
                var bh = lines.length * lineH + pad * 2;

                if (pixels.length === 1) {
                    var p1 = pixels[0];
                    var align = this.style.align || 'left';
                    var xOffset = 0;
                    if (align === 'center') {
                        xOffset = -bw / 2;
                    } else if (align === 'right') {
                        xOffset = -bw;
                    }

                    var bx = p1.x + xOffset;
                    var by = p1.y - bh / 2;

                    return [
                        { x: bx, y: by },
                        { x: bx + bw, y: by },
                        { x: bx + bw, y: by + bh },
                        { x: bx, y: by + bh }
                    ];
                } else if (pixels.length === 2 && def.defaults && def.defaults.hasTail) {
                    var p2 = pixels[1];
                    var bx = p2.x - bw / 2;
                    var by = p2.y - bh / 2;
                    return [
                        { x: bx, y: by },
                        { x: bx + bw, y: by },
                        { x: bx + bw, y: by + bh },
                        { x: bx, y: by + bh }
                    ];
                }
            }
            return pixels && pixels.length >= 2 ? [
                pixels[0],
                { x: pixels[1].x, y: pixels[0].y },
                pixels[1],
                { x: pixels[0].x, y: pixels[1].y }
            ] : null;
        }

        drawHandles(ctx, pixels, isSelected) {
            if (!pixels || pixels.length === 0) return;
            var def = this.getObjectDef();
            var isEmoji = def && (def.geometry === RichGeometry.EMOJI || this.constructor.name === 'EmojiDrawing' || (def.defaults && def.defaults.emoji));
            if (isEmoji && pixels.length === 1 && pixels[0]) {
                var p = pixels[0];
                var size = (this.style && this.style.fontSize) || (def.defaults && def.defaults.fontSize) || 28;
                var half = size / 2 + 5;
                if (isSelected) {
                    ctx.save();
                    ctx.strokeStyle = '#2962ff';
                    ctx.lineWidth = 1;
                    ctx.setLineDash([3, 3]);
                    ctx.strokeRect(p.x - half, p.y - half, size + 10, size + 10);
                    ctx.setLineDash([]);
                    this._drawHandle(ctx, { x: p.x - half, y: p.y - half }, true, false);
                    this._drawHandle(ctx, { x: p.x + half, y: p.y - half }, true, false);
                    this._drawHandle(ctx, { x: p.x + half, y: p.y + half }, true, false);
                    this._drawHandle(ctx, { x: p.x - half, y: p.y + half }, true, false);
                    ctx.restore();
                } else {
                    this._drawHandle(ctx, p, false, false);
                }
                return;
            }

            // Standard anchor handles
            for (var i = 0; i < pixels.length; i++) {
                if (pixels[i]) this._drawHandle(ctx, pixels[i], isSelected, false);
            }
            // Midpoints
            var mids = this.getMidpoints(pixels);
            for (var mi = 0; mi < mids.length; mi++) {
                if (mids[mi]) this._drawHandle(ctx, mids[mi], isSelected, true);
            }
            // 8-dir resize handles when selected
            if (isSelected && pixels.length >= 2) {
                var rHandles = this.getResizeHandles(pixels);
                if (rHandles) {
                    var keys = ['NW','N','NE','W','E','SW','S','SE'];
                    for (var ki = 0; ki < keys.length; ki++) {
                        var h = rHandles[keys[ki]];
                        if (h) this._drawHandle(ctx, h, true, false);
                    }
                }
            }
        }

        _drawHandle(ctx, p, isSelected, isMidpoint) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            if (isMidpoint) {
                var s = 5;
                ctx.beginPath(); ctx.moveTo(p.x,p.y-s); ctx.lineTo(p.x+s,p.y);
                ctx.lineTo(p.x,p.y+s); ctx.lineTo(p.x-s,p.y); ctx.closePath();
            } else {
                ctx.beginPath(); ctx.arc(p.x,p.y,isSelected?5:4,0,Math.PI*2);
            }
            ctx.fill(); ctx.strokeStyle='#2962ff'; ctx.lineWidth=1; ctx.stroke();
        }

        translate(dx, dy, chartState) {
            if (this.locked||!chartState||!this.coords) return;
            this.coords = this.coords.map(function(c){
                var p = chartState.coordToPixel(c);
                return p ? (chartState.pixelToCoord(p.x+dx,p.y+dy)||c) : c;
            });
            this._invalidateCache();
        }

        hitTestHandle(pos, chartState) {
            var def = this.getObjectDef();
            var isEmoji = def && (def.geometry === RichGeometry.EMOJI || this.constructor.name === 'EmojiDrawing' || (def.defaults && def.defaults.emoji));
            if (!isEmoji) return null;

            var pixels = this.getPixels ? this.getPixels(chartState) : [];
            if (!pixels || pixels.length === 0 || !pixels[0]) return null;
            var p = pixels[0];
            var size = (this.style && this.style.fontSize) || (def.defaults && def.defaults.fontSize) || 28;
            var half = size / 2 + 5;
            var threshold = 10;

            var handles = {
                'topLeft':     { x: p.x - half, y: p.y - half },
                'topRight':    { x: p.x + half, y: p.y - half },
                'bottomLeft':  { x: p.x - half, y: p.y + half },
                'bottomRight': { x: p.x + half, y: p.y + half }
            };

            for (var key in handles) {
                var h = handles[key];
                var dist = Math.hypot(pos.x - h.x, pos.y - h.y);
                if (dist <= threshold) {
                    return key;
                }
            }
            return null;
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX, pixelY) {
            var def = this.getObjectDef();
            var isEmoji = def && (def.geometry === RichGeometry.EMOJI || this.constructor.name === 'EmojiDrawing' || (def.defaults && def.defaults.emoji));
            
            if (isEmoji) {
                var pixels = this.getPixels ? this.getPixels(chartState) : [];
                if (!pixels || pixels.length === 0 || !pixels[0]) return;
                var p = pixels[0];

                if (pixelY === undefined && chartState && chartState.priceToY) {
                    pixelY = chartState.priceToY(newPrice);
                }
                if (pixelX === undefined || pixelY === undefined) return;

                var dx = Math.abs(pixelX - p.x);
                var dy = Math.abs(pixelY - p.y);
                var maxDist = Math.max(dx, dy);
                var newSize = Math.round(Math.max(14, Math.min(240, (maxDist - 5) * 2)));

                this.style.fontSize = newSize;
                if (this.model) {
                    if (!this.model.style) this.model.style = {};
                    this.model.style.fontSize = newSize;
                }
                this._invalidateCache();

                // Synchronize Style Panel if visible
                var sizeSlider = document.getElementById('sp-emoji-size');
                if (sizeSlider) sizeSlider.value = newSize;
                var sizeVal = document.getElementById('sp-emoji-size-val');
                if (sizeVal) sizeVal.textContent = newSize + 'px';
                if (window.toolManager && window.toolManager.stylePanel && typeof window.toolManager.stylePanel._updateEmojiPresetActive === 'function') {
                    window.toolManager.stylePanel._updateEmojiPresetActive(newSize);
                }
                return;
            }

            super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
            this._invalidateCache();
        }

        // Double-click handler for text editing
        onDoubleClick(pixelPos, chartState) {
            var engine = window._drawingEngine;
            if (!engine || !engine.textEditor) return;
            var id = this.model ? this.model.id : null;
            if (!id) return;
            engine.textEditor.edit(id, this, pixelPos);
        }
    }

    // --- Rich Object Subclasses ---
    // Group 1: Text
    class TextNote extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.text_note; } }
    
    class AnchoredText extends BaseRichDrawing {
        constructor(startPos, chartState, options = {}) {
            super(startPos, null, options);
            if (chartState) {
                var vp = chartState.viewport || {};
                var w = vp.width || 800;
                var h = vp.height || 500;
                this.coords = [{ xRel: startPos.x / w, yRel: startPos.y / h }];
            }
        }
        getObjectDef() { return OBJECT_DEFS.anchored_text; }
        
        translate(dx, dy, chartState) {
            if (this.locked || !chartState || !this.coords || !this.coords[0]) return;
            var vp = chartState.viewport || {};
            var w = vp.width || 800;
            var h = vp.height || 500;
            var p = chartState.coordToPixel(this.coords[0]);
            if (p) {
                var newX = p.x + dx;
                var newY = p.y + dy;
                this.coords[0] = { xRel: newX / w, yRel: newY / h };
            }
            this._invalidateCache();
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;
            if (idx < 0 || idx >= this.coords.length) return;
            var vp = chartState.viewport || {};
            var w = vp.width || 800;
            var h = vp.height || 500;
            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            this.coords[idx] = { xRel: pixelX / w, yRel: pixelY / h };
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }
    }
    
    class Callout extends BaseRichDrawing {
        getObjectDef() { return OBJECT_DEFS.callout; }
        
        draw(ctx, chartState, isSelected) {
            if (!chartState || !chartState.coordToPixel) return;
            if (this.coords.length === 1 && this.currentPos) {
                var p1 = chartState.coordToPixel(this.coords[0]);
                if (p1) {
                    var p2 = this.currentPos;
                    var text = this.text || (this.model && this.model.content && this.model.content.plain) || 'Callout';
                    var fs = this.style.fontSize || 13;
                    var ff = this.style.fontFamily || '-apple-system, Roboto, sans-serif';
                    var color = this.style.color || '#d1d4dc';
                    var bg = this.style.background || this.style.fillColor || '#2a2e39';
                    var borderColor = this.style.borderColor || this.style.strokeColor || '#5d606b';
                    var r = this.style.cornerRadius || 6;

                    ctx.save();
                    ctx.font = (this.style.bold ? 'bold ' : '') + (this.style.italic ? 'italic ' : '') + fs + 'px ' + ff;

                    var lines = text.split('\n');
                    var maxW = 0;
                    for (var i = 0; i < lines.length; i++) {
                        var w = ctx.measureText(lines[i]).width;
                        if (w > maxW) maxW = w;
                    }
                    var lineH = fs * 1.3;
                    var pad = 8;
                    var bw = maxW + pad * 2;
                    var bh = lines.length * lineH + pad * 2;

                    var bx = p2.x - bw / 2;
                    var by = p2.y - bh / 2;

                    ctx.beginPath();
                    ctx.moveTo(p1.x, p1.y);
                    
                    var ix = p2.x;
                    var iy = p2.y;
                    if (p1.x < bx) ix = bx;
                    else if (p1.x > bx + bw) ix = bx + bw;
                    if (p1.y < by) iy = by;
                    else if (p1.y > by + bh) iy = by + bh;
                    ctx.lineTo(ix, iy);

                    ctx.strokeStyle = borderColor;
                    ctx.lineWidth = this.style.width || 1;
                    ctx.globalAlpha = 0.5;
                    ctx.stroke();

                    ctx.fillStyle = bg;
                    ctx.globalAlpha = 0.4;
                    ctx.beginPath();
                    ctx.roundRect(bx, by, bw, bh, r);
                    ctx.fill();

                    ctx.strokeStyle = borderColor;
                    ctx.lineWidth = this.style.width || 1;
                    ctx.globalAlpha = 0.6;
                    ctx.beginPath();
                    ctx.roundRect(bx, by, bw, bh, r);
                    ctx.stroke();

                    ctx.fillStyle = color;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'top';
                    ctx.globalAlpha = 0.6;
                    for (var i = 0; i < lines.length; i++) {
                        ctx.fillText(lines[i], p2.x, by + pad + i * lineH);
                    }

                    ctx.restore();
                }
                return;
            }
            super.draw(ctx, chartState, isSelected);
        }
    }
    
    class Balloon extends Callout { getObjectDef() { return OBJECT_DEFS.balloon; } }
    class ArrowLabel extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.arrow_label; } }
    // Group 2: Icons & Emojis
    class EmojiDrawing extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.emoji; } }
    class IconDrawing extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.icon; } }
    class SymbolDrawing extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.symbol; } }
    // Group 3: Images
    class ImageDrawing extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.image; } }
    class WatermarkDrawing extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.watermark; } }
    class LogoDrawing extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.logo; } }
    // Group 4: Stickers
    class StickerBuy extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_buy; } }
    class StickerSell extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_sell; } }
    class StickerLong extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_long; } }
    class StickerShort extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_short; } }
    class StickerTarget extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_target; } }
    class StickerStop extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_stop; } }
    class StickerStar extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_star; } }
    class StickerPin extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_pin; } }
    class StickerCheck extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_check; } }
    class StickerWarning extends BaseRichDrawing { getObjectDef() { return OBJECT_DEFS.sticker_warning; } }

    class Rectangle extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            ctx.beginPath();
            ctx.rect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
            this.applyFillStyle(ctx);
            ctx.fill();
            this.applyStyle(ctx);
            ctx.stroke();
        }

        // Drag-based placement: coords[1] tracks the drag position
        update(pos, chartState) {
            if (!chartState) return;
            var c = chartState.pixelToCoord(pos.x, pos.y);
            if (c && c.logical != null && c.price != null) {
                if (this.coords.length < 2) this.coords.push({ logical: c.logical, price: c.price });
                else this.coords[1] = { logical: c.logical, price: c.price };
            }
            this.currentPos = pos;
        }

        isValid() { return this.coords.length >= 2; }

        // Phase 2: 4 corner anchors
        getAnchorPoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            return [
                pixels[0],
                { x: pixels[0].x, y: pixels[1].y },
                pixels[1],
                { x: pixels[1].x, y: pixels[0].y }
            ];
        }

        // Phase 2: 4 edge midpoints (spec: 4 corners + 4 edge midpoints, no center)
        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var cx = (pixels[0].x + pixels[1].x) / 2;
            var cy = (pixels[0].y + pixels[1].y) / 2;
            return [
                { x: cx, y: pixels[0].y },          // top edge
                { x: cx, y: pixels[1].y },          // bottom edge
                { x: pixels[0].x, y: cy },          // left edge
                { x: pixels[1].x, y: cy }           // right edge
            ];
        }

        // Phase 2: Fill shape as polygon (4 corners clockwise from top-left)
        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            return [
                { x: Math.min(pixels[0].x, pixels[1].x), y: Math.min(pixels[0].y, pixels[1].y) },
                { x: Math.max(pixels[0].x, pixels[1].x), y: Math.min(pixels[0].y, pixels[1].y) },
                { x: Math.max(pixels[0].x, pixels[1].x), y: Math.max(pixels[0].y, pixels[1].y) },
                { x: Math.min(pixels[0].x, pixels[1].x), y: Math.max(pixels[0].y, pixels[1].y) }
            ];
        }

        // Border hit-testing: 4 edge segments
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var x0 = Math.min(pixels[0].x, pixels[1].x);
            var x1 = Math.max(pixels[0].x, pixels[1].x);
            var y0 = Math.min(pixels[0].y, pixels[1].y);
            var y1 = Math.max(pixels[0].y, pixels[1].y);
            return [
                { p1: { x: x0, y: y0 }, p2: { x: x1, y: y0 } },
                { p1: { x: x1, y: y0 }, p2: { x: x1, y: y1 } },
                { p1: { x: x1, y: y1 }, p2: { x: x0, y: y1 } },
                { p1: { x: x0, y: y1 }, p2: { x: x0, y: y0 } }
            ];
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            var logical = null, price = newPrice;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    logical = snapInfo.logical;
                    price = snapInfo.price;
                }
            }
            if (logical == null) {
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
                if (mapper) logical = mapper(pixelX);
            }
            if (logical == null) return;

            // Midpoint drags resize a single axis; anchor drags resize both axes.
            if (draggedHandle.hitType === 'midpoint') {
                if (idx === 0) {                 // top edge — move top, keep bottom
                    this.coords[0].price = price;
                } else if (idx === 1) {          // bottom edge — move bottom, keep top
                    this.coords[1].price = price;
                } else if (idx === 2) {          // left edge — move left, keep right
                    this.coords[0].logical = logical;
                } else if (idx === 3) {          // right edge — move right, keep left
                    this.coords[1].logical = logical;
                }
            } else {
                // Anchor indices mapping based on getAnchorPoints:
                // 0: p1, 1: {p1.x, p2.y}, 2: p2, 3: {p2.x, p1.y}
                if (idx === 0) { // Top/Left (p1)
                    this.coords[0] = { logical: logical, price: price };
                } else if (idx === 1) { // Bottom/Left
                    this.coords[0].logical = logical;
                    this.coords[1].price = price;
                } else if (idx === 2) { // Bottom/Right (p2)
                    this.coords[1] = { logical: logical, price: price };
                } else if (idx === 3) { // Top/Right
                    this.coords[1].logical = logical;
                    this.coords[0].price = price;
                }
            }
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }
    }

    class Circle extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const radius = Math.sqrt(Math.pow(p2.x - p1.x, 2) + Math.pow(p2.y - p1.y, 2));
            ctx.beginPath();
            ctx.arc(p1.x, p1.y, radius, 0, Math.PI * 2);
            this.applyFillStyle(ctx);
            ctx.fill();
            this.applyStyle(ctx);
            ctx.stroke();
        }

        // Drag-based placement: coords[0]=center, coords[1]=radius point
        update(pos, chartState) {
            if (!chartState) return;
            var c = chartState.pixelToCoord(pos.x, pos.y);
            if (c && c.logical != null && c.price != null) {
                if (this.coords.length < 2) this.coords.push({ logical: c.logical, price: c.price });
                else this.coords[1] = { logical: c.logical, price: c.price };
            }
            this.currentPos = pos;
        }

        isValid() { return this.coords.length >= 2; }

        // Center handle (0) moves the whole circle; radius handle (1) resizes it.
        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;
            if (idx < 0 || idx >= this.coords.length) return;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            var logical = null, price = newPrice;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    logical = snapInfo.logical;
                    price = snapInfo.price;
                }
            }
            if (logical == null) {
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
                if (mapper) logical = mapper(pixelX);
            }
            if (logical == null) return;

            if (idx === 0) {
                // Dragging center: translate both coords to preserve the radius
                var dl = logical - this.coords[0].logical;
                var dp = price - this.coords[0].price;
                this.coords[0] = { logical: logical, price: price };
                if (this.coords[1]) {
                    this.coords[1] = { logical: this.coords[1].logical + dl, price: this.coords[1].price + dp };
                }
            } else {
                this.coords[1] = { logical: logical, price: price };
            }
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }

        // Edge segments approximate the circle as a 32-gon for border hit-testing
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var cx = pixels[0].x, cy = pixels[0].y;
            var r = Math.sqrt(Math.pow(pixels[1].x - cx, 2) + Math.pow(pixels[1].y - cy, 2));
            var segs = [];
            for (var a = 0; a < 32; a++) {
                var ang1 = (a / 32) * Math.PI * 2;
                var ang2 = ((a + 1) / 32) * Math.PI * 2;
                segs.push({
                    p1: { x: cx + r * Math.cos(ang1), y: cy + r * Math.sin(ang1) },
                    p2: { x: cx + r * Math.cos(ang2), y: cy + r * Math.sin(ang2) }
                });
            }
            return segs;
        }

        // Fill shape: approximate circle as 32-gon for hit-testing
        getFillShape(pixels) {
            if (!pixels || pixels.length < 1) return null;
            var cx = pixels[0].x, cy = pixels[0].y;
            var r = pixels[1] ? Math.sqrt(Math.pow(pixels[1].x - cx, 2) + Math.pow(pixels[1].y - cy, 2)) : 10;
            var verts = [];
            for (var a = 0; a < 32; a++) {
                var ang = (a / 32) * Math.PI * 2;
                verts.push({ x: cx + r * Math.cos(ang), y: cy + r * Math.sin(ang) });
            }
            return verts;
        }
    }

    class TextDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.text = "Text";
            this._userEdited = false;
        }
        
        getFillShape(pixels) {
            if (!pixels || pixels.length < 1) return null;
            var p1 = pixels[0];
            var fontSize = this.style.fontSize || 14;
            var align = this.style.align || 'left';
            
            var text = this.text || '';
            var lines = text.split('\n');
            var maxW = 0;
            for (var i = 0; i < lines.length; i++) {
                var w = lines[i].length * fontSize * 0.6; // approximation
                if (w > maxW) maxW = w;
            }
            var lineH = fontSize * 1.3;
            var pad = 8;
            var bw = maxW + pad * 2;
            var bh = lines.length * lineH + pad * 2;

            var xOffset = 0;
            if (align === 'center') {
                xOffset = -bw / 2;
            } else if (align === 'right') {
                xOffset = -bw;
            }

            var bx = p1.x + xOffset;
            var by = p1.y - bh / 2;

            return [
                { x: bx, y: by },
                { x: bx + bw, y: by },
                { x: bx + bw, y: by + bh },
                { x: bx, y: by + bh }
            ];
        }
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 1;
        }
        isValid() { return this.text && this.text.length > 0 && (this._userEdited || this.text !== "Text"); }
        
        draw(ctx, chartState, isSelected) {
            if (!this.coords || this.coords.length === 0) return;
            const p1 = chartState.coordToPixel(this.coords[0]);
            if (!p1) return;

            var text = this.text || '';
            var color = this.style.color || '#d1d4dc';
            var fontSize = this.style.fontSize || 14;
            var fontFamily = this.style.fontFamily || '-apple-system, Roboto, sans-serif';
            var align = this.style.align || 'left';
            var hasBg = !!this.style.fillColor || !!this.style.background;
            var bg = this.style.fillColor || this.style.background || '#1e222d';
            var hasBorder = !!this.style.strokeColor || !!this.style.border;
            var borderColor = this.style.strokeColor || this.style.border || '#5d606b';

            ctx.save();
            ctx.font = (this.style.bold ? 'bold ' : '') + (this.style.italic ? 'italic ' : '') + fontSize + 'px ' + fontFamily;
            
            var lines = text.split('\n');
            var maxW = 0;
            for (var i = 0; i < lines.length; i++) {
                var w = ctx.measureText(lines[i]).width;
                if (w > maxW) maxW = w;
            }
            var lineH = fontSize * 1.3;
            var pad = 8;
            var bw = maxW + pad * 2;
            var bh = lines.length * lineH + pad * 2;

            var xOffset = 0;
            if (align === 'center') {
                xOffset = -bw / 2;
            } else if (align === 'right') {
                xOffset = -bw;
            }

            var bx = p1.x + xOffset;
            var by = p1.y - bh / 2;

            if (hasBg) {
                ctx.fillStyle = bg;
                ctx.globalAlpha = this.style.fillOpacity != null ? this.style.fillOpacity : 0.9;
                ctx.fillRect(bx, by, bw, bh);
            }

            if (hasBorder) {
                ctx.strokeStyle = borderColor;
                ctx.lineWidth = this.style.width || 1;
                ctx.globalAlpha = this.style.opacity != null ? this.style.opacity : 1;
                ctx.strokeRect(bx, by, bw, bh);
            }

            ctx.fillStyle = color;
            ctx.globalAlpha = this.style.opacity != null ? this.style.opacity : 1;
            ctx.textAlign = align;
            ctx.textBaseline = 'top';

            var tx = p1.x;
            if (align === 'left') {
                tx = p1.x + pad;
            } else if (align === 'right') {
                tx = p1.x - pad;
            }

            for (var i = 0; i < lines.length; i++) {
                ctx.fillText(lines[i], tx, by + pad + i * lineH);
            }

            if (isSelected) {
                ctx.strokeStyle = '#2962ff';
                ctx.lineWidth = 1.5;
                ctx.strokeRect(bx - 2, by - 2, bw + 4, bh + 4);

                ctx.fillStyle = '#2962ff';
                ctx.beginPath();
                ctx.arc(p1.x, p1.y, 4, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }
        
        onDoubleClick(pixelPos, chartState) {
            var engine = window._drawingEngine;
            if (!engine || !engine.textEditor) return;
            var id = this.model ? this.model.id : null;
            if (!id) return;
            engine.textEditor.edit(id, this, pixelPos);
            this._userEdited = true;
        }
    }



    class BrushDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            if (chartState) {
                var c = chartState.pixelToCoord(startPos.x, startPos.y);
                if (c) this.coords = [{ logical: c.logical, price: c.price, time: c.time }];  // PHASE A: keep canonical anchor
            }
            // Minimum pixel distance between stored points (never every mouse event)
            this.minSampleDist = 3;
            this._lastPixel = null;
        }

        addPoint(pos, chartState) {
            return true;
        }

        // Drag-based: append points only when the pointer has moved at least
        // minSampleDist px from the last stored point.
        update(pos, chartState) {
            if (!chartState) return;
            var pixel = { x: pos.x, y: pos.y };
            if (this._lastPixel == null) {
                var first = this.getPixels(chartState);
                if (first && first.length > 0) this._lastPixel = { x: first[0].x, y: first[0].y };
                else this._lastPixel = pixel;
            }
            var dx = pixel.x - this._lastPixel.x;
            var dy = pixel.y - this._lastPixel.y;
            if (Math.sqrt(dx * dx + dy * dy) >= this.minSampleDist) {
                var c = chartState.pixelToCoord(pos.x, pos.y);
                if (c && c.logical != null && c.price != null) {
                    this.coords.push({ logical: c.logical, price: c.price, time: c.time });  // PHASE A: keep canonical anchor
                    this._lastPixel = pixel;
                }
            }
            this.currentPos = pos;
        }

        // Smooth hand-drawn stroke: quadratic Bézier curves through segment midpoints
        draw(ctx, chartState, isSelected, isHovered) {
            const pixels = this.getPixels(chartState);
            if (pixels.length < 2) return;

            ctx.beginPath();
            ctx.moveTo(pixels[0].x, pixels[0].y);
            if (pixels.length === 2) {
                ctx.lineTo(pixels[1].x, pixels[1].y);
            } else {
                for (let i = 1; i < pixels.length - 1; i++) {
                    const midX = (pixels[i].x + pixels[i + 1].x) / 2;
                    const midY = (pixels[i].y + pixels[i + 1].y) / 2;
                    ctx.quadraticCurveTo(pixels[i].x, pixels[i].y, midX, midY);
                }
                ctx.lineTo(pixels[pixels.length - 1].x, pixels[pixels.length - 1].y);
            }
            this.applyStyle(ctx);
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.stroke();
            ctx.globalAlpha = 1.0;
        }

        // Hit testing: stroke segments (edge/body hit → whole stroke draggable)
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var segs = [];
            for (var i = 0; i < pixels.length - 1; i++) {
                if (pixels[i] && pixels[i + 1]) segs.push({ p1: pixels[i], p2: pixels[i + 1] });
            }
            return segs;
        }

        // No point editing by default — whole stroke draggable via body hit
        getAnchorPoints(pixels) { return []; }
        getMidpoints(pixels) { return []; }

        isValid() { return this.coords.length >= 2; }
    }

    class ArrowMarker extends TwoPointDrawing {
        renderShape(ctx, p1, p2, isHovered) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            this.applyStyle(ctx);
            ctx.stroke();
            const angle = Math.atan2(p2.y - p1.y, p2.x - p1.x);
            const headLen = 12;
            ctx.beginPath();
            ctx.moveTo(p2.x, p2.y);
            ctx.lineTo(p2.x - headLen * Math.cos(angle - 0.4), p2.y - headLen * Math.sin(angle - 0.4));
            ctx.moveTo(p2.x, p2.y);
            ctx.lineTo(p2.x - headLen * Math.cos(angle + 0.4), p2.y - headLen * Math.sin(angle + 0.4));
            this.applyStyle(ctx);
            ctx.stroke();

            // Support optional text label
            var text = this.text || (this.model && this.model.content && this.model.content.plain) || '';
            if (text) {
                var mx = (p1.x + p2.x) / 2;
                var my = (p1.y + p2.y) / 2;
                ctx.save();
                ctx.translate(mx, my);
                ctx.rotate(angle);
                ctx.font = '12px Arial';
                ctx.fillStyle = this.style.color || '#d1d4dc';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'bottom';
                ctx.fillText(text, 0, -4);
                ctx.restore();
            }
        }
        onDoubleClick(pixelPos, chartState) {
            var engine = window._drawingEngine;
            if (!engine || !engine.textEditor) return;
            var id = this.model ? this.model.id : null;
            if (!id) return;
            engine.textEditor.edit(id, this, pixelPos);
        }
    }

    function formatPrice(price) {
        if (price === null || price === undefined || isNaN(price)) return '';
        var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
        if (series && typeof series.options === 'function') {
            var pf = series.options().priceFormat;
            if (pf && pf.precision !== undefined) {
                return price.toFixed(pf.precision);
            }
        }
        return price.toFixed(2);
    }

    class PriceLabel extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
        }
        addPoint(pos, chartState) {
            return true;
        }
        isValid() { return this.coords.length >= 1 && this.coords[0].price !== undefined; }
        
        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            super.updateHandle(draggedHandle, newPrice, chartState, pixelX);
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }

        getAnchorPoints(pixels) {
            return pixels || [];
        }

        draw(ctx, chartState, isSelected) {
            if (!this.coords || this.coords.length === 0) return;
            const p1 = chartState.coordToPixel(this.coords[0]);
            if (!p1) return;
            
            const priceVal = this.coords[0].price;
            const priceText = formatPrice(priceVal);
            const label = `₹${priceText}`;
            
            ctx.save();
            ctx.font = 'bold 12px Arial';
            const metrics = ctx.measureText(label);
            const padX = 8;
            const padY = 5;
            const bw = metrics.width + padX * 2;
            const bh = 20 + padY * 2;

            var boxY = p1.y - bh - 8;
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p1.x, p1.y - 8);
            ctx.strokeStyle = '#5d606b';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            ctx.fillStyle = '#37474f';
            ctx.beginPath();
            ctx.roundRect(p1.x - bw / 2, boxY, bw, bh, 4);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(label, p1.x, boxY + bh / 2 + 1);

            if (isSelected) {
                ctx.strokeStyle = '#2962ff';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.roundRect(p1.x - bw / 2 - 1, boxY - 1, bw + 2, bh + 2, 4);
                ctx.stroke();
                
                ctx.fillStyle = '#2962ff';
                ctx.beginPath();
                ctx.arc(p1.x, p1.y, 4, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }

        getFillShape(pixels) {
            if (!pixels || pixels.length < 1) return null;
            var p1 = pixels[0];
            var priceVal = this.coords[0].price;
            var priceText = formatPrice(priceVal);
            var label = `₹${priceText}`;
            var bw = (label.length * 7) + 16;
            var bh = 30;
            var boxY = p1.y - bh - 8;

            return [
                { x: p1.x - bw / 2, y: boxY },
                { x: p1.x + bw / 2, y: boxY },
                { x: p1.x + bw / 2, y: boxY + bh },
                { x: p1.x - bw / 2, y: boxY + bh }
            ];
        }
    }

    class HighlighterDrawing extends BrushDrawing {
        // Highlighter is a transparent brush: same freehand sampling, but rendered
        // with low opacity and (if supported) multiply blend mode.
        draw(ctx, chartState, isSelected, isHovered) {
            const pixels = this.getPixels(chartState);
            if (pixels.length < 2) return;

            ctx.save();
            // Multiply blend mode if supported, else fall back to plain alpha
            var prevOp = ctx.globalCompositeOperation;
            ctx.globalCompositeOperation = 'multiply';
            if (ctx.globalCompositeOperation !== 'multiply') {
                ctx.globalCompositeOperation = prevOp;
            }

            ctx.beginPath();
            ctx.moveTo(pixels[0].x, pixels[0].y);
            if (pixels.length === 2) {
                ctx.lineTo(pixels[1].x, pixels[1].y);
            } else {
                for (let i = 1; i < pixels.length - 1; i++) {
                    const midX = (pixels[i].x + pixels[i + 1].x) / 2;
                    const midY = (pixels[i].y + pixels[i + 1].y) / 2;
                    ctx.quadraticCurveTo(pixels[i].x, pixels[i].y, midX, midY);
                }
                ctx.lineTo(pixels[pixels.length - 1].x, pixels[pixels.length - 1].y);
            }
            this.applyStyle(ctx);
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.stroke();
            ctx.globalAlpha = 1.0;
            ctx.restore();
        }

        isValid() { return this.coords.length >= 2; }
    }

    // --- NEW DRAWING CLASSES ---

    // (HorizontalLine and VerticalLine removed due to duplicate definition earlier)

    class Ellipse extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const cx = (p1.x + p2.x) / 2;
            const cy = (p1.y + p2.y) / 2;
            const rx = Math.abs(p2.x - p1.x) / 2;
            const ry = Math.abs(p2.y - p1.y) / 2;
            ctx.beginPath();
            ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
            this.applyFillStyle(ctx);
            ctx.fill();
            this.applyStyle(ctx);
            ctx.stroke();
        }

        // Drag-based placement: coords[1] tracks the drag position (bounding box)
        update(pos, chartState) {
            if (!chartState) return;
            var c = chartState.pixelToCoord(pos.x, pos.y);
            if (c && c.logical != null && c.price != null) {
                if (this.coords.length < 2) this.coords.push({ logical: c.logical, price: c.price });
                else this.coords[1] = { logical: c.logical, price: c.price };
            }
            this.currentPos = pos;
        }

        isValid() { return this.coords.length >= 2; }

        // 4 corner anchors
        getAnchorPoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            return [
                pixels[0],
                { x: pixels[0].x, y: pixels[1].y },
                pixels[1],
                { x: pixels[1].x, y: pixels[0].y }
            ];
        }

        // 4 edge midpoints (top/bottom/left/right)
        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var cx = (pixels[0].x + pixels[1].x) / 2;
            var cy = (pixels[0].y + pixels[1].y) / 2;
            return [
                { x: cx, y: pixels[0].y },
                { x: cx, y: pixels[1].y },
                { x: pixels[0].x, y: cy },
                { x: pixels[1].x, y: cy }
            ];
        }

        // Fill shape: approximate ellipse as 32-gon
        getFillShape(pixels) {
            if (!pixels || pixels.length < 2) return null;
            const cx = (pixels[0].x + pixels[1].x) / 2;
            const cy = (pixels[0].y + pixels[1].y) / 2;
            const rx = Math.abs(pixels[1].x - pixels[0].x) / 2;
            const ry = Math.abs(pixels[1].y - pixels[0].y) / 2;
            var verts = [];
            for (var a = 0; a < 32; a++) {
                var ang = (a / 32) * Math.PI * 2;
                verts.push({ x: cx + rx * Math.cos(ang), y: cy + ry * Math.sin(ang) });
            }
            return verts;
        }

        // Border hit-testing: 4 bbox edge segments
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var x0 = Math.min(pixels[0].x, pixels[1].x);
            var x1 = Math.max(pixels[0].x, pixels[1].x);
            var y0 = Math.min(pixels[0].y, pixels[1].y);
            var y1 = Math.max(pixels[0].y, pixels[1].y);
            return [
                { p1: { x: x0, y: y0 }, p2: { x: x1, y: y0 } },
                { p1: { x: x1, y: y0 }, p2: { x: x1, y: y1 } },
                { p1: { x: x1, y: y1 }, p2: { x: x0, y: y1 } },
                { p1: { x: x0, y: y1 }, p2: { x: x0, y: y0 } }
            ];
        }

        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            var logical = null, price = newPrice;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    logical = snapInfo.logical;
                    price = snapInfo.price;
                }
            }
            if (logical == null) {
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
                if (mapper) logical = mapper(pixelX);
            }
            if (logical == null) return;

            if (draggedHandle.hitType === 'midpoint') {
                if (idx === 0) {                 // top edge
                    this.coords[0].price = price;
                } else if (idx === 1) {          // bottom edge
                    this.coords[1].price = price;
                } else if (idx === 2) {          // left edge
                    this.coords[0].logical = logical;
                } else if (idx === 3) {          // right edge
                    this.coords[1].logical = logical;
                }
            } else {
                if (idx === 0) {
                    this.coords[0] = { logical: logical, price: price };
                } else if (idx === 1) {
                    this.coords[0].logical = logical;
                    this.coords[1].price = price;
                } else if (idx === 2) {
                    this.coords[1] = { logical: logical, price: price };
                } else if (idx === 3) {
                    this.coords[1].logical = logical;
                    this.coords[0].price = price;
                }
            }
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }
    }

    class TriangleShape extends BaseDrawing {
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 3;
        }
        draw(ctx, chartState, isSelected, isHovered) {
            const pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            ctx.beginPath();
            ctx.moveTo(pixels[0].x, pixels[0].y);
            for (let i = 1; i < pixels.length; i++) {
                ctx.lineTo(pixels[i].x, pixels[i].y);
            }
            if (pixels.length < 3 && this.currentPos) {
                ctx.lineTo(this.currentPos.x, this.currentPos.y);
            }
            if (pixels.length >= 2) ctx.closePath();
            // Fill first, stroke on top
            if (this.style.fillColor) {
                this.applyFillStyle(ctx);
                ctx.fill();
            }
            this.applyStyle(ctx);
            ctx.stroke();
            ctx.globalAlpha = 1.0;
        }
        drawHandle(ctx, p, isSelected) {
            ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, isSelected ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#ff9800';
            ctx.stroke();
        }
        isValid() { return this.coords.length >= 3; }
        getAnchorPoints(pixels) { return pixels || []; }
        // No midpoint handles — 3 editable corners only (avoids anchor-index collision)
        getMidpoints(pixels) { return []; }
        getFillShape(pixels) {
            if (!pixels || pixels.length < 3) return null;
            return pixels;
        }
        // Edge hit-testing: all 3 sides including the closing edge
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 3) return [];
            var segs = [];
            for (var i = 0; i < pixels.length; i++) {
                var next = (i + 1) % pixels.length;
                if (pixels[i] && pixels[next]) {
                    segs.push({ p1: pixels[i], p2: pixels[next] });
                }
            }
            return segs;
        }
        // Each corner is editable via the base anchor-handle flow
        updateHandle(draggedHandle, newPrice, chartState, pixelX) {
            if (this.locked || !draggedHandle || draggedHandle.handleIndex === undefined) return;
            var idx = draggedHandle.handleIndex;
            if (idx < 0 || idx >= this.coords.length) return;

            var pixelY = chartState.priceToY ? chartState.priceToY(newPrice) : 0;
            var logical = null, price = newPrice;
            if (window.getSnappedAnchor) {
                var snapInfo = window.getSnappedAnchor(pixelX, pixelY, chartState);
                if (snapInfo) {
                    logical = snapInfo.logical;
                    price = snapInfo.price;
                }
            }
            if (logical == null) {
                var mapper = (chartState && (chartState.xToLogical || chartState.xToTime)) ? (chartState.xToLogical || chartState.xToTime).bind(chartState) : null;
                if (mapper) logical = mapper(pixelX);
            }
            if (logical == null) return;
            this.coords[idx] = { logical: logical, price: price };
            if (typeof this._invalidateCache === 'function') this._invalidateCache();
        }
    }

    class TrendAngle extends TwoPointDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.showAxisLabels = true;
        }

        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return null;
            var clipper = new GeometryClipper();
            var clipped;
            if (this.style.extendLeft && this.style.extendRight) clipped = clipper.clipInfiniteLine(pixels[0], pixels[1]);
            else if (this.style.extendRight) clipped = clipper.clipRay(pixels[0], pixels[1]);
            else if (this.style.extendLeft) clipped = clipper.clipRay(pixels[1], pixels[0]);
            else clipped = clipper.clipSegment(pixels[0], pixels[1]);
            if (!clipped) return [];
            return [{ p1: clipped.p1, p2: clipped.p2 }];
        }

        getMidpoints(pixels) {
            if (!pixels || pixels.length < 2) return [];
            return [GeometryUtils.midpoint(pixels[0], pixels[1])];
        }

        _computeDataAngle() {
            if (!this.coords || this.coords.length < 2) return 0;
            var c1 = this.coords[0];
            var c2 = this.coords[1];
            var dl = c2.logical - c1.logical;
            var dp = c2.price - c1.price;
            if (dl === 0) return dp >= 0 ? 90 : -90;
            return Math.atan2(dp, dl) * (180 / Math.PI);
        }

        _drawArc(ctx, p1, p2) {
            // Arc uses pixel angle for visual alignment with line on screen
            var pixelDx = p2.x - p1.x;
            var pixelDy = p2.y - p1.y;
            if (Math.abs(pixelDx) < 1 && Math.abs(pixelDy) < 1) return;
            var pixelAngle = Math.atan2(pixelDy, pixelDx);
            var arcRadius = 24;

            ctx.save();
            ctx.strokeStyle = this.style.color || '#4caf50';
            ctx.lineWidth = 1.5;
            ctx.globalAlpha = 0.7;
            ctx.setLineDash([]);

            // Arc spans from horizontal-right (0°) to the line's pixel angle
            var arcEnd = pixelAngle;
            ctx.beginPath();
            if (arcEnd >= 0) {
                ctx.arc(p1.x, p1.y, arcRadius, 0, arcEnd);
            } else {
                ctx.arc(p1.x, p1.y, arcRadius, arcEnd, 0);
            }
            ctx.stroke();

            // Angle label — uses data-coordinate angle (zoom-invariant)
            var labelR = arcRadius + 14;
            var midA = arcEnd / 2;
            var lx = p1.x + labelR * Math.cos(midA);
            var ly = p1.y + labelR * Math.sin(midA);

            ctx.fillStyle = this.style.color || '#4caf50';
            ctx.font = 'bold 12px -apple-system, Roboto, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(this._computeDataAngle().toFixed(1) + '°', lx, ly);

            // Small markers at arc endpoints
            ctx.strokeStyle = this.style.color || '#4caf50';
            ctx.lineWidth = 1;
            ctx.globalAlpha = 0.4;
            var ep1x = p1.x + arcRadius;
            var ep1y = p1.y;
            var ep2x = p1.x + arcRadius * Math.cos(arcEnd);
            var ep2y = p1.y + arcRadius * Math.sin(arcEnd);
            ctx.beginPath(); ctx.moveTo(ep1x - 4, ep1y); ctx.lineTo(ep1x + 4, ep1y); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(ep2x - 4, ep2y); ctx.lineTo(ep2x + 4, ep2y); ctx.stroke();

            ctx.restore();
        }

        _drawInfoLabel(ctx, p1, p2) {
            if (!this.coords || this.coords.length < 2) return;
            var c1 = this.coords[0];
            var c2 = this.coords[1];
            var deltaPrice = c2.price - c1.price;
            var deltaBars = Math.round(c2.logical - c1.logical);
            var percentText = '';
            if (c1.price !== 0) {
                percentText = ((deltaPrice / c1.price) * 100).toFixed(2) + '%';
            }
            var angleText = this._computeDataAngle().toFixed(1) + '°';
            var priceText = (deltaPrice >= 0 ? '+' : '') + deltaPrice.toFixed(2) + (percentText ? ' (' + percentText + ')' : '');
            var barsText = Math.abs(deltaBars) + (Math.abs(deltaBars) === 1 ? ' bar' : ' bars');

            var lines = [angleText, priceText, barsText];
            var maxW = 0;
            ctx.save();
            ctx.font = 'bold 11px -apple-system, Roboto, sans-serif';
            for (var li = 0; li < lines.length; li++) {
                var mw = ctx.measureText(lines[li]).width;
                if (mw > maxW) maxW = mw;
            }
            var pad = 8;
            var lh = 18;
            var bw = maxW + pad * 2;
            var bh = lines.length * lh + pad;

            // Position near the midpoint but offset above the line
            var midX = (p1.x + p2.x) / 2;
            var midY = (p1.y + p2.y) / 2;

            var color = (this.style && this.style.color) || '#4caf50';

            ctx.fillStyle = color + 'E0';
            ctx.beginPath();
            ctx.roundRect(midX + 12, midY - bh / 2, bw, bh, 4);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';
            for (var li2 = 0; li2 < lines.length; li2++) {
                ctx.fillText(lines[li2], midX + 12 + pad, midY - bh / 2 + pad + li2 * lh);
            }

            ctx.restore();
        }

        renderShape(ctx, p1, p2, isHovered) {
            var segments = this.getEdgeSegments([p1, p2]);
            if (!segments || segments.length === 0) return;
            var clipped = segments[0];

            ctx.beginPath();
            ctx.moveTo(clipped.p1.x, clipped.p1.y);
            ctx.lineTo(clipped.p2.x, clipped.p2.y);
            this.applyStyle(ctx);

            if (isHovered) {
                ctx.lineWidth = (this.style.width || 2) + 2;
                ctx.globalAlpha = (this.style.opacity != null ? this.style.opacity : 1) * 0.9;
            }

            ctx.stroke();

            this._drawArc(ctx, p1, p2);
            this._drawInfoLabel(ctx, p1, p2);

            if (this.style.showLabel) this.drawPriceBadge(ctx, p2.x + 6, p2.y, this.getPriceLabel(1));
        }
    }



    // Single-click Long Position - TradingView style spanning candles
    class LongPosition extends BaseDrawing {
        constructor(startPos, chartState, options = {}) {
            super(startPos, chartState, options);
            this.quantity = options.quantity || 10;
            this.riskRewardRatio = options.riskRewardRatio || 2;

            this.initialHalfSize = 40; // Fixed pixel half-size fallback

            var cs = chartState || (window.toolManager && typeof window.toolManager.getChartState === 'function' ? window.toolManager.getChartState() : null) || window.coordinateMapper;

            // Bar-based horizontal width so it stays pinned to candles and scales properly when zooming/panning
            var defaultBars = 4;
            if (options.leftBars !== undefined) {
                this.leftBars = options.leftBars;
            } else if (options.leftOffset && cs && typeof cs.coordToPixel === 'function') {
                var p0 = cs.coordToPixel({ logical: 100, price: this.coords[0] ? this.coords[0].price : 100 });
                var p1 = cs.coordToPixel({ logical: 101, price: this.coords[0] ? this.coords[0].price : 100 });
                var spacing = (p0 && p1 && typeof p0.x === 'number' && typeof p1.x === 'number') ? Math.abs(p1.x - p0.x) : 8;
                this.leftBars = Math.max(1, Math.round(options.leftOffset / (spacing || 8)));
            } else {
                this.leftBars = defaultBars;
            }

            if (options.rightBars !== undefined) {
                this.rightBars = options.rightBars;
            } else if (options.rightOffset && cs && typeof cs.coordToPixel === 'function') {
                var p0 = cs.coordToPixel({ logical: 100, price: this.coords[0] ? this.coords[0].price : 100 });
                var p1 = cs.coordToPixel({ logical: 101, price: this.coords[0] ? this.coords[0].price : 100 });
                var spacing = (p0 && p1 && typeof p0.x === 'number' && typeof p1.x === 'number') ? Math.abs(p1.x - p0.x) : 8;
                this.rightBars = Math.max(1, Math.round(options.rightOffset / (spacing || 8)));
            } else {
                this.rightBars = defaultBars;
            }

            // Asymmetric width offsets fallback (from entry point)
            this.leftOffset = this.initialHalfSize;
            this.rightOffset = this.initialHalfSize;

            if (options.targetPrice !== undefined && options.stopPrice !== undefined) {
                this.targetPrice = options.targetPrice;
                this.stopPrice = options.stopPrice;
                this.entryPrice = options.entryPrice || (this.coords[0] ? this.coords[0].price : 0);
            } else {
                if (this.coords.length >= 1) {
                    this.entryPrice = this.coords[0].price || 0;
                }
                var entryPixelY = null;
                var entryPixelX = null;
                if (startPos && typeof startPos.y === 'number') {
                    entryPixelY = startPos.y;
                    entryPixelX = startPos.x;
                } else if (this.coords.length >= 1 && cs && typeof cs.coordToPixel === 'function') {
                    var p = cs.coordToPixel(this.coords[0]);
                    if (p) {
                        entryPixelY = p.y;
                        entryPixelX = p.x;
                    }
                }

                var calculated = false;
                if (cs && typeof cs.pixelToCoord === 'function' && typeof entryPixelY === 'number' && !isNaN(entryPixelY)) {
                    // Place compact square: target 48px above, stop 24px below
                    var targetCoord = cs.pixelToCoord(entryPixelX || 100, entryPixelY - 48);
                    var stopCoord = cs.pixelToCoord(entryPixelX || 100, entryPixelY + 24);
                    if (targetCoord && typeof targetCoord.price === 'number' && stopCoord && typeof stopCoord.price === 'number') {
                        if (targetCoord.price > this.entryPrice && stopCoord.price < this.entryPrice) {
                            this.targetPrice = targetCoord.price;
                            this.stopPrice = stopCoord.price;
                            var profit = this.targetPrice - this.entryPrice;
                            var risk = this.entryPrice - this.stopPrice;
                            if (risk > 0) {
                                this.riskRewardRatio = Math.round((profit / risk) * 10) / 10 || 2;
                            }
                            calculated = true;
                        }
                    }
                }

                if (!calculated && this.coords.length >= 1) {
                    this.entryPrice = this.coords[0].price || 0;
                    var _riskPct = (options.riskPct !== undefined) ? options.riskPct : 0.015;
                    var _risk = Math.abs(this.entryPrice) * _riskPct;
                    var _reward = _risk * this.riskRewardRatio;
                    // long: stop BELOW entry, target ABOVE entry
                    this.stopPrice = this.entryPrice - _risk;
                    this.targetPrice = this.entryPrice + _reward;
                }
            }
        }

        // Override - complete on single click
        addPoint(pos, chartState) {
            return true; // Always complete immediately
        }

        _getEntryLogical(chartState) {
            var entryCoord = this.coords && this.coords[0];
            if (!entryCoord) return null;
            if (entryCoord.logical !== undefined && entryCoord.logical !== null && !isNaN(entryCoord.logical)) {
                return entryCoord.logical;
            }
            var cs = chartState || (window.toolManager && typeof window.toolManager.getChartState === 'function' ? window.toolManager.getChartState() : null) || window.coordinateMapper;
            if (cs) {
                if (entryCoord.time !== undefined && entryCoord.time !== null && typeof cs.timeToLogical === 'function') {
                    var log = cs.timeToLogical(entryCoord.time);
                    if (log !== null && log !== undefined && !isNaN(log)) {
                        entryCoord.logical = log;
                        return log;
                    }
                }
                var p = cs.coordToPixel ? cs.coordToPixel(entryCoord) : null;
                if (p && typeof cs.pixelToCoord === 'function') {
                    var c = cs.pixelToCoord(p.x, p.y);
                    if (c && c.logical !== null && c.logical !== undefined && !isNaN(c.logical)) {
                        entryCoord.logical = c.logical;
                        return c.logical;
                    }
                }
            }
            return null;
        }

        _getHorizontalBounds(chartState, entryPixel) {
            var entryLogical = this._getEntryLogical(chartState);
            var leftBars = (typeof this.leftBars === 'number' && this.leftBars > 0) ? this.leftBars : 4;
            var rightBars = (typeof this.rightBars === 'number' && this.rightBars > 0) ? this.rightBars : 4;

            if (entryLogical !== null && chartState && typeof chartState.coordToPixel === 'function') {
                var leftP = chartState.coordToPixel({ logical: entryLogical - leftBars, price: this.entryPrice });
                var rightP = chartState.coordToPixel({ logical: entryLogical + rightBars, price: this.entryPrice });
                if (leftP && rightP && typeof leftP.x === 'number' && typeof rightP.x === 'number' && !isNaN(leftP.x) && !isNaN(rightP.x)) {
                    var leftX = Math.min(leftP.x, rightP.x);
                    var rightX = Math.max(leftP.x, rightP.x);
                    return { left: leftX, right: rightX, width: Math.max(10, rightX - leftX) };
                }
            }

            var lOff = this.leftOffset || 40;
            var rOff = this.rightOffset || 40;
            return { left: entryPixel.x - lOff, right: entryPixel.x + rOff, width: lOff + rOff };
        }

        _ensurePrices() {
            if (this.entryPrice === undefined || this.entryPrice === null || this.entryPrice === 0) {
                if (this.coords && this.coords.length >= 1) {
                    this.entryPrice = this.coords[0].price || 0;
                }
            }
            if (this.targetPrice === undefined || this.targetPrice === null || this.stopPrice === undefined || this.stopPrice === null) {
                if (this.entryPrice) {
                    var _riskPct = (this.options && this.options.riskPct !== undefined) ? this.options.riskPct : 0.015;
                    var _risk = Math.abs(this.entryPrice) * _riskPct;
                    var _reward = _risk * (this.riskRewardRatio || 2);
                    this.stopPrice = this.entryPrice - _risk;
                    this.targetPrice = this.entryPrice + _reward;
                }
            }
        }

        isValid() {
            this._ensurePrices();
            return this.coords.length >= 1 && this.entryPrice > 0;
        }

        // Get bounding box for hit testing
        getHitBox(chartState) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY - this.initialHalfSize;
            const stopLossY = stopPixel ? stopPixel.y : entryY + this.initialHalfSize;

            return {
                left: left,
                right: right,
                top: Math.min(targetY, stopLossY),
                bottom: Math.max(targetY, stopLossY)
            };
        }

        _getCurrentPrice(entryFallback) {
            if (typeof window.lastLivePrice === 'number' && !isNaN(window.lastLivePrice) && window.lastLivePrice > 0) {
                return window.lastLivePrice;
            }
            if (typeof window._lastCandleClose === 'number' && !isNaN(window._lastCandleClose) && window._lastCandleClose > 0) {
                return window._lastCandleClose;
            }
            if (window._lastHistoricalCandle && typeof window._lastHistoricalCandle.close === 'number' && window._lastHistoricalCandle.close > 0) {
                return window._lastHistoricalCandle.close;
            }
            if (window.coordinateMapper && typeof window.coordinateMapper.lastPrice === 'number' && window.coordinateMapper.lastPrice > 0) {
                return window.coordinateMapper.lastPrice;
            }
            if (typeof window.currentBarPrice === 'number' && window.currentBarPrice > 0) {
                return window.currentBarPrice;
            }
            var candles = window._chartCandles || window.candleData || [];
            if (candles.length > 0 && candles[candles.length - 1] && typeof candles[candles.length - 1].close === 'number') {
                return candles[candles.length - 1].close;
            }
            var cpEl = document.getElementById('chart-ticker-price') || document.getElementById('header-price');
            if (cpEl && cpEl.textContent) {
                var p = parseFloat(cpEl.textContent.replace(/[^\d.]/g, ''));
                if (!isNaN(p) && p > 0) return p;
            }
            return entryFallback || 0;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return;

            const dpr = window.devicePixelRatio || 1;
            const viewport = (window.coordinateMapper && window.coordinateMapper.viewport) || (chartState && chartState.viewport) || {};
            const cw = (ctx.canvas ? ctx.canvas.width / dpr : 0) || (window.toolManager && window.toolManager.canvas ? window.toolManager.canvas.clientWidth : 0) || viewport.width || 800;
            const ch = (ctx.canvas ? ctx.canvas.height / dpr : 0) || (window.toolManager && window.toolManager.canvas ? window.toolManager.canvas.clientHeight : 0) || viewport.height || 500;

            const isHighlighted = !!(isSelected || isHovered);
            const fillOpacity = isHighlighted ? 0.28 : 0.20;

            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;
            const width = bounds.width;

            // Convert prices to pixels
            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY - this.initialHalfSize;
            const stopLossY = stopPixel ? stopPixel.y : entryY + this.initialHalfSize;

            // Profit zone (green/teal) - above entry for long
            ctx.fillStyle = `rgba(8, 153, 129, ${fillOpacity})`;
            ctx.fillRect(left, targetY, width, entryY - targetY);

            // Loss zone (red) - below entry for long
            ctx.fillStyle = `rgba(242, 54, 69, ${fillOpacity})`;
            ctx.fillRect(left, entryY, width, stopLossY - entryY);

            // Draw border outlines only when highlighted/selected
            if (isHighlighted) {
                ctx.strokeStyle = 'rgba(8, 153, 129, 0.7)';
                ctx.lineWidth = 1;
                ctx.strokeRect(left, targetY, width, entryY - targetY);
                ctx.strokeStyle = 'rgba(242, 54, 69, 0.7)';
                ctx.strokeRect(left, entryY, width, stopLossY - entryY);
            }

            // Draw entry line
            ctx.beginPath();
            ctx.moveTo(left, entryY);
            ctx.lineTo(right, entryY);
            ctx.strokeStyle = isHighlighted ? '#2962ff' : 'rgba(200, 200, 200, 0.5)';
            ctx.lineWidth = isHighlighted ? 1.5 : 1;
            ctx.stroke();

            // When selected or hovered, show tooltips, center PnL badge, handles
            if (isHighlighted) {
                const targetDiff = this.targetPrice - this.entryPrice;
                const targetPct = ((targetDiff / this.entryPrice) * 100).toFixed(3);
                const targetTicks = Math.round(targetDiff / (window.currentTickSize || 0.05));
                const targetAmount = (targetDiff * this.quantity).toFixed(2);
                const targetText = `Target: ${targetDiff.toFixed(2)} (${targetPct}%) ${targetTicks}, Amount: ${targetAmount}`;
                this._drawTooltipWithPointer(ctx, left, targetY, targetText, '#089981', 'down');

                const stopDiff = this.entryPrice - this.stopPrice;
                const stopPct = ((stopDiff / this.entryPrice) * 100).toFixed(3);
                const stopTicks = Math.round(stopDiff / (window.currentTickSize || 0.05));
                const stopAmount = (stopDiff * this.quantity).toFixed(2);
                const stopText = `Stop: ${stopDiff.toFixed(2)} (${stopPct}%) ${stopTicks}, Amount: ${stopAmount}`;
                this._drawTooltipWithPointer(ctx, left, stopLossY, stopText, '#f23645', 'up');

                const currentPrice = this._getCurrentPrice(this.entryPrice);
                const openPnl = (currentPrice - this.entryPrice) * this.quantity;
                const centerX = left + width / 2;
                const centerY = (targetY + entryY) / 2;
                this._drawCenterPnLBox(ctx, centerX, centerY, openPnl, this.quantity, this.riskRewardRatio);

                // Handles (blue rounded squares)
                this._drawHandle(ctx, left, targetY);
                this._drawHandle(ctx, right, targetY);
                this._drawHandle(ctx, left, entryY);
                this._drawHandle(ctx, right, entryY);
                this._drawHandle(ctx, left, stopLossY);
                this._drawHandle(ctx, right, stopLossY);
            }
        }

        drawAxisBadges(ctx, chartState, isSelected, isHovered) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return;

            const isHighlighted = !!(isSelected || isHovered);
            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;

            const targetCoord = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopCoord = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });
            const targetY = targetCoord ? targetCoord.y : entryY - this.initialHalfSize;
            const stopLossY = stopCoord ? stopCoord.y : entryY + this.initialHalfSize;

            // Always draw 3 price badges on the Y-Axis price scale (Target, Entry, Stop)
            this._drawPriceScaleBadge(ctx, targetY, this.targetPrice, '#089981', chartState);
            this._drawPriceScaleBadge(ctx, entryY, this.entryPrice, '#787b86', chartState);
            this._drawPriceScaleBadge(ctx, stopLossY, this.stopPrice, '#f23645', chartState);

            if (isHighlighted) {
                const startTimeStr = this._formatTimeBadge(left, entryY, chartState);
                const endTimeStr = this._formatTimeBadge(right, entryY, chartState);
                if (startTimeStr) this._drawTimeScaleBadge(ctx, left, startTimeStr, '#2962ff', chartState);
                if (endTimeStr) this._drawTimeScaleBadge(ctx, right, endTimeStr, '#2962ff', chartState);
            }
        }

        _drawPriceScaleBadge(ctx, y, price, bgColor, chartState) {
            if (y === null || y === undefined || isNaN(y) || price === null || price === undefined) return;
            var text = typeof price === 'number' ? price.toFixed(2) : String(price);
            var dims = this._getPlotDimensions(ctx.canvas);
            var plotWidth = dims.plotWidth;
            var priceScaleWidth = dims.priceScaleWidth;

            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw = ctx.measureText(text).width;
            var pad = 6, bh = 18;
            var bw = Math.min(Math.round(tw + pad * 2), priceScaleWidth - 4);
            var rx = plotWidth + 2;

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(rx, y - bh / 2, bw, bh, 3);
            } else {
                ctx.rect(rx, y - bh / 2, bw, bh);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, rx + bw / 2, y);
            ctx.restore();
        }

        _drawTimeScaleBadge(ctx, x, text, bgColor, chartState) {
            if (x === null || x === undefined || isNaN(x) || !text) return;
            var dims = this._getPlotDimensions(ctx.canvas);
            var plotWidth = dims.plotWidth;
            var plotHeight = dims.plotHeight;
            var timeScaleHeight = dims.timeScaleHeight;

            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw = ctx.measureText(text).width;
            var pad = 6, bh = Math.min(20, Math.max(16, timeScaleHeight - 4));
            var bw = tw + pad * 2;
            var bx = Math.max(2, Math.min(plotWidth - bw - 2, x - bw / 2));
            var by = plotHeight + 2;

            ctx.fillStyle = bgColor || '#2962ff';
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(bx, by, bw, bh, 3);
            } else {
                ctx.rect(bx, by, bw, bh);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, bx + bw / 2, by + bh / 2);
            ctx.restore();
        }

        _formatTimeBadge(pixelX, entryY, chartState) {
            if (!chartState) return null;
            var coord = chartState.pixelToCoord ? chartState.pixelToCoord(pixelX, entryY) : null;
            var timeSec = null;
            var logical = coord ? coord.logical : null;

            if (logical === null || logical === undefined || isNaN(logical)) {
                if (chartState.xToLogical) logical = chartState.xToLogical(pixelX);
            }

            var candles = window._chartCandles || window.candleData || [];
            if (candles.length > 0 && logical !== null && logical !== undefined && !isNaN(logical)) {
                var idx = Math.round(logical);
                if (idx >= 0 && idx < candles.length && candles[idx]) {
                    var ct = candles[idx].time;
                    if (typeof ct === 'number') timeSec = ct > 1e11 ? ct / 1000 : ct;
                    else if (typeof ct === 'string') {
                        var p = Date.parse(ct);
                        if (!isNaN(p)) timeSec = p / 1000;
                    }
                } else if (idx >= candles.length && candles.length > 1) {
                    var lastC = candles[candles.length - 1];
                    var prevC = candles[candles.length - 2];
                    var tLast = typeof lastC.time === 'number' ? (lastC.time > 1e11 ? lastC.time / 1000 : lastC.time) : (Date.parse(lastC.time) / 1000);
                    var tPrev = typeof prevC.time === 'number' ? (prevC.time > 1e11 ? prevC.time / 1000 : prevC.time) : (Date.parse(prevC.time) / 1000);
                    var interval = (!isNaN(tLast) && !isNaN(tPrev) && tLast > tPrev) ? (tLast - tPrev) : 300;
                    timeSec = tLast + (idx - (candles.length - 1)) * interval;
                } else if (idx < 0 && candles.length > 1) {
                    var firstC = candles[0];
                    var secondC = candles[1];
                    var tFirst = typeof firstC.time === 'number' ? (firstC.time > 1e11 ? firstC.time / 1000 : firstC.time) : (Date.parse(firstC.time) / 1000);
                    var tSecond = typeof secondC.time === 'number' ? (secondC.time > 1e11 ? secondC.time / 1000 : secondC.time) : (Date.parse(secondC.time) / 1000);
                    var interval = (!isNaN(tSecond) && !isNaN(tFirst) && tSecond > tFirst) ? (tSecond - tFirst) : 300;
                    timeSec = tFirst - (0 - idx) * interval;
                }
            }

            if (timeSec === null && coord && coord.time !== undefined && coord.time !== null) {
                if (typeof coord.time === 'number') {
                    timeSec = coord.time > 1e11 ? coord.time / 1000 : coord.time;
                } else if (typeof coord.time === 'string') {
                    var parsed = Date.parse(coord.time);
                    if (!isNaN(parsed)) timeSec = parsed / 1000;
                }
            }

            if (timeSec === null && logical !== null && logical !== undefined && window.coordinateMapper && typeof window.coordinateMapper._logicalToTime === 'function') {
                var cmTime = window.coordinateMapper._logicalToTime(logical);
                if (cmTime != null) {
                    timeSec = typeof cmTime === 'number' ? (cmTime > 1e11 ? cmTime / 1000 : cmTime) : (Date.parse(cmTime) / 1000);
                }
            }

            if (timeSec === null || isNaN(timeSec) || timeSec <= 0) return null;

            var d = new Date(timeSec * 1000);
            var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            var dayName = days[d.getDay()];
            var dayNum = String(d.getDate()).padStart(2, '0');
            var mon = months[d.getMonth()];
            var yr = "'" + String(d.getFullYear()).slice(-2);
            var hrs = String(d.getHours()).padStart(2, '0');
            var mins = String(d.getMinutes()).padStart(2, '0');

            if (hrs === '00' && mins === '00' && (window.activeRange === '1D' || window.activeRange === '1W' || window.activeRange === '1M')) {
                return `${dayName} ${dayNum} ${mon} ${yr}`;
            }
            return `${dayName} ${dayNum} ${mon} ${yr}  ${hrs}:${mins}`;
        }

        _drawTooltipWithPointer(ctx, x, y, text, bgColor, pointerDirection) {
            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw = ctx.measureText(text).width;
            var padX = 8, bh = 22;
            var bw = tw + padX * 2;
            var r = 4;
            var pointerSize = 5;
            var bx = x;
            var by = pointerDirection === 'down' ? y - bh - pointerSize : y + pointerSize;

            // Draw rounded box
            ctx.fillStyle = bgColor;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(bx, by, bw, bh, r);
            } else {
                ctx.rect(bx, by, bw, bh);
            }
            ctx.fill();

            // Draw pointer triangle
            var px = bx + 18;
            ctx.beginPath();
            if (pointerDirection === 'down') {
                ctx.moveTo(px - pointerSize, by + bh);
                ctx.lineTo(px, by + bh + pointerSize);
                ctx.lineTo(px + pointerSize, by + bh);
            } else {
                ctx.moveTo(px - pointerSize, by);
                ctx.lineTo(px, by - pointerSize);
                ctx.lineTo(px + pointerSize, by);
            }
            ctx.closePath();
            ctx.fill();

            // Text
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, bx + padX, by + bh / 2);
            ctx.restore();
        }

        _drawCenterPnLBox(ctx, centerX, centerY, pnl, qty, rr) {
            var isPositive = pnl >= 0;
            var bgColor = isPositive ? 'rgba(8, 153, 129, 0.95)' : 'rgba(242, 54, 69, 0.95)';
            var pnlText = `Open PnL: ${isPositive ? '' : '-'}${Math.abs(pnl).toFixed(2)}, Qty: ${qty}`;
            var rrText = `Risk/reward ratio: ${typeof rr === 'number' ? rr.toFixed(2) : rr}`;

            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw1 = ctx.measureText(pnlText).width;
            ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw2 = ctx.measureText(rrText).width;
            var boxWidth = Math.max(tw1, tw2) + 20;
            var boxHeight = 36;
            var bx = centerX - boxWidth / 2;
            var by = centerY - boxHeight / 2;

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(bx, by, boxWidth, boxHeight, 5);
            } else {
                ctx.rect(bx, by, boxWidth, boxHeight);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.fillText(pnlText, centerX, centerY - 8);
            ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.fillText(rrText, centerX, centerY + 8);
            ctx.restore();
        }

        _drawHandle(ctx, x, y) {
            var size = 8;
            ctx.save();
            ctx.fillStyle = '#131722';
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(x - size / 2, y - size / 2, size, size, 2);
            } else {
                ctx.rect(x - size / 2, y - size / 2, size, size);
            }
            ctx.fill();
            ctx.stroke();
            ctx.restore();
        }

        drawHandle(ctx, p, isSelected, color) {
            const size = isSelected ? 5 : 3;
            ctx.fillStyle = color || '#2962ff';
            ctx.beginPath();
            ctx.rect(p.x - size, p.y - size, size * 2, size * 2);
            ctx.fill();
            if (isSelected) {
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 1;
                ctx.stroke();
            }
        }

        drawPositionLabel(ctx, x, y, text, bgColor) {
            ctx.font = 'bold 11px Arial';
            const textWidth = ctx.measureText(text).width;
            const padding = 6;
            const height = 16;

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            ctx.roundRect(x, y - height / 2, textWidth + padding * 2, height, 2);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, x + padding, y);
        }

        drawCenterPnL(ctx, x, y, profitPct, profitAmount) {
            const text1 = `Closed P&L:  ${profitPct}, Qty: ${this.quantity}`;
            const text2 = `Risk/Reward Ratio:  ${this.riskRewardRatio}`;

            ctx.font = 'bold 11px Arial';
            const width1 = ctx.measureText(text1).width;
            const width2 = ctx.measureText(text2).width;
            const boxWidth = Math.max(width1, width2) + 20;
            const boxHeight = 36;

            ctx.fillStyle = 'rgba(38, 166, 154, 0.95)';
            ctx.beginPath();
            ctx.roundRect(x - boxWidth / 2, y - boxHeight / 2, boxWidth, boxHeight, 4);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text1, x, y - 8);
            ctx.font = '10px Arial';
            ctx.fillText(text2, x, y + 8);
        }

        // Hit test for handles - returns handle name if hit, null otherwise
        hitTestHandle(pos, chartState) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY - this.initialHalfSize;
            const stopLossY = stopPixel ? stopPixel.y : entryY + this.initialHalfSize;

            const hitThreshold = 10;

            // Corner handles (check first so they take priority over edges)
            if (Math.abs(pos.x - left) <= hitThreshold && Math.abs(pos.y - targetY) <= hitThreshold) return 'topLeft';
            if (Math.abs(pos.x - right) <= hitThreshold && Math.abs(pos.y - targetY) <= hitThreshold) return 'topRight';
            if (Math.abs(pos.x - left) <= hitThreshold && Math.abs(pos.y - stopLossY) <= hitThreshold) return 'bottomLeft';
            if (Math.abs(pos.x - right) <= hitThreshold && Math.abs(pos.y - stopLossY) <= hitThreshold) return 'bottomRight';

            // Check left edge (vertical line for horizontal resize)
            if (Math.abs(pos.x - left) <= hitThreshold && pos.y >= targetY - hitThreshold && pos.y <= stopLossY + hitThreshold) {
                return 'left';
            }
            // Check right edge (vertical line for horizontal resize)
            if (Math.abs(pos.x - right) <= hitThreshold && pos.y >= targetY - hitThreshold && pos.y <= stopLossY + hitThreshold) {
                return 'right';
            }
            // Check target line (anywhere along it)
            if (Math.abs(pos.y - targetY) <= hitThreshold && pos.x >= left - hitThreshold && pos.x <= right + hitThreshold) {
                return 'target';
            }
            // Check stop line
            if (Math.abs(pos.y - stopLossY) <= hitThreshold && pos.x >= left - hitThreshold && pos.x <= right + hitThreshold) {
                return 'stop';
            }

            return null;
        }

        // Update prices/width when handle is dragged
        updateHandle(handleName, newPrice, chartState, pixelX) {
            if (handleName === 'target') {
                // Target must be above entry for long
                if (newPrice > this.entryPrice) {
                    this.targetPrice = newPrice;
                    // Recalculate R:R ratio
                    const profit = this.targetPrice - this.entryPrice;
                    const risk = this.entryPrice - this.stopPrice;
                    if (risk > 0) {
                        this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                    }
                }
            } else if (handleName === 'stop') {
                // Stop must be below entry for long
                if (newPrice < this.entryPrice) {
                    this.stopPrice = newPrice;
                    // Recalculate R:R ratio
                    const profit = this.targetPrice - this.entryPrice;
                    const risk = this.entryPrice - this.stopPrice;
                    if (risk > 0) {
                        this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                    }
                }
            } else if ((handleName === 'left' || handleName === 'topLeft' || handleName === 'bottomLeft') && pixelX !== undefined) {
                // Resize from left edge
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    var entryLogical = this._getEntryLogical(chartState);
                    if (entryLogical !== null && chartState && typeof chartState.pixelToCoord === 'function') {
                        var c = chartState.pixelToCoord(pixelX, entryPixel.y);
                        if (c && c.logical !== null && c.logical !== undefined && !isNaN(c.logical)) {
                            var diff = entryLogical - c.logical;
                            if (diff >= 0.5) this.leftBars = diff;
                        }
                    }
                    const newLeftOffset = entryPixel.x - pixelX;
                    if (newLeftOffset >= 15) {
                        this.leftOffset = newLeftOffset;
                    }
                }
            } else if ((handleName === 'right' || handleName === 'topRight' || handleName === 'bottomRight') && pixelX !== undefined) {
                // Resize from right edge
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    var entryLogical = this._getEntryLogical(chartState);
                    if (entryLogical !== null && chartState && typeof chartState.pixelToCoord === 'function') {
                        var c = chartState.pixelToCoord(pixelX, entryPixel.y);
                        if (c && c.logical !== null && c.logical !== undefined && !isNaN(c.logical)) {
                            var diff = c.logical - entryLogical;
                            if (diff >= 0.5) this.rightBars = diff;
                        }
                    }
                    const newRightOffset = pixelX - entryPixel.x;
                    if (newRightOffset >= 15) {
                        this.rightOffset = newRightOffset;
                    }
                }
            }
            // Corner vertical resizing
            if (handleName === 'topLeft' || handleName === 'topRight') {
                if (newPrice > this.entryPrice) {
                    this.targetPrice = newPrice;
                    const profit = this.targetPrice - this.entryPrice;
                    const risk = this.entryPrice - this.stopPrice;
                    if (risk > 0) this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                }
            } else if (handleName === 'bottomLeft' || handleName === 'bottomRight') {
                if (newPrice < this.entryPrice) {
                    this.stopPrice = newPrice;
                    const profit = this.targetPrice - this.entryPrice;
                    const risk = this.entryPrice - this.stopPrice;
                    if (risk > 0) this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                }
            }
        }

        getPixels(chartState) {
            if (this.coords.length < 1) return [];
            const p = chartState.coordToPixel(this.coords[0]);
            return p ? [p] : [];
        }

        getFillShape(pixels) {
            if (!window.toolManager) return null;
            const chartState = window.toolManager.getChartState();
            const box = this.getHitBox(chartState);
            if (!box) return null;
            return [
                { x: box.left, y: box.top },
                { x: box.right, y: box.top },
                { x: box.right, y: box.bottom },
                { x: box.left, y: box.bottom }
            ];
        }

        drawHandles(ctx, pixels, isSelected) {
            // Overridden to do nothing so the default black-and-white handle box is not drawn in the middle.
        }

        getAnchorPoints(pixels) {
            return [];
        }

        translate(dx, dy, chartState) {
            if (this.locked) return;
            const oldEntry = this.entryPrice;
            super.translate(dx, dy, chartState);
            if (this.coords.length >= 1) {
                const newEntry = this.coords[0].price;
                const delta = newEntry - oldEntry;
                this.targetPrice += delta;
                this.stopPrice += delta;
                this.entryPrice = newEntry;
            }
        }
    }

    // Single-click Short Position - TradingView style spanning candles
    class ShortPosition extends BaseDrawing {
        constructor(startPos, chartState, options = {}) {
            super(startPos, chartState, options);
            this.quantity = options.quantity || 10;
            this.riskRewardRatio = options.riskRewardRatio || 2;

            this.initialHalfSize = 40; // Fixed pixel half-size fallback

            var cs = chartState || (window.toolManager && typeof window.toolManager.getChartState === 'function' ? window.toolManager.getChartState() : null) || window.coordinateMapper;

            // Bar-based horizontal width so it stays pinned to candles and scales properly when zooming/panning
            var defaultBars = 4;
            if (options.leftBars !== undefined) {
                this.leftBars = options.leftBars;
            } else if (options.leftOffset && cs && typeof cs.coordToPixel === 'function') {
                var p0 = cs.coordToPixel({ logical: 100, price: this.coords[0] ? this.coords[0].price : 100 });
                var p1 = cs.coordToPixel({ logical: 101, price: this.coords[0] ? this.coords[0].price : 100 });
                var spacing = (p0 && p1 && typeof p0.x === 'number' && typeof p1.x === 'number') ? Math.abs(p1.x - p0.x) : 8;
                this.leftBars = Math.max(1, Math.round(options.leftOffset / (spacing || 8)));
            } else {
                this.leftBars = defaultBars;
            }

            if (options.rightBars !== undefined) {
                this.rightBars = options.rightBars;
            } else if (options.rightOffset && cs && typeof cs.coordToPixel === 'function') {
                var p0 = cs.coordToPixel({ logical: 100, price: this.coords[0] ? this.coords[0].price : 100 });
                var p1 = cs.coordToPixel({ logical: 101, price: this.coords[0] ? this.coords[0].price : 100 });
                var spacing = (p0 && p1 && typeof p0.x === 'number' && typeof p1.x === 'number') ? Math.abs(p1.x - p0.x) : 8;
                this.rightBars = Math.max(1, Math.round(options.rightOffset / (spacing || 8)));
            } else {
                this.rightBars = defaultBars;
            }

            // Asymmetric width offsets fallback (from entry point)
            this.leftOffset = this.initialHalfSize;
            this.rightOffset = this.initialHalfSize;

            if (options.targetPrice !== undefined && options.stopPrice !== undefined) {
                this.targetPrice = options.targetPrice;
                this.stopPrice = options.stopPrice;
                this.entryPrice = options.entryPrice || (this.coords[0] ? this.coords[0].price : 0);
            } else {
                if (this.coords.length >= 1) {
                    this.entryPrice = this.coords[0].price || 0;
                }
                var entryPixelY = null;
                var entryPixelX = null;
                if (startPos && typeof startPos.y === 'number') {
                    entryPixelY = startPos.y;
                    entryPixelX = startPos.x;
                } else if (this.coords.length >= 1 && cs && typeof cs.coordToPixel === 'function') {
                    var p = cs.coordToPixel(this.coords[0]);
                    if (p) {
                        entryPixelY = p.y;
                        entryPixelX = p.x;
                    }
                }

                var calculated = false;
                if (cs && typeof cs.pixelToCoord === 'function' && typeof entryPixelY === 'number' && !isNaN(entryPixelY)) {
                    // For short: stop is 24px above (higher price), target is 48px below (lower price)
                    var stopCoord = cs.pixelToCoord(entryPixelX || 100, entryPixelY - 24);
                    var targetCoord = cs.pixelToCoord(entryPixelX || 100, entryPixelY + 48);
                    if (targetCoord && typeof targetCoord.price === 'number' && stopCoord && typeof stopCoord.price === 'number') {
                        if (stopCoord.price > this.entryPrice && targetCoord.price < this.entryPrice) {
                            this.stopPrice = stopCoord.price;
                            this.targetPrice = targetCoord.price;
                            var profit = this.entryPrice - this.targetPrice;
                            var risk = this.stopPrice - this.entryPrice;
                            if (risk > 0) {
                                this.riskRewardRatio = Math.round((profit / risk) * 10) / 10 || 2;
                            }
                            calculated = true;
                        }
                    }
                }

                if (!calculated && this.coords.length >= 1) {
                    this.entryPrice = this.coords[0].price || 0;
                    var _riskPctS = (options.riskPct !== undefined) ? options.riskPct : 0.015;
                    var _riskS = Math.abs(this.entryPrice) * _riskPctS;
                    var _rewardS = _riskS * this.riskRewardRatio;
                    // short: stop ABOVE entry, target BELOW entry
                    this.stopPrice = this.entryPrice + _riskS;
                    this.targetPrice = this.entryPrice - _rewardS;
                }
            }
        }

        // Override - complete on single click
        addPoint(pos, chartState) {
            return true; // Always complete immediately
        }

        _getEntryLogical(chartState) {
            var entryCoord = this.coords && this.coords[0];
            if (!entryCoord) return null;
            if (entryCoord.logical !== undefined && entryCoord.logical !== null && !isNaN(entryCoord.logical)) {
                return entryCoord.logical;
            }
            var cs = chartState || (window.toolManager && typeof window.toolManager.getChartState === 'function' ? window.toolManager.getChartState() : null) || window.coordinateMapper;
            if (cs) {
                if (entryCoord.time !== undefined && entryCoord.time !== null && typeof cs.timeToLogical === 'function') {
                    var log = cs.timeToLogical(entryCoord.time);
                    if (log !== null && log !== undefined && !isNaN(log)) {
                        entryCoord.logical = log;
                        return log;
                    }
                }
                var p = cs.coordToPixel ? cs.coordToPixel(entryCoord) : null;
                if (p && typeof cs.pixelToCoord === 'function') {
                    var c = cs.pixelToCoord(p.x, p.y);
                    if (c && c.logical !== null && c.logical !== undefined && !isNaN(c.logical)) {
                        entryCoord.logical = c.logical;
                        return c.logical;
                    }
                }
            }
            return null;
        }

        _getHorizontalBounds(chartState, entryPixel) {
            var entryLogical = this._getEntryLogical(chartState);
            var leftBars = (typeof this.leftBars === 'number' && this.leftBars > 0) ? this.leftBars : 4;
            var rightBars = (typeof this.rightBars === 'number' && this.rightBars > 0) ? this.rightBars : 4;

            if (entryLogical !== null && chartState && typeof chartState.coordToPixel === 'function') {
                var leftP = chartState.coordToPixel({ logical: entryLogical - leftBars, price: this.entryPrice });
                var rightP = chartState.coordToPixel({ logical: entryLogical + rightBars, price: this.entryPrice });
                if (leftP && rightP && typeof leftP.x === 'number' && typeof rightP.x === 'number' && !isNaN(leftP.x) && !isNaN(rightP.x)) {
                    var leftX = Math.min(leftP.x, rightP.x);
                    var rightX = Math.max(leftP.x, rightP.x);
                    return { left: leftX, right: rightX, width: Math.max(10, rightX - leftX) };
                }
            }

            var lOff = this.leftOffset || 40;
            var rOff = this.rightOffset || 40;
            return { left: entryPixel.x - lOff, right: entryPixel.x + rOff, width: lOff + rOff };
        }

        _ensurePrices() {
            if (this.entryPrice === undefined || this.entryPrice === null || this.entryPrice === 0) {
                if (this.coords && this.coords.length >= 1) {
                    this.entryPrice = this.coords[0].price || 0;
                }
            }
            if (this.targetPrice === undefined || this.targetPrice === null || this.stopPrice === undefined || this.stopPrice === null) {
                if (this.entryPrice) {
                    var _riskPctS = (this.options && this.options.riskPct !== undefined) ? this.options.riskPct : 0.015;
                    var _riskS = Math.abs(this.entryPrice) * _riskPctS;
                    var _rewardS = _riskS * (this.riskRewardRatio || 2);
                    this.stopPrice = this.entryPrice + _riskS;
                    this.targetPrice = this.entryPrice - _rewardS;
                }
            }
        }

        isValid() {
            this._ensurePrices();
            return this.coords.length >= 1 && this.entryPrice > 0;
        }

        // Get bounding box for hit testing
        getHitBox(chartState) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY + this.initialHalfSize;
            const stopLossY = stopPixel ? stopPixel.y : entryY - this.initialHalfSize;

            return {
                left: left,
                right: right,
                top: Math.min(targetY, stopLossY),
                bottom: Math.max(targetY, stopLossY)
            };
        }

        _getCurrentPrice(entryFallback) {
            if (typeof window.lastLivePrice === 'number' && !isNaN(window.lastLivePrice) && window.lastLivePrice > 0) {
                return window.lastLivePrice;
            }
            if (typeof window._lastCandleClose === 'number' && !isNaN(window._lastCandleClose) && window._lastCandleClose > 0) {
                return window._lastCandleClose;
            }
            if (window._lastHistoricalCandle && typeof window._lastHistoricalCandle.close === 'number' && window._lastHistoricalCandle.close > 0) {
                return window._lastHistoricalCandle.close;
            }
            if (window.coordinateMapper && typeof window.coordinateMapper.lastPrice === 'number' && window.coordinateMapper.lastPrice > 0) {
                return window.coordinateMapper.lastPrice;
            }
            if (typeof window.currentBarPrice === 'number' && window.currentBarPrice > 0) {
                return window.currentBarPrice;
            }
            var candles = window._chartCandles || window.candleData || [];
            if (candles.length > 0 && candles[candles.length - 1] && typeof candles[candles.length - 1].close === 'number') {
                return candles[candles.length - 1].close;
            }
            var cpEl = document.getElementById('chart-ticker-price') || document.getElementById('header-price');
            if (cpEl && cpEl.textContent) {
                var p = parseFloat(cpEl.textContent.replace(/[^\d.]/g, ''));
                if (!isNaN(p) && p > 0) return p;
            }
            return entryFallback || 0;
        }

        draw(ctx, chartState, isSelected, isHovered) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return;

            const dpr = window.devicePixelRatio || 1;
            const viewport = (window.coordinateMapper && window.coordinateMapper.viewport) || (chartState && chartState.viewport) || {};
            const cw = (ctx.canvas ? ctx.canvas.width / dpr : 0) || (window.toolManager && window.toolManager.canvas ? window.toolManager.canvas.clientWidth : 0) || viewport.width || 800;
            const ch = (ctx.canvas ? ctx.canvas.height / dpr : 0) || (window.toolManager && window.toolManager.canvas ? window.toolManager.canvas.clientHeight : 0) || viewport.height || 500;

            const isHighlighted = !!(isSelected || isHovered);
            const fillOpacity = isHighlighted ? 0.28 : 0.20;

            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;
            const width = bounds.width;

            // Convert prices to pixels
            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY + this.initialHalfSize; // Below entry for short
            const stopLossY = stopPixel ? stopPixel.y : entryY - this.initialHalfSize; // Above entry for short

            // Loss zone (red) - above entry for short
            ctx.fillStyle = `rgba(242, 54, 69, ${fillOpacity})`;
            ctx.fillRect(left, stopLossY, width, entryY - stopLossY);

            // Profit zone (green/teal) - below entry for short
            ctx.fillStyle = `rgba(8, 153, 129, ${fillOpacity})`;
            ctx.fillRect(left, entryY, width, targetY - entryY);

            // Draw border outlines only when highlighted/selected
            if (isHighlighted) {
                ctx.strokeStyle = 'rgba(242, 54, 69, 0.7)';
                ctx.lineWidth = 1;
                ctx.strokeRect(left, stopLossY, width, entryY - stopLossY);
                ctx.strokeStyle = 'rgba(8, 153, 129, 0.7)';
                ctx.strokeRect(left, entryY, width, targetY - entryY);
            }

            // Draw entry line
            ctx.beginPath();
            ctx.moveTo(left, entryY);
            ctx.lineTo(right, entryY);
            ctx.strokeStyle = isHighlighted ? '#2962ff' : 'rgba(200, 200, 200, 0.5)';
            ctx.lineWidth = isHighlighted ? 1.5 : 1;
            ctx.stroke();

            // When selected or hovered, show tooltips, center PnL badge, handles
            if (isHighlighted) {
                const stopDiff = this.stopPrice - this.entryPrice;
                const stopPct = ((stopDiff / this.entryPrice) * 100).toFixed(3);
                const stopTicks = Math.round(stopDiff / (window.currentTickSize || 0.05));
                const stopAmount = (stopDiff * this.quantity).toFixed(2);
                const stopText = `Stop: ${stopDiff.toFixed(2)} (${stopPct}%) ${stopTicks}, Amount: ${stopAmount}`;
                this._drawTooltipWithPointer(ctx, left, stopLossY, stopText, '#f23645', 'down');

                const targetDiff = this.entryPrice - this.targetPrice;
                const targetPct = ((targetDiff / this.entryPrice) * 100).toFixed(3);
                const targetTicks = Math.round(targetDiff / (window.currentTickSize || 0.05));
                const targetAmount = (targetDiff * this.quantity).toFixed(2);
                const targetText = `Target: ${targetDiff.toFixed(2)} (${targetPct}%) ${targetTicks}, Amount: ${targetAmount}`;
                this._drawTooltipWithPointer(ctx, left, targetY, targetText, '#089981', 'up');

                const currentPrice = this._getCurrentPrice(this.entryPrice);
                const openPnl = (this.entryPrice - currentPrice) * this.quantity;
                const centerX = left + width / 2;
                const centerY = (targetY + entryY) / 2;
                this._drawCenterPnLBox(ctx, centerX, centerY, openPnl, this.quantity, this.riskRewardRatio);

                // Handles (blue rounded squares)
                this._drawHandle(ctx, left, stopLossY);
                this._drawHandle(ctx, right, stopLossY);
                this._drawHandle(ctx, left, entryY);
                this._drawHandle(ctx, right, entryY);
                this._drawHandle(ctx, left, targetY);
                this._drawHandle(ctx, right, targetY);
            }
        }

        drawAxisBadges(ctx, chartState, isSelected, isHovered) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return;

            const isHighlighted = !!(isSelected || isHovered);
            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;

            const targetCoord = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopCoord = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });
            const targetY = targetCoord ? targetCoord.y : entryY + this.initialHalfSize;
            const stopLossY = stopCoord ? stopCoord.y : entryY - this.initialHalfSize;

            // Always draw 3 price badges on the Y-Axis price scale (Target, Entry, Stop)
            this._drawPriceScaleBadge(ctx, targetY, this.targetPrice, '#089981', chartState);
            this._drawPriceScaleBadge(ctx, entryY, this.entryPrice, '#787b86', chartState);
            this._drawPriceScaleBadge(ctx, stopLossY, this.stopPrice, '#f23645', chartState);

            if (isHighlighted) {
                const startTimeStr = this._formatTimeBadge(left, entryY, chartState);
                const endTimeStr = this._formatTimeBadge(right, entryY, chartState);
                if (startTimeStr) this._drawTimeScaleBadge(ctx, left, startTimeStr, '#2962ff', chartState);
                if (endTimeStr) this._drawTimeScaleBadge(ctx, right, endTimeStr, '#2962ff', chartState);
            }
        }

        _drawPriceScaleBadge(ctx, y, price, bgColor, chartState) {
            if (y === null || y === undefined || isNaN(y) || price === null || price === undefined) return;
            var text = typeof price === 'number' ? price.toFixed(2) : String(price);
            var dims = this._getPlotDimensions(ctx.canvas);
            var plotWidth = dims.plotWidth;
            var priceScaleWidth = dims.priceScaleWidth;

            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw = ctx.measureText(text).width;
            var pad = 6, bh = 18;
            var bw = Math.min(Math.round(tw + pad * 2), priceScaleWidth - 4);
            var rx = plotWidth + 2;

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(rx, y - bh / 2, bw, bh, 3);
            } else {
                ctx.rect(rx, y - bh / 2, bw, bh);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, rx + bw / 2, y);
            ctx.restore();
        }

        _drawTimeScaleBadge(ctx, x, text, bgColor, chartState) {
            if (x === null || x === undefined || isNaN(x) || !text) return;
            var dims = this._getPlotDimensions(ctx.canvas);
            var plotWidth = dims.plotWidth;
            var plotHeight = dims.plotHeight;
            var timeScaleHeight = dims.timeScaleHeight;

            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw = ctx.measureText(text).width;
            var pad = 6, bh = Math.min(20, Math.max(16, timeScaleHeight - 4));
            var bw = tw + pad * 2;
            var bx = Math.max(2, Math.min(plotWidth - bw - 2, x - bw / 2));
            var by = plotHeight + 2;

            ctx.fillStyle = bgColor || '#2962ff';
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(bx, by, bw, bh, 3);
            } else {
                ctx.rect(bx, by, bw, bh);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, bx + bw / 2, by + bh / 2);
            ctx.restore();
        }

        _formatTimeBadge(pixelX, entryY, chartState) {
            if (!chartState) return null;
            var coord = chartState.pixelToCoord ? chartState.pixelToCoord(pixelX, entryY) : null;
            var timeSec = null;
            var logical = coord ? coord.logical : null;

            if (logical === null || logical === undefined || isNaN(logical)) {
                if (chartState.xToLogical) logical = chartState.xToLogical(pixelX);
            }

            var candles = window._chartCandles || window.candleData || [];
            if (candles.length > 0 && logical !== null && logical !== undefined && !isNaN(logical)) {
                var idx = Math.round(logical);
                if (idx >= 0 && idx < candles.length && candles[idx]) {
                    var ct = candles[idx].time;
                    if (typeof ct === 'number') timeSec = ct > 1e11 ? ct / 1000 : ct;
                    else if (typeof ct === 'string') {
                        var p = Date.parse(ct);
                        if (!isNaN(p)) timeSec = p / 1000;
                    }
                } else if (idx >= candles.length && candles.length > 1) {
                    var lastC = candles[candles.length - 1];
                    var prevC = candles[candles.length - 2];
                    var tLast = typeof lastC.time === 'number' ? (lastC.time > 1e11 ? lastC.time / 1000 : lastC.time) : (Date.parse(lastC.time) / 1000);
                    var tPrev = typeof prevC.time === 'number' ? (prevC.time > 1e11 ? prevC.time / 1000 : prevC.time) : (Date.parse(prevC.time) / 1000);
                    var interval = (!isNaN(tLast) && !isNaN(tPrev) && tLast > tPrev) ? (tLast - tPrev) : 300;
                    timeSec = tLast + (idx - (candles.length - 1)) * interval;
                } else if (idx < 0 && candles.length > 1) {
                    var firstC = candles[0];
                    var secondC = candles[1];
                    var tFirst = typeof firstC.time === 'number' ? (firstC.time > 1e11 ? firstC.time / 1000 : firstC.time) : (Date.parse(firstC.time) / 1000);
                    var tSecond = typeof secondC.time === 'number' ? (secondC.time > 1e11 ? secondC.time / 1000 : secondC.time) : (Date.parse(secondC.time) / 1000);
                    var interval = (!isNaN(tSecond) && !isNaN(tFirst) && tSecond > tFirst) ? (tSecond - tFirst) : 300;
                    timeSec = tFirst - (0 - idx) * interval;
                }
            }

            if (timeSec === null && coord && coord.time !== undefined && coord.time !== null) {
                if (typeof coord.time === 'number') {
                    timeSec = coord.time > 1e11 ? coord.time / 1000 : coord.time;
                } else if (typeof coord.time === 'string') {
                    var parsed = Date.parse(coord.time);
                    if (!isNaN(parsed)) timeSec = parsed / 1000;
                }
            }

            if (timeSec === null && logical !== null && logical !== undefined && window.coordinateMapper && typeof window.coordinateMapper._logicalToTime === 'function') {
                var cmTime = window.coordinateMapper._logicalToTime(logical);
                if (cmTime != null) {
                    timeSec = typeof cmTime === 'number' ? (cmTime > 1e11 ? cmTime / 1000 : cmTime) : (Date.parse(cmTime) / 1000);
                }
            }

            if (timeSec === null || isNaN(timeSec) || timeSec <= 0) return null;

            var d = new Date(timeSec * 1000);
            var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            var dayName = days[d.getDay()];
            var dayNum = String(d.getDate()).padStart(2, '0');
            var mon = months[d.getMonth()];
            var yr = "'" + String(d.getFullYear()).slice(-2);
            var hrs = String(d.getHours()).padStart(2, '0');
            var mins = String(d.getMinutes()).padStart(2, '0');

            if (hrs === '00' && mins === '00' && (window.activeRange === '1D' || window.activeRange === '1W' || window.activeRange === '1M')) {
                return `${dayName} ${dayNum} ${mon} ${yr}`;
            }
            return `${dayName} ${dayNum} ${mon} ${yr}  ${hrs}:${mins}`;
        }

        _drawTooltipWithPointer(ctx, x, y, text, bgColor, pointerDirection) {
            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw = ctx.measureText(text).width;
            var padX = 8, bh = 22;
            var bw = tw + padX * 2;
            var r = 4;
            var pointerSize = 5;
            var bx = x;
            var by = pointerDirection === 'down' ? y - bh - pointerSize : y + pointerSize;

            // Draw rounded box
            ctx.fillStyle = bgColor;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(bx, by, bw, bh, r);
            } else {
                ctx.rect(bx, by, bw, bh);
            }
            ctx.fill();

            // Draw pointer triangle
            var px = bx + 18;
            ctx.beginPath();
            if (pointerDirection === 'down') {
                ctx.moveTo(px - pointerSize, by + bh);
                ctx.lineTo(px, by + bh + pointerSize);
                ctx.lineTo(px + pointerSize, by + bh);
            } else {
                ctx.moveTo(px - pointerSize, by);
                ctx.lineTo(px, by - pointerSize);
                ctx.lineTo(px + pointerSize, by);
            }
            ctx.closePath();
            ctx.fill();

            // Text
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, bx + padX, by + bh / 2);
            ctx.restore();
        }

        _drawCenterPnLBox(ctx, centerX, centerY, pnl, qty, rr) {
            var isPositive = pnl >= 0;
            var bgColor = isPositive ? 'rgba(8, 153, 129, 0.95)' : 'rgba(242, 54, 69, 0.95)';
            var pnlText = `Open PnL: ${isPositive ? '' : '-'}${Math.abs(pnl).toFixed(2)}, Qty: ${qty}`;
            var rrText = `Risk/reward ratio: ${typeof rr === 'number' ? rr.toFixed(2) : rr}`;

            ctx.save();
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw1 = ctx.measureText(pnlText).width;
            ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            var tw2 = ctx.measureText(rrText).width;
            var boxWidth = Math.max(tw1, tw2) + 20;
            var boxHeight = 36;
            var bx = centerX - boxWidth / 2;
            var by = centerY - boxHeight / 2;

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(bx, by, boxWidth, boxHeight, 5);
            } else {
                ctx.rect(bx, by, boxWidth, boxHeight);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.fillText(pnlText, centerX, centerY - 8);
            ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.fillText(rrText, centerX, centerY + 8);
            ctx.restore();
        }

        _drawHandle(ctx, x, y) {
            var size = 8;
            ctx.save();
            ctx.fillStyle = '#131722';
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.beginPath();
            if (typeof ctx.roundRect === 'function') {
                ctx.roundRect(x - size / 2, y - size / 2, size, size, 2);
            } else {
                ctx.rect(x - size / 2, y - size / 2, size, size);
            }
            ctx.fill();
            ctx.stroke();
            ctx.restore();
        }

        drawHandle(ctx, p, isSelected, color) {
            const size = isSelected ? 5 : 3;
            ctx.fillStyle = color || '#2962ff';
            ctx.beginPath();
            ctx.rect(p.x - size, p.y - size, size * 2, size * 2);
            ctx.fill();
            if (isSelected) {
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 1;
                ctx.stroke();
            }
        }

        drawPositionLabel(ctx, x, y, text, bgColor) {
            ctx.font = 'bold 11px Arial';
            const textWidth = ctx.measureText(text).width;
            const padding = 6;
            const height = 16;

            ctx.fillStyle = bgColor;
            ctx.beginPath();
            ctx.roundRect(x, y - height / 2, textWidth + padding * 2, height, 2);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, x + padding, y);
        }

        drawCenterPnL(ctx, x, y, profitPct, profitAmount) {
            const text1 = `Closed P&L:  ${profitPct}, Qty: ${this.quantity}`;
            const text2 = `Risk/Reward Ratio:  ${this.riskRewardRatio}`;

            ctx.font = 'bold 11px Arial';
            const width1 = ctx.measureText(text1).width;
            const width2 = ctx.measureText(text2).width;
            const boxWidth = Math.max(width1, width2) + 20;
            const boxHeight = 36;

            ctx.fillStyle = 'rgba(239, 83, 80, 0.95)';
            ctx.beginPath();
            ctx.roundRect(x - boxWidth / 2, y - boxHeight / 2, boxWidth, boxHeight, 4);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text1, x, y - 8);
            ctx.font = '10px Arial';
            ctx.fillText(text2, x, y + 8);
        }

        // Hit test for handles - returns handle name if hit, null otherwise
        hitTestHandle(pos, chartState) {
            this._ensurePrices();
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const bounds = this._getHorizontalBounds(chartState, entryPixel);
            const entryY = entryPixel.y;
            const left = bounds.left;
            const right = bounds.right;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY + this.initialHalfSize;
            const stopLossY = stopPixel ? stopPixel.y : entryY - this.initialHalfSize;

            const hitThreshold = 10;

            // Corner handles (check first so they take priority over edges)
            if (Math.abs(pos.x - left) <= hitThreshold && Math.abs(pos.y - stopLossY) <= hitThreshold) return 'topLeft';
            if (Math.abs(pos.x - right) <= hitThreshold && Math.abs(pos.y - stopLossY) <= hitThreshold) return 'topRight';
            if (Math.abs(pos.x - left) <= hitThreshold && Math.abs(pos.y - targetY) <= hitThreshold) return 'bottomLeft';
            if (Math.abs(pos.x - right) <= hitThreshold && Math.abs(pos.y - targetY) <= hitThreshold) return 'bottomRight';

            // Check left edge (vertical line for horizontal resize)
            if (Math.abs(pos.x - left) <= hitThreshold && pos.y >= stopLossY - hitThreshold && pos.y <= targetY + hitThreshold) {
                return 'left';
            }
            // Check right edge (vertical line for horizontal resize)
            if (Math.abs(pos.x - right) <= hitThreshold && pos.y >= stopLossY - hitThreshold && pos.y <= targetY + hitThreshold) {
                return 'right';
            }
            // Check stop line (above entry for short)
            if (Math.abs(pos.y - stopLossY) <= hitThreshold && pos.x >= left - hitThreshold && pos.x <= right + hitThreshold) {
                return 'stop';
            }
            // Check target line (below entry for short)
            if (Math.abs(pos.y - targetY) <= hitThreshold && pos.x >= left - hitThreshold && pos.x <= right + hitThreshold) {
                return 'target';
            }

            return null;
        }

        // Update prices/width when handle is dragged
        updateHandle(handleName, newPrice, chartState, pixelX) {
            if (handleName === 'target') {
                if (newPrice < this.entryPrice) {
                    this.targetPrice = newPrice;
                    const profit = this.entryPrice - this.targetPrice;
                    const risk = this.stopPrice - this.entryPrice;
                    if (risk > 0) this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                }
            } else if (handleName === 'stop') {
                if (newPrice > this.entryPrice) {
                    this.stopPrice = newPrice;
                    const profit = this.entryPrice - this.targetPrice;
                    const risk = this.stopPrice - this.entryPrice;
                    if (risk > 0) this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                }
            } else if ((handleName === 'left' || handleName === 'topLeft' || handleName === 'bottomLeft') && pixelX !== undefined) {
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    var entryLogical = this._getEntryLogical(chartState);
                    if (entryLogical !== null && chartState && typeof chartState.pixelToCoord === 'function') {
                        var c = chartState.pixelToCoord(pixelX, entryPixel.y);
                        if (c && c.logical !== null && c.logical !== undefined && !isNaN(c.logical)) {
                            var diff = entryLogical - c.logical;
                            if (diff >= 0.5) this.leftBars = diff;
                        }
                    }
                    const newLeftOffset = entryPixel.x - pixelX;
                    if (newLeftOffset >= 15) this.leftOffset = newLeftOffset;
                }
            } else if ((handleName === 'right' || handleName === 'topRight' || handleName === 'bottomRight') && pixelX !== undefined) {
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    var entryLogical = this._getEntryLogical(chartState);
                    if (entryLogical !== null && chartState && typeof chartState.pixelToCoord === 'function') {
                        var c = chartState.pixelToCoord(pixelX, entryPixel.y);
                        if (c && c.logical !== null && c.logical !== undefined && !isNaN(c.logical)) {
                            var diff = c.logical - entryLogical;
                            if (diff >= 0.5) this.rightBars = diff;
                        }
                    }
                    const newRightOffset = pixelX - entryPixel.x;
                    if (newRightOffset >= 15) this.rightOffset = newRightOffset;
                }
            }
            // Corner vertical resizing
            if (handleName === 'topLeft' || handleName === 'topRight') {
                if (newPrice > this.entryPrice) {
                    this.stopPrice = newPrice;
                    const profit = this.entryPrice - this.targetPrice;
                    const risk = this.stopPrice - this.entryPrice;
                    if (risk > 0) this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                }
            } else if (handleName === 'bottomLeft' || handleName === 'bottomRight') {
                if (newPrice < this.entryPrice) {
                    this.targetPrice = newPrice;
                    const profit = this.entryPrice - this.targetPrice;
                    const risk = this.stopPrice - this.entryPrice;
                    if (risk > 0) this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                }
            }
        }

        getPixels(chartState) {
            if (this.coords.length < 1) return [];
            const p = chartState.coordToPixel(this.coords[0]);
            return p ? [p] : [];
        }

        getFillShape(pixels) {
            if (!window.toolManager) return null;
            const chartState = window.toolManager.getChartState();
            const box = this.getHitBox(chartState);
            if (!box) return null;
            return [
                { x: box.left, y: box.top },
                { x: box.right, y: box.top },
                { x: box.right, y: box.bottom },
                { x: box.left, y: box.bottom }
            ];
        }

        drawHandles(ctx, pixels, isSelected) {
            // Overridden to do nothing so the default black-and-white handle box is not drawn in the middle.
        }

        getAnchorPoints(pixels) {
            return [];
        }

        translate(dx, dy, chartState) {
            if (this.locked) return;
            const oldEntry = this.entryPrice;
            super.translate(dx, dy, chartState);
            if (this.coords.length >= 1) {
                const newEntry = this.coords[0].price;
                const delta = newEntry - oldEntry;
                this.targetPrice += delta;
                this.stopPrice += delta;
                this.entryPrice = newEntry;
            }
        }
    }

    class PriceRange extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const midX = (p1.x + p2.x) / 2;
            ctx.beginPath();
            ctx.moveTo(midX, p1.y);
            ctx.lineTo(midX, p2.y);
            this.applyStyle(ctx);
            ctx.stroke();
            // Arrows
            ctx.beginPath();
            ctx.moveTo(midX - 5, p1.y + 8);
            ctx.lineTo(midX, p1.y);
            ctx.lineTo(midX + 5, p1.y + 8);
            this.applyStyle(ctx);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(midX - 5, p2.y - 8);
            ctx.lineTo(midX, p2.y);
            ctx.lineTo(midX + 5, p2.y - 8);
            this.applyStyle(ctx);
            ctx.stroke();
            // Label
            if (this.coords.length >= 2) {
                const diff = Math.abs((this.coords[0].price || 0) - (this.coords[1].price || 0)).toFixed(2);
                ctx.fillStyle = 'rgba(0,0,0,0.8)';
                ctx.fillRect(midX - 30, (p1.y + p2.y) / 2 - 10, 60, 20);
                ctx.fillStyle = '#fff';
                ctx.font = '11px Arial';
                ctx.fillText(`₹${diff}`, midX - 20, (p1.y + p2.y) / 2 + 4);
            }
        }
    }

    class DateRange extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const midY = (p1.y + p2.y) / 2;
            ctx.beginPath();
            ctx.moveTo(p1.x, midY);
            ctx.lineTo(p2.x, midY);
            this.applyStyle(ctx);
            ctx.stroke();
            // Vertical markers
            ctx.beginPath();
            ctx.moveTo(p1.x, midY - 10);
            ctx.lineTo(p1.x, midY + 10);
            ctx.moveTo(p2.x, midY - 10);
            ctx.lineTo(p2.x, midY + 10);
            this.applyStyle(ctx);
            ctx.stroke();
            // Label
            const bars = Math.abs(p2.x - p1.x);
            ctx.fillStyle = 'rgba(0,0,0,0.8)';
            ctx.fillRect((p1.x + p2.x) / 2 - 30, midY - 25, 60, 20);
            ctx.fillStyle = '#fff';
            ctx.font = '11px Arial';
            const barCount = Math.round(bars / 10);
            ctx.fillText(`${barCount} bars`, (p1.x + p2.x) / 2 - 22, midY - 11);
        }
    }

    class Measure extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Rectangle outline
            this.applyStyle(ctx);
            ctx.strokeRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
            // Info
            if (this.coords.length >= 2) {
                const priceDiff = ((this.coords[1].price || 0) - (this.coords[0].price || 0)).toFixed(2);
                const pct = this.coords[0].price ? (((this.coords[1].price - this.coords[0].price) / this.coords[0].price) * 100).toFixed(2) : '0';
                ctx.fillStyle = 'rgba(0,188,212,0.9)';
                ctx.fillRect(p2.x + 5, p2.y - 5, 90, 35);
                ctx.fillStyle = '#000';
                ctx.font = '11px Arial';
                ctx.fillText(`Δ ₹${priceDiff}`, p2.x + 10, p2.y + 10);
                ctx.fillText(`${pct}%`, p2.x + 10, p2.y + 24);
            }
        }
    }

    class PathDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this._pathLike = true;
            this._pathDone = false;
            this._lastClickTime = 0;
        }
        // Each click creates one vertex; never auto-completes via point count.
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return false; // Finish via double-click or ESC
        }
        finish() {
            this._pathDone = true;
        }
        isComplete() {
            return this._pathDone || this.coords.length >= (this.pointCount || (this.options && this.options.points) || 99);
        }
        draw(ctx, chartState, isSelected, isHovered) {
            const pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            ctx.beginPath();
            ctx.moveTo(pixels[0].x, pixels[0].y);
            for (let i = 1; i < pixels.length; i++) {
                ctx.lineTo(pixels[i].x, pixels[i].y);
            }
            // Live preview from last vertex to cursor while drawing
            if (this.currentPos && !this._pathDone && !this.model) {
                ctx.lineTo(this.currentPos.x, this.currentPos.y);
            }
            this.applyStyle(ctx);
            ctx.lineJoin = 'round';
            ctx.lineCap = 'round';
            ctx.stroke();
            ctx.globalAlpha = 1.0;
            // Vertex dots when selected/hovered
            if (isSelected || isHovered) {
                pixels.forEach(p => {
                    ctx.fillStyle = isSelected ? '#2962ff' : '#fff';
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, isSelected ? 4 : 3, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.strokeStyle = '#2962ff';
                    ctx.lineWidth = 1;
                    ctx.stroke();
                });
            }
        }
        isValid() { return this.coords.length >= 2; }

        // Every vertex draggable (anchor handles)
        getAnchorPoints(pixels) { return pixels || []; }
        getMidpoints(pixels) { return []; }

        // Segment hit-testing
        getEdgeSegments(pixels) {
            if (!pixels || pixels.length < 2) return [];
            var segs = [];
            for (var i = 0; i < pixels.length - 1; i++) {
                if (pixels[i] && pixels[i + 1]) segs.push({ p1: pixels[i], p2: pixels[i + 1] });
            }
            return segs;
        }

        // Insert a vertex on a segment: finds the nearest segment to the pixel and
        // inserts the corresponding coord after it.
        insertVertexAtPixel(pixel, chartState) {
            if (!chartState || !this.coords || this.coords.length < 1) return null;
            var pixels = this.getPixels(chartState);
            if (pixels.length < 1) return null;
            var coord = chartState.pixelToCoord(pixel.x, pixel.y);
            if (!coord) return null;

            var bestSeg = -1, bestDist = Infinity;
            for (var i = 0; i < pixels.length - 1; i++) {
                var seg = GeometryUtils.pointToLineSegment(pixel, pixels[i], pixels[i + 1]);
                if (seg.dist < bestDist) {
                    bestDist = seg.dist;
                    bestSeg = i;
                }
            }
            if (bestSeg < 0) {
                // Single-vertex path: just append
                this.coords.push({ logical: coord.logical, price: coord.price });
            } else {
                this.coords.splice(bestSeg + 1, 0, { logical: coord.logical, price: coord.price });
            }
            return coord;
        }

        // Delete a vertex by index (keeps at least 2 points)
        deleteVertexAt(index) {
            if (!this.coords || this.coords.length <= 2) return false;
            if (index < 0 || index >= this.coords.length) return false;
            this.coords.splice(index, 1);
            return true;
        }
    }



    // --- Tool Definitions Map ---

    const ToolDefinitions = {
        // Cursors
        cursor: { type: 'cursor', name: 'Cross', key: 'cursor', group: 'cursor' },
        dot: { type: 'cursor', name: 'Dot', key: 'dot', group: 'cursor' },
        arrow: { type: 'cursor', name: 'Arrow', key: 'arrow', group: 'cursor' },
        eraser: { type: 'action', name: 'Eraser', key: 'eraser', group: 'cursor' },

        // Lines
        trendline: { class: TrendLine, type: 'drawing', name: 'Trend Line', key: 'trendline', group: 'lines', points: 2 },
        ray: { class: Ray, type: 'drawing', name: 'Ray', key: 'ray', group: 'lines', points: 2 },
        extended: { class: ExtendedLine, type: 'drawing', name: 'Extended Line', key: 'extended', group: 'lines', points: 2 },
        infoline: { class: InfoLine, type: 'drawing', name: 'Info Line', key: 'infoline', group: 'lines', points: 2 },
        trend_angle: { class: TrendAngle, type: 'drawing', name: 'Trend Angle', key: 'trend_angle', group: 'lines', points: 2 },
        horizontal_line: { class: HorizontalLine, type: 'drawing', name: 'Horizontal Line', key: 'horizontal_line', group: 'lines', points: 1 },
        vertical_line: { class: VerticalLine, type: 'drawing', name: 'Vertical Line', key: 'vertical_line', group: 'lines', points: 1 },
        horizontal_ray: { class: HorizontalRay, type: 'drawing', name: 'Horizontal Ray', key: 'horizontal_ray', group: 'lines', points: 1 },
        cross_line: { class: CrossLine, type: 'drawing', name: 'Cross Line', key: 'cross_line', group: 'lines', points: 1 },
        channel: { class: ParallelChannel, type: 'drawing', name: 'Parallel Channel', key: 'channel', group: 'lines', points: 3 },
        flat_top_channel: { class: FlatTopChannel, type: 'drawing', name: 'Flat Top/Bottom Channel', key: 'flat_top_channel', group: 'lines', points: 3 },
        flat_bottom_channel: { class: FlatBottomChannel, type: 'drawing', name: 'Flat Top/Bottom Channel', key: 'flat_bottom_channel', group: 'lines', points: 3 },
        disjoint_channel: { class: DisjointChannel, type: 'drawing', name: 'Disjoint Channel', key: 'disjoint_channel', group: 'lines', points: 4 },
        regression_trend: { class: RegressionTrend, type: 'drawing', name: 'Regression Trend', key: 'regression_trend', group: 'lines', points: 2 },
        pitchfork: { class: Pitchfork, type: 'drawing', name: 'Pitchfork', key: 'pitchfork', group: 'lines', points: 3 },
        schiff_pitchfork: { class: SchiffPitchfork, type: 'drawing', name: 'Schiff Pitchfork', key: 'schiff_pitchfork', group: 'lines', points: 3 },
        modified_schiff_pitchfork: { class: ModifiedSchiffPitchfork, type: 'drawing', name: 'Modified Schiff Pitchfork', key: 'modified_schiff_pitchfork', group: 'lines', points: 3 },
        inside_pitchfork: { class: InsidePitchfork, type: 'drawing', name: 'Inside Pitchfork', key: 'inside_pitchfork', group: 'lines', points: 3 },

        // Shapes
        rectangle: { class: Rectangle, type: 'drawing', name: 'Rectangle', key: 'rectangle', group: 'shapes', points: 2, dragBased: true },
        circle: { class: Circle, type: 'drawing', name: 'Circle', key: 'circle', group: 'shapes', points: 2, dragBased: true },
        ellipse: { class: Ellipse, type: 'drawing', name: 'Ellipse', key: 'ellipse', group: 'shapes', points: 2, dragBased: true },
        triangle: { class: TriangleShape, type: 'drawing', name: 'Triangle', key: 'triangle', group: 'shapes', points: 3 },
        path: { class: PathDrawing, type: 'drawing', name: 'Path', key: 'path', group: 'shapes', points: 99 },
        brush: { class: BrushDrawing, type: 'drawing', name: 'Brush', key: 'brush', group: 'shapes', points: 0, dragBased: true },
        highlighter: { class: HighlighterDrawing, type: 'drawing', name: 'Highlighter', key: 'highlighter', group: 'shapes', points: 0, dragBased: true },

        // Text & Annotations
        text: { class: TextDrawing, type: 'drawing', name: 'Text', key: 'text', group: 'text', points: 1 },
        anchored_text: { class: AnchoredText, type: 'drawing', name: 'Anchored Text', key: 'anchored_text', group: 'text', points: 1 },
        note: { class: TextNote, type: 'drawing', name: 'Note', key: 'note', group: 'text', points: 1 },
        // PHASE D1 (BUG-006): OBJECT_DEFS.callout is the authoritative
        // implementation and declares points:2 with two anchorMeta entries
        // and hasTail:true (anchor point + text box). The registry said 1,
        // so `callout` and `rich_callout` drove the SAME class with
        // different creation contracts. 2 matches the implementation.
        callout: { class: Callout, type: 'drawing', name: 'Callout', key: 'callout', group: 'text', points: 2 },
        price_label: { class: PriceLabel, type: 'drawing', name: 'Price Label', key: 'price_label', group: 'text', points: 1 },
        arrow_marker: { class: ArrowMarker, type: 'drawing', name: 'Arrow Marker', key: 'arrow_marker', group: 'text', points: 2 },

        // Fibonacci & Gann (11 Fib tools + Gann)
        fib_retracement: { class: FibRetracement, type: 'drawing', name: 'Fib Retracement', key: 'fib_retracement', group: 'fib', points: 2 },
        fib_trend_ext: { class: FibExtension, type: 'drawing', name: 'Fib Extension', key: 'fib_trend_ext', group: 'fib', points: 3 },
        fib_channel: { class: FibChannel, type: 'drawing', name: 'Fib Channel', key: 'fib_channel', group: 'fib', points: 3 },
        fib_fan: { class: FibFan, type: 'drawing', name: 'Fib Fan', key: 'fib_fan', group: 'fib', points: 2 },
        fib_time_zone: { class: FibTimeZone, type: 'drawing', name: 'Fib Time Zone', key: 'fib_time_zone', group: 'fib', points: 2 },
        fib_circles: { class: FibCircles, type: 'drawing', name: 'Fib Circles', key: 'fib_circles', group: 'fib', points: 2 },
        fib_arcs: { class: FibArcs, type: 'drawing', name: 'Fib Arcs', key: 'fib_arcs', group: 'fib', points: 2 },
        fib_speed_resistance: { class: FibSpeedResistance, type: 'drawing', name: 'Speed Resistance', key: 'fib_speed_resistance', group: 'fib', points: 2 },
        fib_spiral: { class: FibSpiral, type: 'drawing', name: 'Fib Spiral', key: 'fib_spiral', group: 'fib', points: 2 },
        fib_wedge: { class: FibWedge, type: 'drawing', name: 'Fib Wedge', key: 'fib_wedge', group: 'fib', points: 3 },
        pitchfan: { class: Pitchfan, type: 'drawing', name: 'Pitchfan', key: 'pitchfan', group: 'fib', points: 3 },
        gann_box: { class: GannBox, type: 'drawing', name: 'Gann Box', key: 'gann_box', group: 'fib', points: 2 },
        gann_square: { class: GannSquare, type: 'drawing', name: 'Gann Square', key: 'gann_square', group: 'fib', points: 2 },
        gann_square_fixed: { class: GannSquareFixed, type: 'drawing', name: 'Gann Square Fixed', key: 'gann_square_fixed', group: 'fib', points: 2 },
        gann_fan: { class: GannFan, type: 'drawing', name: 'Gann Fan', key: 'gann_fan', group: 'fib', points: 2 },

        // Patterns (Phase 3.7)
        // Harmonic Patterns
        gartley: { class: Gartley, type: 'drawing', name: 'Gartley', key: 'gartley', group: 'patterns', points: 5 },
        butterfly: { class: Butterfly, type: 'drawing', name: 'Butterfly', key: 'butterfly', group: 'patterns', points: 5 },
        bat: { class: Bat, type: 'drawing', name: 'Bat', key: 'bat', group: 'patterns', points: 5 },
        crab: { class: Crab, type: 'drawing', name: 'Crab', key: 'crab', group: 'patterns', points: 5 },
        deep_crab: { class: DeepCrab, type: 'drawing', name: 'Deep Crab', key: 'deep_crab', group: 'patterns', points: 5 },
        shark: { class: Shark, type: 'drawing', name: 'Shark', key: 'shark', group: 'patterns', points: 5 },
        cypher: { class: Cypher, type: 'drawing', name: 'Cypher', key: 'cypher', group: 'patterns', points: 5 },
        abcd: { class: Abcd, type: 'drawing', name: 'ABCD', key: 'abcd', group: 'patterns', points: 4 },
        // Chart Patterns
        head_and_shoulders: { class: HeadAndShoulders, type: 'drawing', name: 'Head & Shoulders', key: 'head_and_shoulders', group: 'patterns', points: 5 },
        inverse_head_and_shoulders: { class: InverseHeadAndShoulders, type: 'drawing', name: 'Inverse Head & Shoulders', key: 'inverse_head_and_shoulders', group: 'patterns', points: 5 },
        double_top: { class: DoubleTop, type: 'drawing', name: 'Double Top', key: 'double_top', group: 'patterns', points: 3 },
        double_bottom: { class: DoubleBottom, type: 'drawing', name: 'Double Bottom', key: 'double_bottom', group: 'patterns', points: 3 },
        triple_top: { class: TripleTop, type: 'drawing', name: 'Triple Top', key: 'triple_top', group: 'patterns', points: 5 },
        triple_bottom: { class: TripleBottom, type: 'drawing', name: 'Triple Bottom', key: 'triple_bottom', group: 'patterns', points: 5 },
        // Triangle Patterns
        ascending_triangle: { class: AscendingTriangle, type: 'drawing', name: 'Ascending Triangle', key: 'ascending_triangle', group: 'patterns', points: 4 },
        descending_triangle: { class: DescendingTriangle, type: 'drawing', name: 'Descending Triangle', key: 'descending_triangle', group: 'patterns', points: 4 },
        symmetrical_triangle: { class: SymmetricalTriangle, type: 'drawing', name: 'Symmetrical Triangle', key: 'symmetrical_triangle', group: 'patterns', points: 4 },
        expanding_triangle: { class: ExpandingTriangle, type: 'drawing', name: 'Expanding Triangle', key: 'expanding_triangle', group: 'patterns', points: 4 },
        // Wedge Patterns
        rising_wedge: { class: RisingWedge, type: 'drawing', name: 'Rising Wedge', key: 'rising_wedge', group: 'patterns', points: 4 },
        falling_wedge: { class: FallingWedge, type: 'drawing', name: 'Falling Wedge', key: 'falling_wedge', group: 'patterns', points: 4 },
        // Channels
        ascending_channel: { class: AscendingChannel, type: 'drawing', name: 'Ascending Channel', key: 'ascending_channel', group: 'patterns', points: 4 },
        descending_channel: { class: DescendingChannel, type: 'drawing', name: 'Descending Channel', key: 'descending_channel', group: 'patterns', points: 4 },
        // Legacy aliases (now point to proper classes)
        xabcd_pattern: { class: XabcdPattern, type: 'drawing', name: 'XABCD Pattern', key: 'xabcd_pattern', group: 'patterns', points: 5 },
        cypher_pattern: { class: Cypher, type: 'drawing', name: 'Cypher Pattern', key: 'cypher_pattern', group: 'patterns', points: 5 },
        abcd_pattern: { class: Abcd, type: 'drawing', name: 'ABCD Pattern', key: 'abcd_pattern', group: 'patterns', points: 4 },
        triangle_pattern: { class: TriangleShape, type: 'drawing', name: 'Triangle Pattern', key: 'triangle_pattern', group: 'patterns', points: 3 },
        // Elliott Wave (Phase 3.6)
        elliott_impulse: { class: ImpulseWave, type: 'drawing', name: 'Elliott Impulse (12345)', key: 'elliott_impulse', group: 'patterns', points: 6 },
        elliott_correction: { class: CorrectiveWave, type: 'drawing', name: 'Elliott Correction (ABC)', key: 'elliott_correction', group: 'patterns', points: 4 },
        elliott_triangle: { class: ElliottTriangle, type: 'drawing', name: 'Elliott Triangle (ABCDE)', key: 'elliott_triangle', group: 'patterns', points: 6 },
        elliott_double_combo: { class: ElliottDoubleCombo, type: 'drawing', name: 'Elliott Double Combo', key: 'elliott_double_combo', group: 'patterns', points: 5 },
        elliott_triple_combo: { class: ElliottTripleCombo, type: 'drawing', name: 'Elliott Triple Combo', key: 'elliott_triple_combo', group: 'patterns', points: 7 },
        elliott_flat: { class: ElliottFlat, type: 'drawing', name: 'Elliott Flat', key: 'elliott_flat', group: 'patterns', points: 4 },
        elliott_zigzag: { class: ElliottZigZag, type: 'drawing', name: 'Elliott ZigZag', key: 'elliott_zigzag', group: 'patterns', points: 4 },
        elliott_combination: { class: ElliottCombination, type: 'drawing', name: 'Elliott Combination', key: 'elliott_combination', group: 'patterns', points: 2 },

        // Prediction & Measurement (single-click position tools)
        long_position: { class: LongPosition, type: 'drawing', name: 'Long Position', key: 'long_position', group: 'prediction', points: 1 },
        short_position: { class: ShortPosition, type: 'drawing', name: 'Short Position', key: 'short_position', group: 'prediction', points: 1 },
        price_range: { class: PriceRange, type: 'drawing', name: 'Price Range', key: 'price_range', group: 'prediction', points: 2 },
        date_range: { class: DateRange, type: 'drawing', name: 'Date Range', key: 'date_range', group: 'prediction', points: 2 },
        date_price_range: { class: DateRange, type: 'drawing', name: 'Date & Price Range', key: 'date_price_range', group: 'prediction', points: 2 },

        // Phase 3.9: Rich Objects
        // Text
        text_note: { class: TextNote, type: 'drawing', name: 'Text Note', key: 'text_note', group: 'objects', points: 1 },
        rich_anchored_text: { class: AnchoredText, type: 'drawing', name: 'Anchored Text', key: 'rich_anchored_text', group: 'objects', points: 1 },
        rich_callout: { class: Callout, type: 'drawing', name: 'Callout', key: 'rich_callout', group: 'objects', points: 2 },
        balloon: { class: Balloon, type: 'drawing', name: 'Balloon', key: 'balloon', group: 'objects', points: 2 },
        arrow_label: { class: ArrowLabel, type: 'drawing', name: 'Arrow Label', key: 'arrow_label', group: 'objects', points: 2 },
        // Icons & Emojis
        emoji: { class: EmojiDrawing, type: 'drawing', name: 'Emoji', key: 'emoji', group: 'objects', points: 1 },
        icon: { class: IconDrawing, type: 'drawing', name: 'Icon', key: 'icon', group: 'objects', points: 1 },
        symbol: { class: SymbolDrawing, type: 'drawing', name: 'Symbol', key: 'symbol', group: 'objects', points: 1 },
        // Images
        image: { class: ImageDrawing, type: 'drawing', name: 'Image', key: 'image', group: 'objects', points: 2 },
        watermark: { class: WatermarkDrawing, type: 'drawing', name: 'Watermark', key: 'watermark', group: 'objects', points: 2 },
        logo: { class: LogoDrawing, type: 'drawing', name: 'Logo', key: 'logo', group: 'objects', points: 2 },
        // Stickers
        sticker_buy: { class: StickerBuy, type: 'drawing', name: 'Buy Sticker', key: 'sticker_buy', group: 'objects', points: 1 },
        sticker_sell: { class: StickerSell, type: 'drawing', name: 'Sell Sticker', key: 'sticker_sell', group: 'objects', points: 1 },
        sticker_long: { class: StickerLong, type: 'drawing', name: 'Long Sticker', key: 'sticker_long', group: 'objects', points: 1 },
        sticker_short: { class: StickerShort, type: 'drawing', name: 'Short Sticker', key: 'sticker_short', group: 'objects', points: 1 },
        sticker_target: { class: StickerTarget, type: 'drawing', name: 'Target Sticker', key: 'sticker_target', group: 'objects', points: 1 },
        sticker_stop: { class: StickerStop, type: 'drawing', name: 'Stop Sticker', key: 'sticker_stop', group: 'objects', points: 1 },
        sticker_star: { class: StickerStar, type: 'drawing', name: 'Star Sticker', key: 'sticker_star', group: 'objects', points: 1 },
        sticker_pin: { class: StickerPin, type: 'drawing', name: 'Pin Sticker', key: 'sticker_pin', group: 'objects', points: 1 },
        sticker_check: { class: StickerCheck, type: 'drawing', name: 'Check Sticker', key: 'sticker_check', group: 'objects', points: 1 },
        sticker_warning: { class: StickerWarning, type: 'drawing', name: 'Warning Sticker', key: 'sticker_warning', group: 'objects', points: 1 },

        // Utilities
        measure: { class: Measure, type: 'drawing', name: 'Measure', key: 'measure', group: 'utils', points: 2 },
        zoom: { class: TwoPointDrawing, type: 'drawing', name: 'Zoom', key: 'zoom', group: 'utils', points: 2 }
    };

    // Global Helper Functions (called from HTML)

    // Expose the tool registry for QA/debugging. Read-only export of an
    // existing object -- no behaviour change; makes the 112 registered
    // tools reachable from a test page or the console.
    window.__ToolDefinitions = ToolDefinitions;

    window.toolManager = null;

    window.initToolManager = function () {
        if (!window.toolManager) {
            window.toolManager = new ToolManager('drawing-canvas-container', 'drawing-canvas');
            console.log("ToolManager initialized");
            if (window.updateMagnetUI) window.updateMagnetUI();
        }
        // Subscribe to chart pan/zoom; retry if bigChart not ready yet
        (function trySubscribe() {
            if (window._bigChartSubscribed) return;
            if (!window.bigChart) {
                setTimeout(trySubscribe, 500);
                return;
            }
            try {
                window._bigChartSubscribed = true;
                window.bigChart.timeScale().subscribeVisibleTimeRangeChange(function () {
                    var tm = window.toolManager;
                    if (!tm) return;
                    tm._needsRedraw = true;
                    if (tm.engine) {
                        try {
                            var range = window.bigChart.timeScale().getVisibleRange();
                            tm.engine.eventBus.emit('viewport:changed', { from: range.from, to: range.to });
                        } catch (_) {}
                    }
                });
                window.bigChart.subscribeCrosshairMove(function () {
                    if (window.toolManager) window.toolManager._needsRedraw = true;
                });
            } catch (e) {
                window._bigChartSubscribed = false;
                setTimeout(trySubscribe, 1000);
            }
        })();
        if (!window._drawingSyncLoop) {
            window._drawingSyncLoop = true;
            (function syncLoop() {
                var tm = window.toolManager;
                if (tm) {
                    // Always sync the viewport every frame so drawings correctly follow
                    // both X-axis (pan/zoom) and Y-axis (price scale compression) changes.
                    // We detect changes via direct LightweightCharts API to avoid stale cache.
                    var chart = window.bigChart || window.chart;
                    var series = window.bigCandleSeries || window.candleSeries || window.mainSeries;
                    var shouldRedraw = tm._needsRedraw;
                    if (chart && series && window.coordinateMapper) {
                        var lr = chart.timeScale().getVisibleLogicalRange();
                        if (lr) {
                            var h = tm.canvas ? tm.canvas.height / (window.devicePixelRatio || 1) : 500;
                            var priceTop = series.coordinateToPrice(0);
                            var priceBottom = series.coordinateToPrice(h);
                            var w = chart.timeScale().width() || 500;
                            var vp = window.coordinateMapper.viewport;
                            if (priceTop !== null && priceBottom !== null) {
                                if (vp.logicalFrom !== lr.from || vp.logicalTo !== lr.to ||
                                    vp.priceTop !== priceTop || vp.priceBottom !== priceBottom ||
                                    vp.width !== w || vp.height !== h) {
                                    window.coordinateMapper.updateViewport(lr.from, lr.to, priceTop, priceBottom, w, h);
                                    shouldRedraw = true;
                                }
                            }
                        }
                    }
                    if (shouldRedraw) {
                        tm._needsRedraw = false;
                        tm.redraw();
                    }
                }
                requestAnimationFrame(syncLoop);
            })();
        }
    };

    window.toggleSubmenu = function (id, event) {
        console.log(`[EVENT] toggleSubmenu called for id: ${id}`);
        if (window.toolManager) window.toolManager.logDrawingState('toggleSubmenu_Start');
        if (event) {
            // FIRST check: if the click is on a submenu-item, do nothing — let activateTool handle it
            if (event.target && event.target.closest && event.target.closest('.submenu-item')) {
                return;
            }
            event.stopPropagation();
            event.stopImmediatePropagation();
            event.preventDefault();
        }

        if (!window.toolManager) {
            console.warn("ToolManager missing on click, initializing...");
            window.initToolManager();
        }

        const submenu = document.getElementById(id);
        if (!submenu) {
            console.error("Submenu element not found:", id);
            return;
        }

        const wasVisible = submenu.classList.contains('visible');
        document.querySelectorAll('.drawing-submenu').forEach(el => el.classList.remove('visible'));
        if (!wasVisible) {
            submenu.classList.add('visible');
        }
        if (window.toolManager) window.toolManager.logDrawingState('toggleSubmenu_End');
    };

    let lastActivateTime = 0;
    window.activateTool = function (key, event) {
        console.log(`[EVENT] activateTool called for key: ${key}`);
        
        const now = Date.now();
        if (now - lastActivateTime < 100) {
            console.log(`[EVENT] activateTool duplicate call ignored for key: ${key}`);
            if (event) {
                event.stopPropagation();
                event.stopImmediatePropagation();
                event.preventDefault();
            }
            return;
        }
        lastActivateTime = now;

        if (window.toolManager) window.toolManager.logDrawingState('activateTool_Start');
        if (event) {
            event.stopPropagation();
            event.stopImmediatePropagation();
            event.preventDefault();
        }

        if (!window.toolManager) window.initToolManager();

        var def = ToolDefinitions[key];
        if (def) {
            // Immediately hide submenus to prevent click-through to chart behind
            document.querySelectorAll('.drawing-submenu').forEach(function(el) {
                el.classList.remove('visible');
            });
            window.toolManager.setTool(def);
            window.toolManager._justActivatedTool = true;
            setTimeout(function() {
                if (window.toolManager) window.toolManager._justActivatedTool = false;
            }, 200);
            console.log('[activateTool] Tool set:', key);
        } else {
            console.warn('Tool not found:', key);
        }
    };

    // Utils
    window.updateMagnetUI = function() {
        if (!window.toolManager) return;
        var isActive = window.toolManager.engine.snapping.isActive;
        var el = document.querySelector('.toolbar-item[onclick*="toggleMagnet"]');
        if (el) {
            el.setAttribute('data-active', isActive ? 'true' : 'false');
            el.setAttribute('title', isActive ? 'Magnet ON (Strong)' : 'Magnet OFF');
        }
    };

    window.toggleMagnet = function (el) {
        if (!window.toolManager) return;
        window.toolManager.engine.snapping.toggleMode();
        window.updateMagnetUI();
    };

    window.toggleStayMode = function (el) {
        if (!window.toolManager) return;
        window.toolManager.stayMode = !window.toolManager.stayMode;
        el.setAttribute('data-active', window.toolManager.stayMode);
    };

    window.toggleLockAll = function (el) {
        if (!window.toolManager) return;
        window.toolManager.locked = !window.toolManager.locked;
        el.setAttribute('data-active', window.toolManager.locked);
    };

    window.toggleHideAll = function (el) {
        if (!window.toolManager) return;
        window.toolManager.hidden = !window.toolManager.hidden;
        el.setAttribute('data-active', window.toolManager.hidden);
        window.toolManager.redraw();
    };

    // Start
    document.addEventListener('DOMContentLoaded', () => {
        window.initToolManager();
    });

    // Register all drawing classes (must be after class definitions)
    window.DrawingClasses = {
        HorizontalRay: HorizontalRay,
        Ray: Ray,
        HighlighterDrawing: HighlighterDrawing,
        BrushDrawing: BrushDrawing,
        ArrowMarker: ArrowMarker,
        PriceLabel: PriceLabel,
        ParallelChannel: ParallelChannel,
        FlatTopChannel: FlatTopChannel,
        FlatBottomChannel: FlatBottomChannel,
        DisjointChannel: DisjointChannel,
        RegressionTrend: RegressionTrend,
        BaseChannelDrawing: BaseChannelDrawing,
        PriceRange: PriceRange,
        Rectangle: Rectangle,
        ShortPosition: ShortPosition,
        CrossLine: CrossLine,
        GannBox: GannBox,
        GannSquare: GannSquare,
        GannSquareFixed: GannSquareFixed,
        GannFan: GannFan,
        Ellipse: Ellipse,
        TwoPointDrawing: TwoPointDrawing,
        Circle: Circle,
        Measure: Measure,
        FibRetracement: FibRetracement,
        FibExtension: FibExtension,
        FibChannel: FibChannel,
        FibFan: FibFan,
        FibTimeZone: FibTimeZone,
        FibCircles: FibCircles,
        FibArcs: FibArcs,
        FibSpeedResistance: FibSpeedResistance,
        FibSpiral: FibSpiral,
        FibWedge: FibWedge,
        Pitchfan: Pitchfan,
        HorizontalLine: HorizontalLine,
        DateRange: DateRange,
        TextDrawing: TextDrawing,
        LongPosition: LongPosition,
        TrendLine: TrendLine,
        ExtendedLine: ExtendedLine,
        VerticalLine: VerticalLine,
        BaseDrawing: BaseDrawing,
        TrendAngle: TrendAngle,
        TriangleShape: TriangleShape,
        InfoLine: InfoLine,
        PathDrawing: PathDrawing,
        Pitchfork: Pitchfork,
        SchiffPitchfork: SchiffPitchfork,
        ModifiedSchiffPitchfork: ModifiedSchiffPitchfork,
        InsidePitchfork: InsidePitchfork,
        BasePitchforkDrawing: BasePitchforkDrawing,
        // Elliott Wave (Phase 3.6)
        ImpulseWave: ImpulseWave,
        CorrectiveWave: CorrectiveWave,
        ElliottTriangle: ElliottTriangle,
        ElliottDoubleCombo: ElliottDoubleCombo,
        ElliottTripleCombo: ElliottTripleCombo,
        ElliottFlat: ElliottFlat,
        ElliottZigZag: ElliottZigZag,
        ElliottCombination: ElliottCombination,
        // Pattern Tools (Phase 3.7)
        Gartley: Gartley, XabcdPattern: XabcdPattern, Butterfly: Butterfly, Bat: Bat, Crab: Crab,
        DeepCrab: DeepCrab, Shark: Shark, Cypher: Cypher, Abcd: Abcd,
        HeadAndShoulders: HeadAndShoulders, InverseHeadAndShoulders: InverseHeadAndShoulders,
        DoubleTop: DoubleTop, DoubleBottom: DoubleBottom,
        TripleTop: TripleTop, TripleBottom: TripleBottom,
        AscendingTriangle: AscendingTriangle, DescendingTriangle: DescendingTriangle,
        SymmetricalTriangle: SymmetricalTriangle, ExpandingTriangle: ExpandingTriangle,
        RisingWedge: RisingWedge, FallingWedge: FallingWedge,
        AscendingChannel: AscendingChannel, DescendingChannel: DescendingChannel,
        // Phase 3.9: Rich Objects
        TextNote: TextNote, AnchoredText: AnchoredText, Callout: Callout, Balloon: Balloon, ArrowLabel: ArrowLabel,
        EmojiDrawing: EmojiDrawing, IconDrawing: IconDrawing, SymbolDrawing: SymbolDrawing,
        ImageDrawing: ImageDrawing, WatermarkDrawing: WatermarkDrawing, LogoDrawing: LogoDrawing,
        StickerBuy: StickerBuy, StickerSell: StickerSell, StickerLong: StickerLong, StickerShort: StickerShort,
        StickerTarget: StickerTarget, StickerStop: StickerStop, StickerStar: StickerStar, StickerPin: StickerPin,
        StickerCheck: StickerCheck, StickerWarning: StickerWarning
    };

} catch (err) {
    console.error("CRITICAL ERROR in drawings.js:", err);
}

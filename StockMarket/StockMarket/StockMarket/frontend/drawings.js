// drawings.js
console.log("Loading drawings.js...");
try {


    /**
     * ToolManager: Orchestrates the drawing interactions.
     */
    class ToolManager {
        constructor(canvasContainerId, canvasId) {
            this.container = document.getElementById(canvasContainerId);
            this.canvas = document.getElementById(canvasId);
            this.ctx = this.canvas.getContext('2d');
            this.drawings = [];
            this.activeTool = null;
            this.isDrawing = false;
            this.currentDrawing = null;
            this.activeGroup = null; // Track which group is active

            this.magnetEnabled = false;
            this.stayMode = false;
            this.locked = false;
            this.hidden = false;
            this.selectedDrawing = null; // Track currently selected drawing for deletion
            this.hoveredDrawing = null; // Track currently hovered drawing for visual feedback

            // Dragging state for position tool handles
            this.isDraggingHandle = false;
            this.draggedHandle = null; // 'target', 'entry', 'stop', 'left', 'right'
            this.dragDrawing = null;

            // Chart panning state (Click-vs-Drag)
            this.mouseDownPos = null;
            this.isDraggingChart = false;

            this.init();
        }

        init() {
            this.resizeCanvas();
            window.addEventListener('resize', () => this.resizeCanvas());

            // Use pointer events for better handling
            this.canvas.addEventListener('mousedown', (e) => this.onMouseDown(e));
            this.canvas.addEventListener('mousemove', (e) => this.onMouseMove(e));
            this.canvas.addEventListener('mouseup', (e) => this.onMouseUp(e));

            // Pass wheel events through to the chart for zooming
            this.canvas.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });

            // Pass right-click through for context menus
            this.canvas.addEventListener('contextmenu', (e) => {
                // Let right-click pass through to chart
                e.stopPropagation();
            });

            // Global key listener for ESC (reset) and Delete/Backspace (delete selected)
            window.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    this.selectedDrawing = null;
                    this.resetToDefault();
                }
                if ((e.key === 'Delete' || e.key === 'Backspace') && this.selectedDrawing) {
                    this.deleteSelected();
                    e.preventDefault();
                }
            });

            // Close submenus on click outside
            document.addEventListener('click', (e) => {
                if (!e.target.closest('.toolbar-item')) {
                    this.closeAllSubmenus();
                }
            });

            // PARENT INTERACTION LAYER (Crucial for "Click-Through" behavior)
            // We listen on the chart-container in Capture phase to intercept events 
            // before they hit the Chart (when in selection mode) or Canvas.
            const chartContainer = document.getElementById('chart-container');
            if (chartContainer) {
                chartContainer.addEventListener('mousedown', (e) => this.handleContainerMouseDown(e), { capture: true });
                chartContainer.addEventListener('mousemove', (e) => this.handleContainerMouseMove(e), { capture: true });
                chartContainer.addEventListener('mouseup', (e) => this.handleContainerMouseUp(e), { capture: true });
            }
        }

        // --- Container Level Handlers (Capture Phase) ---
        // These run BEFORE the Chart or Canvas gets the event.

        handleContainerMouseDown(e) {
            // Only intervene if we are in Selection Mode (Cursor Tool / No active tool)
            const isSelectionMode = (!this.activeTool || this.activeTool.type === 'cursor') && !this.isDrawing;
            if (!isSelectionMode) return; // Let Canvas handle Drawing Mode

            // Calculate Pos relative to Canvas (which is same size as Container)
            const rect = this.canvas.getBoundingClientRect();
            const pos = {
                x: e.clientX - rect.left,
                y: e.clientY - rect.top
            };
            const chartState = this.getChartState();

            // First check if clicking on a handle of selected drawing (for dragging)
            if (this.selectedDrawing && this.selectedDrawing.hitTestHandle) {
                const handleHit = this.selectedDrawing.hitTestHandle(pos, chartState);
                if (handleHit) {
                    // Start dragging this handle
                    this.isDraggingHandle = true;
                    this.draggedHandle = handleHit;
                    this.dragDrawing = this.selectedDrawing;
                    e.stopPropagation();
                    e.preventDefault();
                    return;
                }
            }

            // Hit Test for drawings
            const hitDrawing = this.hitTest(pos, chartState);

            if (hitDrawing) {
                // User clicked a drawing!
                // 1. Select it
                this.selectedDrawing = hitDrawing;
                this.redraw();

                // 2. STOP event from reaching the Chart (prevents Panning)
                e.stopPropagation();
            } else {
                // User clicked empty space
                // 1. Deselect if needed
                if (this.selectedDrawing) {
                    this.selectedDrawing = null;
                    this.redraw();
                }
                // 2. Do NOTHING. Let event propagate to Chart. Chart handles panning.
            }
        }

        handleContainerMouseMove(e) {
            const rect = this.canvas.getBoundingClientRect();
            const pos = {
                x: e.clientX - rect.left,
                y: e.clientY - rect.top
            };
            const chartState = this.getChartState();
            const container = document.getElementById('chart-container');

            // Handle dragging
            if (this.isDraggingHandle && this.dragDrawing && chartState) {
                // Convert pixel position to price
                const coord = chartState.pixelToCoord(pos.x, pos.y);
                if (coord && coord.price !== null && coord.price !== undefined) {
                    // Update the drawing based on which handle is being dragged
                    // Pass pixelX for horizontal resize
                    this.dragDrawing.updateHandle(this.draggedHandle, coord.price, chartState, pos.x);
                    this.redraw();
                }
                e.stopPropagation();
                // Show appropriate cursor based on drag type
                if (this.draggedHandle === 'left' || this.draggedHandle === 'right') {
                    if (container) container.style.cursor = 'ew-resize';
                } else {
                    if (container) container.style.cursor = 'ns-resize';
                }
                return;
            }

            const isSelectionMode = (!this.activeTool || this.activeTool.type === 'cursor') && !this.isDrawing;
            if (!isSelectionMode) {
                // Clear hover when not in selection mode
                if (this.hoveredDrawing) {
                    this.hoveredDrawing = null;
                    this.redraw();
                }
                return;
            }

            // Check if hovering over a handle of selected drawing
            if (this.selectedDrawing && this.selectedDrawing.hitTestHandle) {
                const handleHit = this.selectedDrawing.hitTestHandle(pos, chartState);
                if (handleHit) {
                    // Show appropriate cursor based on handle type
                    if (handleHit === 'left' || handleHit === 'right') {
                        if (container) container.style.cursor = 'ew-resize';
                    } else {
                        if (container) container.style.cursor = 'ns-resize';
                    }
                    return;
                }
            }

            // Check Hover for drawings
            const hitDrawing = this.hitTest(pos, chartState);

            // Update hovered drawing state
            const previousHovered = this.hoveredDrawing;
            this.hoveredDrawing = hitDrawing;

            if (hitDrawing) {
                // Hovering drawing -> Pointer cursor
                if (container) container.style.cursor = 'pointer';
            } else {
                // Not hovering -> Let Chart decide (remove override)
                if (container) container.style.cursor = '';
            }

            // Redraw if hover state changed
            if (previousHovered !== this.hoveredDrawing) {
                this.redraw();
            }
        }

        handleContainerMouseUp(e) {
            if (this.isDraggingHandle) {
                this.isDraggingHandle = false;
                this.draggedHandle = null;
                this.dragDrawing = null;
                this.redraw();
            }
        }

        onWheel(e) {
            // Pass wheel events through to the chart underneath
            // This allows zooming/scrolling to work even when drawing canvas is active
            const chartElement = document.getElementById('chart');
            if (chartElement) {
                const isDrawingToolActive = this.activeTool && this.activeTool.type !== 'cursor';

                // If drawing tool is active:
                // - Regular wheel scroll: force shiftKey (Horizontal Panning)
                // - Ctrl + wheel scroll: preserve ctrlKey (Zooming / Scaling)
                // This allows the user to both navigate and adjust scale while mid-drawing.
                const forceShift = isDrawingToolActive && !e.ctrlKey;

                // Create a new wheel event and dispatch it to the chart
                const wheelEvent = new WheelEvent('wheel', {
                    bubbles: true,
                    cancelable: true,
                    clientX: e.clientX,
                    clientY: e.clientY,
                    deltaX: e.deltaX,
                    deltaY: e.deltaY,
                    deltaZ: e.deltaZ,
                    deltaMode: e.deltaMode,
                    ctrlKey: e.ctrlKey,
                    shiftKey: forceShift ? true : e.shiftKey,
                    altKey: e.altKey,
                    metaKey: e.metaKey
                });

                chartElement.dispatchEvent(wheelEvent);

                // If we are in drawing mode, prevent browser default to ensure smooth chart interaction
                if (isDrawingToolActive) {
                    e.preventDefault();
                }
            }
        }

        resizeCanvas() {
            if (this.container && this.canvas) {
                this.canvas.width = this.container.offsetWidth;
                this.canvas.height = this.container.offsetHeight;
                this.redraw();
            }
        }

        getChartState() {
            if (window.getChartCoordinateAPI) {
                return window.getChartCoordinateAPI();
            }
            return null;
        }

        // --- Tool Selection Logic ---

        setTool(toolDef) {
            if (this.locked) return;

            this.activeTool = toolDef;
            this.cancelDrawing(); // Reset any current drawing
            this.updateUI();
            this.closeAllSubmenus();

            // Get the chart container to toggle drawing mode
            const chartContainer = document.getElementById('chart-container');

            // Set cursor and drawing mode based on tool type
            if (toolDef.type === 'cursor') {
                this.setCursor('default');
                // Enable selection mode for cursor tools - allows selecting drawings
                if (chartContainer) {
                    chartContainer.classList.remove('drawing-mode-active');
                    chartContainer.classList.add('selection-mode-active');
                }
            } else if (toolDef.type === 'text') {
                this.setCursor('text');
                if (chartContainer) {
                    chartContainer.classList.add('drawing-mode-active');
                    chartContainer.classList.remove('selection-mode-active');
                }
            } else if (toolDef.type === 'drawing') {
                this.setCursor('crosshair');
                // Enable drawing mode - canvas will intercept mouse events
                if (chartContainer) {
                    chartContainer.classList.add('drawing-mode-active');
                    chartContainer.classList.remove('selection-mode-active');
                }
            } else {
                this.setCursor('crosshair');
                if (chartContainer) {
                    chartContainer.classList.add('drawing-mode-active');
                    chartContainer.classList.remove('selection-mode-active');
                }
            }

            console.log(`Tool activated: ${toolDef.name}`);
        }

        setCursor(type) {
            this.canvas.style.cursor = type;
        }

        // --- Input Handling ---

        getMousePos(e) {
            const rect = this.canvas.getBoundingClientRect();
            let x = e.clientX - rect.left;
            let y = e.clientY - rect.top;

            // Apply Magnet if enabled
            if (this.magnetEnabled) {
                const chartState = this.getChartState();
                if (chartState && chartState.getMagnetPoint) {
                    const magnetPoint = chartState.getMagnetPoint(x, y);
                    if (magnetPoint) {
                        x = magnetPoint.x;
                        y = magnetPoint.y;
                    }
                }
            }
            return { x, y };
        }

        // Hit test to find if a point is near any drawing (for selection)
        hitTest(pos, chartState) {
            if (!chartState) return null;
            const hitThreshold = 10; // pixels

            // Check drawings in reverse order (top-most first)
            for (let i = this.drawings.length - 1; i >= 0; i--) {
                const drawing = this.drawings[i];

                // Check if drawing has a custom hitTest (for box/area drawings)
                if (drawing.getHitBox) {
                    const hitBox = drawing.getHitBox(chartState);
                    if (hitBox &&
                        pos.x >= hitBox.left && pos.x <= hitBox.right &&
                        pos.y >= hitBox.top && pos.y <= hitBox.bottom) {
                        return drawing;
                    }
                    continue;
                }

                const pixels = drawing.getPixels(chartState);

                if (pixels.length >= 2) {
                    // For line-based drawings, check distance to line
                    const p1 = pixels[0];
                    const p2 = pixels[1];
                    if (p1 && p2) {
                        const dist = this.distanceToLine(pos, p1, p2);
                        if (dist <= hitThreshold) {
                            return drawing;
                        }
                    }
                } else if (pixels.length === 1 && pixels[0]) {
                    // For single-point drawings, check distance to point
                    const p = pixels[0];
                    const dist = Math.sqrt((pos.x - p.x) ** 2 + (pos.y - p.y) ** 2);
                    if (dist <= hitThreshold) {
                        return drawing;
                    }
                }
            }
            return null;
        }

        // Calculate distance from point to line segment
        distanceToLine(point, lineStart, lineEnd) {
            const A = point.x - lineStart.x;
            const B = point.y - lineStart.y;
            const C = lineEnd.x - lineStart.x;
            const D = lineEnd.y - lineStart.y;

            const dot = A * C + B * D;
            const lenSq = C * C + D * D;
            let param = -1;

            if (lenSq !== 0) param = dot / lenSq;

            let xx, yy;

            if (param < 0) {
                xx = lineStart.x;
                yy = lineStart.y;
            } else if (param > 1) {
                xx = lineEnd.x;
                yy = lineEnd.y;
            } else {
                xx = lineStart.x + param * C;
                yy = lineStart.y + param * D;
            }

            const dx = point.x - xx;
            const dy = point.y - yy;
            return Math.sqrt(dx * dx + dy * dy);
        }

        onMouseDown(e) {
            // Pass through middle and right clicks to the chart for panning
            if (e.button !== 0) {
                this.forwardEventToChart(e, 'mousedown');
                return;
            }

            const pos = this.getMousePos(e);
            this.mouseDownPos = pos;
            this.mouseDownTime = Date.now();
            this.isDraggingChart = false;

            // Check if click is on axis areas (for zooming)
            const canvasWidth = this.canvas.width;
            const canvasHeight = this.canvas.height;
            const yAxisWidth = 76;
            const xAxisHeight = 28;

            if (pos.x > canvasWidth - yAxisWidth || pos.y > canvasHeight - xAxisHeight) {
                this.forwardEventToChart(e, 'mousedown');
                return;
            }

            // If mid-drawing, we don't necessarily want to forward mousedown to the chart 
            // if it might interfere, but for panning to work, we must.
            // Lightweight charts uses mousedown to start a pan.
            this.forwardEventToChart(e, 'mousedown');
        }

        forwardEventToChart(e, eventType) {
            const chartElement = document.getElementById('chart');
            if (chartElement) {
                const mouseEvent = new MouseEvent(eventType, {
                    bubbles: true,
                    cancelable: true,
                    clientX: e.clientX,
                    clientY: e.clientY,
                    button: e.button,
                    buttons: e.buttons,
                    ctrlKey: e.ctrlKey,
                    shiftKey: e.shiftKey,
                    altKey: e.altKey,
                    metaKey: e.metaKey
                });
                chartElement.dispatchEvent(mouseEvent);
            }
        }

        onMouseMove(e) {
            const pos = this.getMousePos(e);

            // Detect drag start (panning)
            if (e.buttons === 1 && this.mouseDownPos && !this.isDraggingChart) {
                const dist = Math.sqrt(Math.pow(pos.x - this.mouseDownPos.x, 2) + Math.pow(pos.y - this.mouseDownPos.y, 2));
                if (dist > 5) {
                    this.isDraggingChart = true;
                }
            }

            if (this.isDraggingChart) {
                // Forward to chart for panning
                this.forwardEventToChart(e, 'mousemove');
                return;
            }

            if (this.isDrawing && this.currentDrawing) {
                // Update drawing in progress
                const chartState = this.getChartState();
                this.currentDrawing.update(pos, chartState);
                this.redraw();
            } else {
                // Forward to chart for hover/crosshair
                this.forwardEventToChart(e, 'mousemove');
            }
        }

        onMouseUp(e) {
            if (e.button !== 0) {
                this.forwardEventToChart(e, 'mouseup');
                return;
            }

            const pos = this.getMousePos(e);
            let isClick = false;

            if (this.mouseDownPos) {
                const dist = Math.sqrt(Math.pow(pos.x - this.mouseDownPos.x, 2) + Math.pow(pos.y - this.mouseDownPos.y, 2));
                const duration = Date.now() - this.mouseDownTime;
                // A click is defined by small movement AND short duration (or just small movement if it's a slow click)
                if (dist < 5 && duration < 500 && !this.isDraggingChart) {
                    isClick = true;
                }
            }

            if (isClick && this.activeTool && this.activeTool.type === 'drawing') {
                const chartState = this.getChartState();
                if (!this.isDrawing) {
                    // Start new drawing
                    this.isDrawing = true;
                    this.currentDrawing = new this.activeTool.class(pos, chartState, this.activeTool.options);

                    // If it's a single point tool, finish immediately
                    if (this.activeTool.points === 1) {
                        this.finishDrawing();
                    }
                } else {
                    // Continue drawing (multi-point tools)
                    const finished = this.currentDrawing.addPoint(pos, chartState);
                    if (finished) {
                        this.finishDrawing();
                    }
                }
                this.redraw();
            }

            // Always forward mouseup to chart (even if it was a drawing click, doesn't hurt)
            this.forwardEventToChart(e, 'mouseup');

            // Reset dragging state
            this.isDraggingChart = false;
            this.mouseDownPos = null;
        }

        finishDrawing() {
            if (this.currentDrawing) {
                if (this.currentDrawing.isValid()) {
                    this.drawings.push(this.currentDrawing);
                    console.log("Drawing validated and added.");
                }
                this.currentDrawing = null;
            }
            this.isDrawing = false;

            if (!this.stayMode) {
                // Enter selection mode after finishing drawing (allows selecting/deleting)
                this.enterSelectionMode();
            }
            this.redraw();
        }

        // Cancel the current in-progress drawing (but keep the tool selected)
        cancelDrawing() {
            this.isDrawing = false;
            this.currentDrawing = null;
            this.redraw();
        }

        // Fully reset (e.g. ESC key)
        resetToDefault() {
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
            const chartContainer = document.getElementById('chart-container');

            if (chartContainer) {
                chartContainer.classList.remove('drawing-mode-active');
                chartContainer.classList.add('selection-mode-active');
                // Remove any stale cursor override
                chartContainer.style.cursor = '';
            }
            // We don't set cursor on canvas here because it's pointer-events: none
            this.updateUI();
        }

        // Delete only the selected drawing, then return to chart mode
        deleteSelected() {
            if (this.selectedDrawing) {
                const index = this.drawings.indexOf(this.selectedDrawing);
                if (index > -1) {
                    this.drawings.splice(index, 1);
                }
                this.selectedDrawing = null;
                this.redraw();
                // Remain in selection mode
                this.enterSelectionMode();
            }
        }

        clearAll() {
            this.drawings = [];
            this.selectedDrawing = null;
            this.cancelDrawing();
            this.redraw();
        }

        // --- Rendering ---

        redraw() {
            if (!this.ctx) return;
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

            if (this.hidden) return;

            const chartState = this.getChartState();
            if (!chartState) return;

            this.drawings.forEach(d => {
                const isSelected = (d === this.selectedDrawing);
                const isHovered = (d === this.hoveredDrawing);
                d.draw(this.ctx, chartState, isSelected, isHovered);
            });
            if (this.currentDrawing) {
                this.currentDrawing.draw(this.ctx, chartState, false, false);
            }
        }

        // --- Submenu & UI ---

        closeAllSubmenus() {
            document.querySelectorAll('.drawing-submenu').forEach(el => {
                el.classList.remove('visible');
            });
        }

        updateUI() {
            // Remove active class from all items
            document.querySelectorAll('.toolbar-item, .submenu-item').forEach(el => {
                el.removeAttribute('data-active');
                el.classList.remove('active');
            });

            if (this.activeTool) {
                // Find submenu item
                // We need a way to map activeTool to DOM elements. 
                // Simplified: use tool name or key.
                const key = this.activeTool.key;

                // Highlight submenu item
                const subItem = document.querySelector(`.submenu-item[onclick*="'${key}'"]`);
                if (subItem) subItem.classList.add('active');

                // Highlight parent toolbar item
                if (this.activeTool.group) {
                    const groupParams = document.querySelector(`.toolbar-item[data-tool-group="${this.activeTool.group}"]`);
                    if (groupParams) {
                        groupParams.setAttribute('data-active', 'true');
                        // OPTIONAL: Update parent icon to match selected tool
                        // const svg = subItem.querySelector('svg').cloneNode(true);
                        // groupParams.querySelector('svg').replaceWith(svg);
                    }
                }
            }
        }
    }

    // --- Drawing Classes ---

    class BaseDrawing {
        constructor(startPos, chartState, options = {}) {
            this.coords = []; // Store Chart Coordinates (time/price)
            if (chartState) {
                this.coords.push(chartState.pixelToCoord(startPos.x, startPos.y));
            }
            this.options = options;
            // Temporary mouse pos for preview
            this.currentPos = startPos;
        }

        addPoint(pos, chartState) {
            if (chartState) {
                this.coords.push(chartState.pixelToCoord(pos.x, pos.y));
            }
            // Return true if drawing is complete
            return false;
        }

        update(pos, chartState) {
            this.currentPos = pos;
        }

        isValid() { return this.coords.length > 0; }

        draw(ctx, chartState) { }

        // Helper to get pixels
        getPixels(chartState) {
            return this.coords.map(c => chartState.coordToPixel(c));
        }
    }

    class TwoPointDrawing extends BaseDrawing {
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 2;
        }

        draw(ctx, chartState, isSelected) {
            const pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;

            let p1 = pixels[0];
            let p2 = pixels.length > 1 ? pixels[1] : this.currentPos;
            if (!p1 || !p2) return;

            this.renderShape(ctx, p1, p2);

            // Draw handles only if selected or incomplete (being drawn)
            if (isSelected || pixels.length < 2) {
                this.drawHandle(ctx, p1, isSelected);
                this.drawHandle(ctx, p2, isSelected);
            }
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
    }

    class TrendLine extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class Ray extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Extend line to edge of canvas
            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            if (dx === 0 && dy === 0) return;

            // Calculate a point far away
            const scale = 10000;
            const endX = p1.x + dx * scale;
            const endY = p1.y + dy * scale;

            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(endX, endY);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();

            // Draw arrow head at p2 for visual direction
            /*
            const angle = Math.atan2(dy, dx);
            const headLen = 10;
            ctx.beginPath();
            ctx.moveTo(p2.x, p2.y);
            ctx.lineTo(p2.x - headLen * Math.cos(angle - Math.PI / 6), p2.y - headLen * Math.sin(angle - Math.PI / 6));
            ctx.moveTo(p2.x, p2.y);
            ctx.lineTo(p2.x - headLen * Math.cos(angle + Math.PI / 6), p2.y - headLen * Math.sin(angle + Math.PI / 6));
            ctx.stroke();
            */
        }
    }

    class InfoLine extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();

            // Show info bubble
            const midX = (p1.x + p2.x) / 2;
            const midY = (p1.y + p2.y) / 2;
            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            const angle = Math.atan2(dy, dx) * (180 / Math.PI);

            ctx.fillStyle = 'rgba(41, 98, 255, 0.9)';
            ctx.beginPath();
            ctx.roundRect(midX - 40, midY - 25, 80, 50, 4);
            ctx.fill();

            ctx.fillStyle = '#fff';
            ctx.font = '10px Roboto';
            ctx.textAlign = 'center';
            ctx.fillText(`${dist.toFixed(0)}px`, midX, midY - 5);
            ctx.fillText(`${angle.toFixed(1)}°`, midX, midY + 15);
        }
    }

    class ExtendedLine extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            if (dx === 0 && dy === 0) return;

            const scale = 10000;

            ctx.beginPath();
            ctx.moveTo(p1.x - dx * scale, p1.y - dy * scale);
            ctx.lineTo(p1.x + dx * scale, p1.y + dy * scale);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class HorizontalLine extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Horizontal line across the entire canvas at p1.y
            ctx.beginPath();
            ctx.moveTo(0, p1.y);
            ctx.lineTo(ctx.canvas.width, p1.y);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class HorizontalRay extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Horizontal ray starting at p1.x
            const dir = p2.x >= p1.x ? 1 : -1;
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(dir > 0 ? ctx.canvas.width : 0, p1.y);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class VerticalLine extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Vertical line across whole canvas at p1.x
            ctx.beginPath();
            ctx.moveTo(p1.x, 0);
            ctx.lineTo(p1.x, ctx.canvas.height);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class CrossLine extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Vertical line
            ctx.beginPath();
            ctx.moveTo(p1.x, 0);
            ctx.lineTo(p1.x, ctx.canvas.height);

            // Horizontal line
            ctx.moveTo(0, p1.y);
            ctx.lineTo(ctx.canvas.width, p1.y);

            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class ParallelChannel extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Main line
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();

            // Parallel line (simple approximation - just offsets by fixed amount for now as we don't have 3rd point logic yet)
            // In a full implementation, we'd need a 3rd point for width
            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            const len = Math.sqrt(dx * dx + dy * dy);

            if (len > 0) {
                const perpX = -dy / len * 50; // 50px offset
                const perpY = dx / len * 50;

                ctx.beginPath();
                ctx.moveTo(p1.x + perpX, p1.y + perpY);
                ctx.lineTo(p2.x + perpX, p2.y + perpY);
                ctx.strokeStyle = '#2962ff';
                ctx.setLineDash([5, 5]);
                ctx.stroke();
                ctx.setLineDash([]);

                // Fill
                ctx.fillStyle = 'rgba(41, 98, 255, 0.1)';
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.lineTo(p2.x + perpX, p2.y + perpY);
                ctx.lineTo(p1.x + perpX, p1.y + perpY);
                ctx.closePath();
                ctx.fill();
            }
        }
    }

    // Placeholder for Pitchfork
    class Pitchfork extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            this.drawTrendLine(ctx, p1, p2);
            // Simplified visualization
        }
        drawTrendLine(ctx, p1, p2) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class Rectangle extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            ctx.beginPath();
            ctx.rect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
            ctx.strokeStyle = '#9c27b0';
            ctx.lineWidth = 2;
            ctx.fillStyle = 'rgba(156, 39, 176, 0.1)';
            ctx.fill();
            ctx.stroke();
        }
    }

    class Circle extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const radius = Math.sqrt(Math.pow(p2.x - p1.x, 2) + Math.pow(p2.y - p1.y, 2));
            ctx.beginPath();
            ctx.arc(p1.x, p1.y, radius, 0, Math.PI * 2);
            ctx.strokeStyle = '#e91e63';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class TextDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            this.text = prompt("Enter text:", "Text");
        }
        isValid() { return this.text && this.text.length > 0; }
        draw(ctx, chartState, isSelected) {
            const p1 = chartState.coordToPixel(this.coords[0]);
            if (!p1) return;

            ctx.font = '16px Arial';
            ctx.fillStyle = '#d1d4dc';
            ctx.fillText(this.text, p1.x, p1.y);

            if (isSelected) {
                const metrics = ctx.measureText(this.text);
                const height = 16; // approximate
                ctx.strokeStyle = '#2962ff';
                ctx.lineWidth = 1;
                ctx.strokeRect(p1.x, p1.y - height, metrics.width, height + 4);

                // Draw handle
                ctx.fillStyle = '#2962ff';
                ctx.beginPath();
                ctx.arc(p1.x, p1.y, 3, 0, Math.PI * 2);
                ctx.fill();
            }
        }
    }

    class FibRetracement extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Calculate direction and height
            const isTrendLine = true;

            // Draw diagonal trendline (dashed)
            if (isTrendLine) {
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.setLineDash([4, 4]);
                ctx.strokeStyle = 'rgba(120, 123, 134, 0.6)';
                ctx.lineWidth = 1;
                ctx.stroke();
                ctx.setLineDash([]);
            }

            const levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

            // Standard Colorful Fib Colors to match reference
            const colors = [
                '#787b86', // 0 (Grey)
                '#f44336', // 0.236 (Red)
                '#ff9800', // 0.382 (Orange)
                '#4caf50', // 0.5 (Lime/Green)
                '#009688', // 0.618 (Teal)
                '#2196f3', // 0.786 (Blue)
                '#787b86'  // 1 (Grey)
            ];

            // Background tints for zones
            // We map zone i (between level i and i+1) to a color
            const zoneColors = [
                'rgba(244, 67, 54, 0.15)',   // 0 -> 0.236 (Red)
                'rgba(255, 152, 0, 0.15)',   // 0.236 -> 0.382 (Orange)
                'rgba(205, 220, 57, 0.15)',  // 0.382 -> 0.5 (Lime)
                'rgba(0, 150, 136, 0.15)',   // 0.5 -> 0.618 (Teal)
                'rgba(33, 150, 243, 0.15)',  // 0.618 -> 0.786 (Blue)
                'rgba(120, 123, 134, 0.15)'  // 0.786 -> 1 (Grey)
            ];

            const dy = p2.y - p1.y;
            // Use the horizontal range defined by the points
            const startX = Math.min(p1.x, p2.x);
            const endX = Math.max(p1.x, p2.x);

            const chartState = this.chartState || (window.toolManager && window.toolManager.getChartState());

            // 1. Draw Background Zones
            for (let i = 0; i < levels.length - 1; i++) {
                const lvlA = levels[i];
                const lvlB = levels[i + 1];

                const yA = p1.y + dy * lvlA;
                const yB = p1.y + dy * lvlB;

                if (i < zoneColors.length) {
                    ctx.fillStyle = zoneColors[i];
                    const top = Math.min(yA, yB);
                    const height = Math.abs(yA - yB);
                    if (height > 0) {
                        ctx.fillRect(startX, top, endX - startX, height);
                    }
                }
            }

            // 2. Draw Lines and Labels
            levels.forEach((lvl, i) => {
                const y = p1.y + dy * lvl;
                const color = colors[i];

                // Line
                ctx.beginPath();
                ctx.moveTo(startX, y);
                ctx.lineTo(endX, y);
                ctx.lineWidth = 1;
                ctx.strokeStyle = color;
                ctx.stroke();

                // Text
                let priceText = "";
                if (chartState && chartState.pixelToCoord) {
                    const price = chartState.pixelToCoord(0, y).price;
                    if (price !== undefined && price !== null) {
                        priceText = `(${price.toFixed(2)})`;
                    }
                }

                const text = `${lvl} ${priceText}`;

                ctx.font = '11px Arial';
                ctx.fillStyle = color;
                // Draw text outside to the left of the box, right-aligned
                ctx.textAlign = 'right';
                ctx.textBaseline = 'bottom';
                ctx.fillText(text, startX - 5, y - 2);
            });
        }
    }

    class BrushDrawing extends BaseDrawing {
        constructor(startPos, chartState, options) {
            super(startPos, chartState, options);
            if (chartState) {
                this.coords = [chartState.pixelToCoord(startPos.x, startPos.y)];
            }
        }

        update(pos, chartState) {
            if (chartState) {
                this.coords.push(chartState.pixelToCoord(pos.x, pos.y));
            }
            this.currentPos = pos;
        }

        draw(ctx, chartState) {
            const pixels = this.getPixels(chartState);
            if (pixels.length < 2) return;

            ctx.beginPath();
            ctx.moveTo(pixels[0].x, pixels[0].y);
            for (let i = 1; i < pixels.length; i++) {
                ctx.lineTo(pixels[i].x, pixels[i].y);
            }
            ctx.strokeStyle = '#ffeb3b';
            ctx.lineWidth = 4;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.globalAlpha = 0.5;
            ctx.stroke();
            ctx.globalAlpha = 1.0;
        }
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
            ctx.strokeStyle = '#e91e63';
            ctx.lineWidth = 2;
            ctx.stroke();
        }
    }

    class TriangleShape extends BaseDrawing {
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 3;
        }
        draw(ctx, chartState) {
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
            ctx.strokeStyle = '#ff9800';
            ctx.lineWidth = 2;
            ctx.stroke();
            pixels.forEach(p => this.drawHandle(ctx, p));
        }
        drawHandle(ctx, p) {
            ctx.fillStyle = '#fff';
            ctx.beginPath();
            ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#ff9800';
            ctx.stroke();
        }
        isValid() { return this.coords.length >= 3; }
    }

    // (InfoLine removed due to duplicate definition earlier)

    class TrendAngle extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.strokeStyle = '#4caf50';
            ctx.lineWidth = 2;
            ctx.stroke();
            const angle = Math.atan2(p1.y - p2.y, p2.x - p1.x) * (180 / Math.PI);
            ctx.fillStyle = '#4caf50';
            ctx.font = '12px Arial';
            ctx.fillText(`${angle.toFixed(1)}°`, p2.x + 10, p2.y);
        }
    }

    class GannFan extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const angles = [1 / 8, 1 / 4, 1 / 3, 1 / 2, 1, 2, 3, 4, 8];
            const colors = ['#ff5722', '#ff9800', '#ffc107', '#4caf50', '#2196f3', '#4caf50', '#ffc107', '#ff9800', '#ff5722'];
            angles.forEach((ratio, i) => {
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                const dx = p2.x - p1.x;
                const dy = (p2.y - p1.y) * ratio;
                ctx.lineTo(p1.x + dx * 2, p1.y + dy * 2);
                ctx.strokeStyle = colors[i];
                ctx.lineWidth = 1;
                ctx.stroke();
            });
        }
    }

    // Single-click Long Position - TradingView style spanning 5 candles
    class LongPosition extends BaseDrawing {
        constructor(startPos, chartState, options = {}) {
            super(startPos, chartState, options);
            this.quantity = options.quantity || 10;
            this.riskRewardRatio = options.riskRewardRatio || 2;
            this.stopPercent = options.stopPercent || 1.5; // Default 1.5% stop loss
            this.candleCount = 5; // Span 5 candles like TradingView

            // Store bar spacing for width calculation (use 20px fallback for ~100px width)
            this.barSpacing = chartState && chartState.getBarSpacing ? chartState.getBarSpacing() : 20;

            // Asymmetric width offsets (from entry point)
            const defaultHalfWidth = Math.max(this.barSpacing * this.candleCount, 100) / 2;
            this.leftOffset = defaultHalfWidth;  // Distance to left edge from entry
            this.rightOffset = defaultHalfWidth; // Distance to right edge from entry

            // Auto-calculate entry, stop, and target on single click
            if (chartState && this.coords.length >= 1) {
                this.entryPrice = this.coords[0].price || 0;
                this.stopPrice = this.entryPrice * (1 - this.stopPercent / 100);
                this.targetPrice = this.entryPrice + (this.entryPrice - this.stopPrice) * this.riskRewardRatio;
            }
        }

        // Override - complete on single click
        addPoint(pos, chartState) {
            return true; // Always complete immediately
        }

        isValid() {
            return this.coords.length >= 1 && this.entryPrice > 0;
        }

        // Get bounding box for hit testing
        getHitBox(chartState) {
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryPixel.y - 100;
            const stopLossY = stopPixel ? stopPixel.y : entryPixel.y + 50;

            return {
                left: entryPixel.x - this.leftOffset,
                right: entryPixel.x + this.rightOffset,
                top: Math.min(targetY, stopLossY),
                bottom: Math.max(targetY, stopLossY)
            };
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!chartState || this.coords.length < 1) return;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return;

            // Visual enhancement when hovered or selected
            const isHighlighted = isSelected || isHovered;
            const fillOpacity = isHighlighted ? 0.45 : 0.3;
            const strokeWidth = isHighlighted ? 2 : 1;

            const entryY = entryPixel.y;
            const left = entryPixel.x - this.leftOffset;
            const right = entryPixel.x + this.rightOffset;
            const width = this.leftOffset + this.rightOffset;

            // Convert prices to pixels
            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY - 100;
            const stopLossY = stopPixel ? stopPixel.y : entryY + 50;

            // Profit zone (green/teal) - above entry for long
            ctx.fillStyle = `rgba(38, 166, 154, ${fillOpacity})`;
            ctx.fillRect(left, targetY, width, entryY - targetY);
            ctx.strokeStyle = '#26a69a';
            ctx.lineWidth = strokeWidth;
            ctx.strokeRect(left, targetY, width, entryY - targetY);

            // Loss zone (red) - below entry for long
            ctx.fillStyle = `rgba(239, 83, 80, ${fillOpacity})`;
            ctx.fillRect(left, entryY, width, stopLossY - entryY);
            ctx.strokeStyle = '#ef5350';
            ctx.lineWidth = strokeWidth;
            ctx.strokeRect(left, entryY, width, stopLossY - entryY);

            // Draw entry line (solid blue)
            ctx.beginPath();
            ctx.moveTo(left, entryY);
            ctx.lineTo(right, entryY);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = isHighlighted ? 2 : 1.5;
            ctx.stroke();

            // Calculate P&L values
            const profitPercent = ((this.targetPrice - this.entryPrice) / this.entryPrice * 100).toFixed(2);
            const lossPercent = ((this.entryPrice - this.stopPrice) / this.entryPrice * 100).toFixed(2);
            const profitAmount = ((this.targetPrice - this.entryPrice) * this.quantity).toFixed(0);
            const lossAmount = ((this.entryPrice - this.stopPrice) * this.quantity).toFixed(0);

            // TradingView style labels
            this.drawPositionLabel(ctx, right + 5, targetY,
                `Target: ${this.targetPrice.toFixed(2)} (${profitPercent}%) ${this.quantity}, Amount: ${profitAmount}`,
                '#26a69a');

            this.drawPositionLabel(ctx, right + 5, stopLossY,
                `Stop: ${this.stopPrice.toFixed(2)} (${lossPercent}%) ${this.quantity}, Amount: ${lossAmount}`,
                '#ef5350');

            // Center P&L Info Box (TradingView style)
            const centerX = left + width / 2;
            const centerY = entryY - (entryY - targetY) / 2;
            this.drawCenterPnL(ctx, centerX, centerY, profitPercent, profitAmount);

            // Draw handles if highlighted
            if (isHighlighted) {
                this.drawHandle(ctx, { x: left, y: targetY }, isSelected, '#26a69a');
                this.drawHandle(ctx, { x: right, y: targetY }, isSelected, '#26a69a');
                this.drawHandle(ctx, { x: left, y: entryY }, isSelected, '#2962ff');
                this.drawHandle(ctx, { x: right, y: entryY }, isSelected, '#2962ff');
                this.drawHandle(ctx, { x: left, y: stopLossY }, isSelected, '#ef5350');
                this.drawHandle(ctx, { x: right, y: stopLossY }, isSelected, '#ef5350');
            }
        }

        drawHandle(ctx, p, isSelected, color) {
            const size = isSelected ? 5 : 3;
            ctx.fillStyle = color;
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
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const entryY = entryPixel.y;
            const left = entryPixel.x - this.leftOffset;
            const right = entryPixel.x + this.rightOffset;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY - 100;
            const stopLossY = stopPixel ? stopPixel.y : entryY + 50;

            const hitThreshold = 10;

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
            // Check entry line
            if (Math.abs(pos.y - entryY) <= hitThreshold && pos.x >= left - hitThreshold && pos.x <= right + hitThreshold) {
                return 'entry';
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
            } else if (handleName === 'entry') {
                // Entry must be between stop and target
                if (newPrice > this.stopPrice && newPrice < this.targetPrice) {
                    this.entryPrice = newPrice;
                    // Update coord to match
                    if (this.coords.length >= 1) {
                        this.coords[0].price = newPrice;
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
            } else if (handleName === 'left' && pixelX !== undefined) {
                // Resize from left edge only - asymmetric
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    const newLeftOffset = entryPixel.x - pixelX;
                    if (newLeftOffset >= 15) { // Minimum 15px from entry
                        this.leftOffset = newLeftOffset;
                    }
                }
            } else if (handleName === 'right' && pixelX !== undefined) {
                // Resize from right edge only - asymmetric
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    const newRightOffset = pixelX - entryPixel.x;
                    if (newRightOffset >= 15) { // Minimum 15px from entry
                        this.rightOffset = newRightOffset;
                    }
                }
            }
        }

        getPixels(chartState) {
            if (this.coords.length < 1) return [];
            const p = chartState.coordToPixel(this.coords[0]);
            return p ? [p] : [];
        }
    }

    // Single-click Short Position - TradingView style spanning 5 candles
    class ShortPosition extends BaseDrawing {
        constructor(startPos, chartState, options = {}) {
            super(startPos, chartState, options);
            this.quantity = options.quantity || 10;
            this.riskRewardRatio = options.riskRewardRatio || 2;
            this.stopPercent = options.stopPercent || 1.5; // Default 1.5% stop loss
            this.candleCount = 5; // Span 5 candles like TradingView

            // Store bar spacing for width calculation (use 20px fallback for ~100px width)
            this.barSpacing = chartState && chartState.getBarSpacing ? chartState.getBarSpacing() : 20;

            // Asymmetric width offsets (from entry point)
            const defaultHalfWidth = Math.max(this.barSpacing * this.candleCount, 100) / 2;
            this.leftOffset = defaultHalfWidth;  // Distance to left edge from entry
            this.rightOffset = defaultHalfWidth; // Distance to right edge from entry

            // Auto-calculate entry, stop, and target on single click
            if (chartState && this.coords.length >= 1) {
                this.entryPrice = this.coords[0].price || 0;
                this.stopPrice = this.entryPrice * (1 + this.stopPercent / 100); // Stop above for short
                this.targetPrice = this.entryPrice - (this.stopPrice - this.entryPrice) * this.riskRewardRatio; // Target below
            }
        }

        // Override - complete on single click
        addPoint(pos, chartState) {
            return true; // Always complete immediately
        }

        isValid() {
            return this.coords.length >= 1 && this.entryPrice > 0;
        }

        // Get bounding box for hit testing
        getHitBox(chartState) {
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryPixel.y + 100;
            const stopLossY = stopPixel ? stopPixel.y : entryPixel.y - 50;

            return {
                left: entryPixel.x - this.leftOffset,
                right: entryPixel.x + this.rightOffset,
                top: Math.min(targetY, stopLossY),
                bottom: Math.max(targetY, stopLossY)
            };
        }

        draw(ctx, chartState, isSelected, isHovered) {
            if (!chartState || this.coords.length < 1) return;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return;

            // Visual enhancement when hovered or selected
            const isHighlighted = isSelected || isHovered;
            const fillOpacity = isHighlighted ? 0.45 : 0.3;
            const strokeWidth = isHighlighted ? 2 : 1;

            const entryY = entryPixel.y;
            const left = entryPixel.x - this.leftOffset;
            const right = entryPixel.x + this.rightOffset;
            const width = this.leftOffset + this.rightOffset;

            // Convert prices to pixels
            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY + 100; // Below entry for short
            const stopLossY = stopPixel ? stopPixel.y : entryY - 50; // Above entry for short

            // Loss zone (red) - above entry for short
            ctx.fillStyle = `rgba(239, 83, 80, ${fillOpacity})`;
            ctx.fillRect(left, stopLossY, width, entryY - stopLossY);
            ctx.strokeStyle = '#ef5350';
            ctx.lineWidth = strokeWidth;
            ctx.strokeRect(left, stopLossY, width, entryY - stopLossY);

            // Profit zone (green/teal) - below entry for short
            ctx.fillStyle = `rgba(38, 166, 154, ${fillOpacity})`;
            ctx.fillRect(left, entryY, width, targetY - entryY);
            ctx.strokeStyle = '#26a69a';
            ctx.lineWidth = strokeWidth;
            ctx.strokeRect(left, entryY, width, targetY - entryY);

            // Draw entry line (solid blue)
            ctx.beginPath();
            ctx.moveTo(left, entryY);
            ctx.lineTo(right, entryY);
            ctx.strokeStyle = '#2962ff';
            ctx.lineWidth = isHighlighted ? 2 : 1.5;
            ctx.stroke();

            // Calculate P&L values
            const profitPercent = ((this.entryPrice - this.targetPrice) / this.entryPrice * 100).toFixed(2);
            const lossPercent = ((this.stopPrice - this.entryPrice) / this.entryPrice * 100).toFixed(2);
            const profitAmount = ((this.entryPrice - this.targetPrice) * this.quantity).toFixed(0);
            const lossAmount = ((this.stopPrice - this.entryPrice) * this.quantity).toFixed(0);

            // TradingView style labels
            this.drawPositionLabel(ctx, right + 5, stopLossY,
                `Stop: ${this.stopPrice.toFixed(2)} (${lossPercent}%) ${this.quantity}, Amount: ${lossAmount}`,
                '#ef5350');

            this.drawPositionLabel(ctx, right + 5, targetY,
                `Target: ${this.targetPrice.toFixed(2)} (${profitPercent}%) ${this.quantity}, Amount: ${profitAmount}`,
                '#26a69a');

            // Center P&L Info Box (TradingView style)
            const centerX = left + width / 2;
            const centerY = entryY + (targetY - entryY) / 2;
            this.drawCenterPnL(ctx, centerX, centerY, profitPercent, profitAmount);

            // Draw handles if highlighted
            if (isHighlighted) {
                this.drawHandle(ctx, { x: left, y: stopLossY }, isSelected, '#ef5350');
                this.drawHandle(ctx, { x: right, y: stopLossY }, isSelected, '#ef5350');
                this.drawHandle(ctx, { x: left, y: entryY }, isSelected, '#2962ff');
                this.drawHandle(ctx, { x: right, y: entryY }, isSelected, '#2962ff');
                this.drawHandle(ctx, { x: left, y: targetY }, isSelected, '#26a69a');
                this.drawHandle(ctx, { x: right, y: targetY }, isSelected, '#26a69a');
            }
        }

        drawHandle(ctx, p, isSelected, color) {
            const size = isSelected ? 5 : 3;
            ctx.fillStyle = color;
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
            if (!chartState || this.coords.length < 1) return null;

            const entryCoord = this.coords[0];
            const entryPixel = chartState.coordToPixel(entryCoord);
            if (!entryPixel) return null;

            const barSpacing = chartState.getBarSpacing ? chartState.getBarSpacing() : 20;
            const width = this.customWidth || Math.max(barSpacing * this.candleCount, 100);

            const entryY = entryPixel.y;
            const left = entryPixel.x - width / 2;
            const right = entryPixel.x + width / 2;

            const targetPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.targetPrice });
            const stopPixel = chartState.coordToPixel({ time: entryCoord.time, price: this.stopPrice });

            const targetY = targetPixel ? targetPixel.y : entryY + 100;
            const stopLossY = stopPixel ? stopPixel.y : entryY - 50;

            const hitThreshold = 10;

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
            // Check entry line
            if (Math.abs(pos.y - entryY) <= hitThreshold && pos.x >= left - hitThreshold && pos.x <= right + hitThreshold) {
                return 'entry';
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
                // Target must be below entry for short
                if (newPrice < this.entryPrice) {
                    this.targetPrice = newPrice;
                    // Recalculate R:R ratio
                    const profit = this.entryPrice - this.targetPrice;
                    const risk = this.stopPrice - this.entryPrice;
                    if (risk > 0) {
                        this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                    }
                }
            } else if (handleName === 'entry') {
                // Entry must be between stop and target for short (stop > entry > target)
                if (newPrice < this.stopPrice && newPrice > this.targetPrice) {
                    this.entryPrice = newPrice;
                    // Update coord to match
                    if (this.coords.length >= 1) {
                        this.coords[0].price = newPrice;
                    }
                }
            } else if (handleName === 'stop') {
                // Stop must be above entry for short
                if (newPrice > this.entryPrice) {
                    this.stopPrice = newPrice;
                    // Recalculate R:R ratio
                    const profit = this.entryPrice - this.targetPrice;
                    const risk = this.stopPrice - this.entryPrice;
                    if (risk > 0) {
                        this.riskRewardRatio = Math.round((profit / risk) * 10) / 10;
                    }
                }
            } else if (handleName === 'left' && pixelX !== undefined) {
                // Resize from left edge - no max limit
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    const newWidth = (entryPixel.x - pixelX) * 2;
                    if (newWidth >= 30) {
                        this.customWidth = newWidth;
                    }
                }
            } else if (handleName === 'right' && pixelX !== undefined) {
                // Resize from right edge - no max limit
                const entryCoord = this.coords[0];
                const entryPixel = chartState.coordToPixel(entryCoord);
                if (entryPixel) {
                    const newWidth = (pixelX - entryPixel.x) * 2;
                    if (newWidth >= 30) {
                        this.customWidth = newWidth;
                    }
                }
            }
        }

        // Get current width for drawing
        getWidth(chartState) {
            if (this.customWidth) return this.customWidth;
            const barSpacing = chartState && chartState.getBarSpacing ? chartState.getBarSpacing() : 20;
            return Math.max(barSpacing * this.candleCount, 100);
        }

        getPixels(chartState) {
            if (this.coords.length < 1) return [];
            const p = chartState.coordToPixel(this.coords[0]);
            return p ? [p] : [];
        }
    }

    class PriceRange extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            const midX = (p1.x + p2.x) / 2;
            ctx.beginPath();
            ctx.moveTo(midX, p1.y);
            ctx.lineTo(midX, p2.y);
            ctx.strokeStyle = '#9c27b0';
            ctx.lineWidth = 2;
            ctx.stroke();
            // Arrows
            ctx.beginPath();
            ctx.moveTo(midX - 5, p1.y + 8);
            ctx.lineTo(midX, p1.y);
            ctx.lineTo(midX + 5, p1.y + 8);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(midX - 5, p2.y - 8);
            ctx.lineTo(midX, p2.y);
            ctx.lineTo(midX + 5, p2.y - 8);
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
            ctx.strokeStyle = '#ff5722';
            ctx.lineWidth = 2;
            ctx.stroke();
            // Vertical markers
            ctx.beginPath();
            ctx.moveTo(p1.x, midY - 10);
            ctx.lineTo(p1.x, midY + 10);
            ctx.moveTo(p2.x, midY - 10);
            ctx.lineTo(p2.x, midY + 10);
            ctx.stroke();
            // Label
            const bars = Math.abs(p2.x - p1.x);
            ctx.fillStyle = 'rgba(0,0,0,0.8)';
            ctx.fillRect((p1.x + p2.x) / 2 - 30, midY - 25, 60, 20);
            ctx.fillStyle = '#fff';
            ctx.font = '11px Arial';
            ctx.fillText(`${Math.round(bars)}px`, (p1.x + p2.x) / 2 - 15, midY - 11);
        }
    }

    class Measure extends TwoPointDrawing {
        renderShape(ctx, p1, p2) {
            // Rectangle outline
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = '#00bcd4';
            ctx.strokeRect(p1.x, p1.y, p2.x - p1.x, p2.y - p1.y);
            ctx.setLineDash([]);
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
            this.isComplete = false;
        }
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return false; // Never auto-complete, use double-click or ESC
        }
        draw(ctx, chartState) {
            const pixels = this.getPixels(chartState);
            if (pixels.length === 0) return;
            ctx.beginPath();
            ctx.moveTo(pixels[0].x, pixels[0].y);
            for (let i = 1; i < pixels.length; i++) {
                ctx.lineTo(pixels[i].x, pixels[i].y);
            }
            if (this.currentPos && !this.isComplete) {
                ctx.lineTo(this.currentPos.x, this.currentPos.y);
            }
            ctx.strokeStyle = '#ffeb3b';
            ctx.lineWidth = 2;
            ctx.stroke();
            pixels.forEach(p => {
                ctx.fillStyle = '#fff';
                ctx.beginPath();
                ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
                ctx.fill();
            });
        }
        isValid() { return this.coords.length >= 2; }
    }

    class FibExtension extends BaseDrawing {
        addPoint(pos, chartState) {
            super.addPoint(pos, chartState);
            return this.coords.length >= 3;
        }
        draw(ctx, chartState) {
            const pixels = this.getPixels(chartState);
            if (pixels.length < 2) return;
            // Draw base lines
            ctx.beginPath();
            ctx.moveTo(pixels[0].x, pixels[0].y);
            ctx.lineTo(pixels[1].x, pixels[1].y);
            if (pixels[2]) ctx.lineTo(pixels[2].x, pixels[2].y);
            else if (this.currentPos) ctx.lineTo(this.currentPos.x, this.currentPos.y);
            ctx.strokeStyle = '#787b86';
            ctx.setLineDash([4, 4]);
            ctx.stroke();
            ctx.setLineDash([]);
            // Extension levels from point 3
            if (pixels.length >= 3) {
                const levels = [0.618, 1.0, 1.618, 2.618];
                const colors = ['#ff9800', '#4caf50', '#2196f3', '#9c27b0'];
                const baseMove = pixels[1].y - pixels[0].y;
                levels.forEach((lvl, i) => {
                    const y = pixels[2].y - baseMove * lvl;
                    ctx.beginPath();
                    ctx.moveTo(pixels[2].x - 50, y);
                    ctx.lineTo(pixels[2].x + 100, y);
                    ctx.strokeStyle = colors[i];
                    ctx.stroke();
                    ctx.fillStyle = colors[i];
                    ctx.font = '10px Arial';
                    ctx.fillText(`${lvl}`, pixels[2].x + 105, y + 4);
                });
            }
            pixels.forEach(p => {
                ctx.fillStyle = '#fff';
                ctx.beginPath();
                ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
                ctx.fill();
                ctx.strokeStyle = '#2962ff';
                ctx.stroke();
            });
        }
        isValid() { return this.coords.length >= 3; }
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
        horizontal_ray: { class: Ray, type: 'drawing', name: 'Horizontal Ray', key: 'horizontal_ray', group: 'lines', points: 2 },
        cross_line: { class: CrossLine, type: 'drawing', name: 'Cross Line', key: 'cross_line', group: 'lines', points: 2 },
        channel: { class: ParallelChannel, type: 'drawing', name: 'Parallel Channel', key: 'channel', group: 'lines', points: 2 },
        pitchfork: { class: Pitchfork, type: 'drawing', name: 'Pitchfork', key: 'pitchfork', group: 'lines', points: 3 },

        // Shapes
        rectangle: { class: Rectangle, type: 'drawing', name: 'Rectangle', key: 'rectangle', group: 'shapes', points: 2 },
        circle: { class: Circle, type: 'drawing', name: 'Circle', key: 'circle', group: 'shapes', points: 2 },
        ellipse: { class: Ellipse, type: 'drawing', name: 'Ellipse', key: 'ellipse', group: 'shapes', points: 2 },
        triangle: { class: TriangleShape, type: 'drawing', name: 'Triangle', key: 'triangle', group: 'shapes', points: 3 },
        path: { class: PathDrawing, type: 'drawing', name: 'Path', key: 'path', group: 'shapes', points: 99 },
        brush: { class: BrushDrawing, type: 'drawing', name: 'Brush', key: 'brush', group: 'shapes', points: 0, dragBased: true },
        highlighter: { class: BrushDrawing, type: 'drawing', name: 'Highlighter', key: 'highlighter', group: 'shapes', points: 0, dragBased: true },

        // Text & Annotations
        text: { class: TextDrawing, type: 'drawing', name: 'Text', key: 'text', group: 'text', points: 1 },
        anchored_text: { class: TextDrawing, type: 'drawing', name: 'Anchored Text', key: 'anchored_text', group: 'text', points: 1 },
        note: { class: TextDrawing, type: 'drawing', name: 'Note', key: 'note', group: 'text', points: 1 },
        callout: { class: TextDrawing, type: 'drawing', name: 'Callout', key: 'callout', group: 'text', points: 1 },
        price_label: { class: TextDrawing, type: 'drawing', name: 'Price Label', key: 'price_label', group: 'text', points: 1 },
        arrow_marker: { class: TwoPointDrawing, type: 'drawing', name: 'Arrow Marker', key: 'arrow_marker', group: 'text', points: 1 },

        // Fibonacci & Gann
        fib_retracement: { class: FibRetracement, type: 'drawing', name: 'Fib Retracement', key: 'fib_retracement', group: 'fib', points: 2 },
        fib_trend_ext: { class: FibExtension, type: 'drawing', name: 'Fib Extension', key: 'fib_trend_ext', group: 'fib', points: 3 },
        fib_channel: { class: TwoPointDrawing, type: 'drawing', name: 'Fib Channel', key: 'fib_channel', group: 'fib', points: 3 },
        gann_box: { class: Rectangle, type: 'drawing', name: 'Gann Box', key: 'gann_box', group: 'fib', points: 2 },
        gann_fan: { class: GannFan, type: 'drawing', name: 'Gann Fan', key: 'gann_fan', group: 'fib', points: 2 },

        // Patterns
        xabcd_pattern: { class: TwoPointDrawing, type: 'drawing', name: 'XABCD Pattern', key: 'xabcd_pattern', group: 'patterns', points: 5 },
        cypher_pattern: { class: TwoPointDrawing, type: 'drawing', name: 'Cypher Pattern', key: 'cypher_pattern', group: 'patterns', points: 5 },
        abcd_pattern: { class: TwoPointDrawing, type: 'drawing', name: 'ABCD Pattern', key: 'abcd_pattern', group: 'patterns', points: 4 },
        triangle_pattern: { class: TriangleShape, type: 'drawing', name: 'Triangle Pattern', key: 'triangle_pattern', group: 'patterns', points: 3 },
        head_and_shoulders: { class: TwoPointDrawing, type: 'drawing', name: 'Head & Shoulders', key: 'head_and_shoulders', group: 'patterns', points: 7 },
        elliott_impulse: { class: TwoPointDrawing, type: 'drawing', name: 'Elliott Impulse (12345)', key: 'elliott_impulse', group: 'patterns', points: 5 },
        elliott_triangle: { class: TwoPointDrawing, type: 'drawing', name: 'Elliott Triangle (ABCDE)', key: 'elliott_triangle', group: 'patterns', points: 5 },
        elliott_correction: { class: TwoPointDrawing, type: 'drawing', name: 'Elliott Correction (ABC)', key: 'elliott_correction', group: 'patterns', points: 3 },
        elliott_double_combo: { class: TwoPointDrawing, type: 'drawing', name: 'Elliott Double Combo', key: 'elliott_double_combo', group: 'patterns', points: 3 },

        // Prediction & Measurement (single-click position tools)
        long_position: { class: LongPosition, type: 'drawing', name: 'Long Position', key: 'long_position', group: 'prediction', points: 1 },
        short_position: { class: ShortPosition, type: 'drawing', name: 'Short Position', key: 'short_position', group: 'prediction', points: 1 },
        date_range: { class: DateRange, type: 'drawing', name: 'Date Range', key: 'date_range', group: 'prediction', points: 2 },
        price_range: { class: PriceRange, type: 'drawing', name: 'Price Range', key: 'price_range', group: 'prediction', points: 2 },
        date_price_range: { class: Measure, type: 'drawing', name: 'Date & Price Range', key: 'date_price_range', group: 'prediction', points: 2 },

        // Utilities
        measure: { class: Measure, type: 'drawing', name: 'Measure', key: 'measure', group: 'utils', points: 2 },
        zoom: { class: TwoPointDrawing, type: 'drawing', name: 'Zoom', key: 'zoom', group: 'utils', points: 2 }
    };

    // Global Helper Functions (called from HTML)

    window.toolManager = null;

    window.initToolManager = function () {
        if (!window.toolManager) {
            window.toolManager = new ToolManager('drawing-canvas-container', 'drawing-canvas');
            console.log("ToolManager initialized");
        }
        // Subscribe to chart pan/zoom if the chart is already available
        if (window.bigChart && !window._bigChartSubscribed) {
            window._bigChartSubscribed = true;
            window.bigChart.timeScale().subscribeVisibleTimeRangeChange(function () {
                window.toolManager.redraw();
            });
            window.bigChart.subscribeCrosshairMove(function () {
                window.toolManager.redraw();
            });
        }
    };

    window.toggleSubmenu = function (id, event) {
        // Stop propagation to prevent document click handler from immediately closing
        if (event) {
            event.stopPropagation();
        }

        if (!window.toolManager) {
            // Try to init if missing
            console.warn("ToolManager missing on click, initializing...");
            window.initToolManager();
        }

        const submenu = document.getElementById(id);
        if (!submenu) {
            console.error("Submenu element not found:", id);
            return;
        }

        const wasVisible = submenu.classList.contains('visible');

        // Close all submenus first
        document.querySelectorAll('.drawing-submenu').forEach(el => el.classList.remove('visible'));

        // If it wasn't visible before, open it
        if (!wasVisible) {
            submenu.classList.add('visible');
            console.log("Submenu opened:", id);
        } else {
            console.log("Submenu closed:", id);
        }
    };

    window.activateTool = function (key, event) {
        // Stop propagation to prevent parent toolbar-item from re-toggling submenu
        if (event) {
            event.stopPropagation();
        }

        if (!window.toolManager) window.initToolManager();

        const def = ToolDefinitions[key];
        if (def) {
            window.toolManager.setTool(def);
            // Close all submenus after tool selection
            document.querySelectorAll('.drawing-submenu').forEach(el => el.classList.remove('visible'));
            console.log('Tool activated and submenu closed:', key);
        } else {
            console.warn('Tool not found:', key);
        }
    };

    // Utils
    window.toggleMagnet = function (el) {
        if (!window.toolManager) return;
        window.toolManager.magnetEnabled = !window.toolManager.magnetEnabled;
        el.setAttribute('data-active', window.toolManager.magnetEnabled);
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

} catch (err) {
    console.error("CRITICAL ERROR in drawings.js:", err);
}

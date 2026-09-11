# TradingView Drawing Tools — Full Behavior Spec

This document describes how each TradingView drawing tool behaves, so you can replicate the exact interaction model in your chart page: how many anchor points it has, what happens when you drag each point, whether it shows price/time on the axes, and whether it extends into future (unbuilt) candles.

---

## 0. Universal concepts (apply to every tool below)

Before the individual tools, these are the shared mechanics every TradingView drawing follows. Your engine should implement these once, generically, then each tool just plugs in its point rules.

**Anchor points**
Every drawing is defined by 1–N "anchor points," each of which is a `(time, price)` pair — i.e. an (x, y) location expressed in chart coordinates, not pixel coordinates. This is critical: a point is *not* stored as a pixel (x, y); it's stored as (timestamp, price). That's what lets a drawing scroll and rescale smoothly with the chart instead of "swimming" or drifting when you pan/zoom.

**Selecting and moving**
- Click once on a drawing → it becomes selected, showing small square handles on each anchor point plus (for lines) a handle at the midpoint.
- Dragging an **anchor point handle** moves only that point; the other point(s) stay exactly fixed. This is the behavior you already described for your trend line — that's correct and it's how every 2-point tool works.
- Dragging the **body of the line** (anywhere between the points, not on a handle) moves the whole drawing as a rigid shape — both points shift together, preserving the line's slope/shape.
- Dragging the **midpoint handle** (if present) also moves the whole line as a rigid shape (a convenience handle so you don't have to grab the line itself).

**Coordinates panel**
Every tool has a "Coordinates" settings tab where each anchor point can be edited numerically as **(date/time, price)** instead of by dragging. This is how TradingView lets you place a point with pixel-perfect precision, and it's the same underlying data model as the drag — dragging just writes to those same two numbers.

**Extending into the future (unbuilt candles)**
This is the key distinction you're asking about:
- Tools like **Trend Line** and **Channel** are drawn *between* two anchor points and by default **stop** at the second point (no extension) — but each has a "Style" option to extend the ray left, right, or both, past its anchors into empty future space (space with no candles yet).
- Tools like **Ray**, **Extended Line**, **Horizontal Line**, **Horizontal Ray**, **Vertical Line**, and **Cross Line** are specifically *designed* to continue indefinitely — they're rendered every frame from the anchor point out to the current edge of the visible chart, including into the empty future area past the last candle, and that edge simply moves further right automatically as more candles print or as the user scrolls. Nothing is "recomputed" for new candles; the line's underlying (time, price) never changes, only how far the renderer draws it.

**Price axis labels**
Each tool independently controls whether it prints a small tag on the right-hand Y (price) axis showing the price at that anchor point. This is a per-tool default:
- Horizontal Line, Horizontal Ray → **price label is ON by default** (their whole purpose is marking a price level).
- Trend Line, Ray, Extended Line → price labels are **available but OFF by default** (togglable per point) — because these are about direction/slope, not a single fixed price.
- Vertical Line → shows a **time** label on the X axis instead of a price label, since it marks a moment in time, not a price.

**Time axis labels**
Symmetric to the above: Vertical Line and Cross Line highlight/label the time axis; horizontal-type tools don't.

**Smooth chart movement while dragging**
When the user drags a point near the edge of the visible viewport, TradingView auto-scrolls the chart smoothly (a small continuous pan) so the point can keep moving off-screen. This is done with a `requestAnimationFrame` loop that (a) redraws the drawing at the new pixel position derived from its (time, price) every frame, and (b) nudges the chart's visible range slightly per frame while the pointer is held near the edge, rather than jumping. The key implementation detail: never store or animate raw pixel coordinates for the drawing itself — always keep (time, price) as source of truth and recompute pixel position from the current visible range on every render tick. That's what makes panning/zooming feel "attached" to the chart instead of dragging along a separate layer.

**Alerts**
Line-type tools (trend line, horizontal line, ray, etc.) can have a price-cross alert attached directly to them via a clock icon — "notify me when price crosses this line."

**Magnet mode**
An optional snapping mode: while placing/dragging a point, if enabled, the point snaps to the nearest O/H/L/C of the nearest candle (strong magnet) or loosely biases toward it (weak magnet), instead of free placement.

---

## 1. Cursors (not drawings, but modes)
- **Cross** – default crosshair cursor.
- **Dot** – crosshair variant with a small dot instead of full cross lines.
- **Arrow** – plain pointer, no crosshair.
- **Eraser** – click a drawing to delete it directly.

These don't need separate render logic in your engine — they just change what the mouse pointer looks like and how clicks are interpreted (eraser mode = click deletes instead of selects).

---

## 2. Trend / Line tools

### 2.1 Trend Line
- **Points:** 2 (start, end).
- **Drag point A** → only A moves, B fixed (slope/length recalculated from B).
- **Drag body** → both points translate together (parallel move).
- **Extension:** off by default; Style settings offer "Extend line" left/right/both — when on, the visual line continues past the anchor into future space, but the two *anchor points* themselves don't move.
- **Price labels:** optional per-point toggle (off by default); when on, shows each point's price on the Y axis.
- **Stats overlay:** optional label showing price range, % change, pip change, bar count, date/time span, and angle between the two points — toggle each independently, and choose whether it always shows or only on selection.
- **Shift+drag while creating** constrains the line to a 45° angle.
- **Arrow ends:** the line's ends can each independently be set to plain, or arrowhead — same tool doubles as an "arrow" annotation.
- **Use case:** trends, support/resistance diagonals, arrows pointing at a spot.

### 2.2 Ray
- **Points:** 2, but only the **first** point is a "true anchor" — the second point just defines direction/slope.
- Behaves like a trend line but the line **only extends infinitely in one direction** (from point 1 through point 2 and onward), never backward past point 1.
- Dragging point 1 moves the origin; dragging point 2 changes the angle the ray shoots off at, pivoting around point 1.
- Continues indefinitely into future (unbuilt) candles.

### 2.3 Extended Line
- **Points:** 2.
- Same as trend line but extends infinitely in **both** directions past both anchors (backward into history and forward into the future) automatically — no toggle needed, it's inherent to the tool.
- Dragging either handle moves only that point; the line rotates around the other fixed point. It does not shorten.

### 2.4 Horizontal Line
- **Points:** 1.
- Purely a price level: a perfectly horizontal line spanning the entire visible width of the chart (past and future), at the price of its single anchor point.
- **Price label: ON by default** on the Y axis (this is the "price shown" behavior you wanted — horizontal line does show price).
- Dragging it only changes its price (Y value); it can't be dragged left/right in any meaningful way since it has no time component — grabbing anywhere on the line and dragging vertically moves it.
- Shortcut: select a point/candle, press **Alt+H**, to drop a horizontal line at that price instantly.
- Continues automatically into every future candle without any extra logic — because it isn't tied to a time range at all, only a price.

### 2.5 Horizontal Ray
- **Points:** 1 (an anchor at a specific bar/time AND price).
- Like a horizontal line, but only extends **rightward** from its anchor point (i.e., it does NOT extend into the past before the anchor) — useful for "price broke this level starting from this candle onward."
- Price label ON by default, same as horizontal line.
- Dragging moves the anchor in both price and time; the ray always re-renders from that anchor to the current right edge of the chart (including future candles).

### 2.6 Vertical Line
- **Points:** 1 (a specific bar/time).
- A perfectly vertical line spanning the full height of the chart at that moment in time.
- Shows a **time** label on the X axis instead of a price label (marks "this candle/this moment"), price axis is irrelevant to it.
- Dragging moves it left/right only (changes which bar/time it marks).

### 2.7 Cross Line
- **Points:** 1.
- Combination of a horizontal + vertical line crossing at one (time, price) point — shows both a price label on Y axis and a time label on X axis simultaneously.
- Dragging moves both time and price at once (full 2D drag), unlike horizontal (Y-only) or vertical (X-only).

### 2.8 Trend Angle
- Same as Trend Line, but locks/labels the angle of inclination in degrees and keeps that angle fixed in terms of price-per-bar as you scroll — mainly used with Gann-style angle theory.

### 2.9 Info Line
- Same 2-point behavior as trend line, but always displays the stats box (price range, bar count, angle etc.) without needing to enable it manually — a "trend line + always-on info label" combo.

---

## 3. Channels

### 3.1 Parallel Channel
- **Points:** 3. Point 1 & 2 define the main trend line (like a trend line). Point 3 sets the offset/width of a second line drawn parallel to it.
- Dragging point 1 or 2 behaves exactly like trend line (pivots the main line, other point fixed).
- Dragging point 3 only changes the channel's **width** (distance between the two parallel lines), sliding perpendicular to the trend line. Horizontal (along-the-line) drag is ignored — the point is constrained to perpendicular movement only.
- Dragging the body of either line (or anywhere inside the shaded band) moves the whole channel together.
- Style option to extend both lines into the future, same toggle concept as trend line.

### 3.2 Regression Trend
- **Points:** exactly 2. The main line is auto-fitted via linear regression across the bars between your two anchor points rather than exactly connecting them; channel bands show standard-deviation width.

### 3.3 Flat Top/Bottom, Disjoint Channel
- Variants of the channel where one side is forced horizontal (flat top/bottom) or where the two boundary lines are independently draggable rather than strictly parallel (disjoint).

### 3.4 Pitchfork family (Classic / Schiff / Modified Schiff / Inside)
- **Points:** 3, labeled conceptually as the start of a trend, the first pullback, and the next swing.
- Produces **three parallel lines**: a median line and two outer (resistance/support) lines, all pivoting together off the 3 anchors.
- Each pitchfork variant differs only in *which* of the 3 points the median line's origin is anchored to (classic uses point 1 directly; Schiff uses the midpoint between points 1 & 2; modified Schiff uses a point further along; inside pitchfork mirrors classic but the median starts from the midpoint of points 2–3).
- All three lines auto-extend into the future indefinitely, like a ray.

---

## 4. Fibonacci & Gann tools

All Fibonacci tools share this pattern: you drag out **2 anchor points** (occasionally 3), and the tool auto-generates a **series of parallel/concentric levels** between/around them based on fixed ratios (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618 etc.) — you don't draw each level; the tool computes them from your 2 points.

- **Fib Retracement:** 2 points (swing high/low). Draws horizontal levels at each Fib ratio between them; each level shows its price on the Y axis and is independently togglable/colorable.
- **Trend-based Fib Extension:** 3 points — measures the move from point 1→2, then projects Fib ratios of that move starting from point 3.
- **Fib Channel:** like Fib retracement but levels are drawn as lines parallel to your trend line, not flat horizontals.
- **Fib Time Zone / Trend-based Fib Time:** instead of price levels, draws a series of **vertical lines** at Fibonacci-ratio time intervals from your anchor(s).
- **Fib Speed Resistance Fan:** 2 points define a bounding box; draws diagonal fan lines at Fib ratio subdivisions of that box.
- **Fib Arcs:** 2 points define a bounding box; draws concentric arcs (curved lines) at Fib ratio subdivisions of that box.
- **Fib Circles:** 2 points; draws concentric ellipses at Fibonacci ratio radii from point 1 toward point 2.
- **Fib Wedge / Fib Spiral / Pitchfan:** more elaborate 2–3 point Fib-ratio derived shapes, same principle — you set 2–3 raw anchors, the ratios are computed automatically.
- **Gann Fan / Gann Square / Gann Box:** 2 points; instead of Fibonacci ratios these use fixed geometric angles (1x1, 1x2, 2x1, etc.) believed by W.D. Gann to represent natural support/resistance slopes — same interaction model (drag 2 points, tool auto-generates a fixed family of lines/angles from them).

**Implementation tip:** build one generic "anchored ratio tool" primitive (N anchor points → array of ratios → generated sub-lines/levels), and every Fibonacci/Gann tool becomes a config of that primitive rather than a separate bespoke tool.

---

## 5. Patterns
2-anchor-point-per-leg tools that snap several connected trend-line legs together and auto-label them:
- **XABCD / ABCD pattern:** 5 (or 4) points forming labeled legs X-A-B-C-D or A-B-C-D, typically with Fib ratio validation shown at each leg.
- **Triangle pattern:** 3 points forming a converging/diverging triangle overlay.
- **Three Drives pattern:** a 7-point structure marking 3 successive "drives" with retracements between.
- **Head and Shoulders:** a preset multi-point template (left shoulder, head, right shoulder, neckline) you drag onto the chart, auto-labeled.
- **Elliott Wave tools:** numbered/lettered point sequences (1-2-3-4-5, A-B-C, etc.) that just place sequential labels at each anchor along a path you click out.
- **Cyclic Lines / Time Cycles / Sine Line:** 2 points define one "cycle length," then the tool auto-repeats vertical markers (cyclic lines) or a sine curve (sine line) at that repeating interval across the rest of the chart, including into the future.

---

## 6. Forecasting & measurement

- **Long/Short Position tool:** 3 draggable horizontal zones (entry, target/profit in green, stop-loss in red) anchored between 2 time points; auto-calculates and displays risk:reward ratio, and $ / % profit-loss based on a quantity you can set, live-updating as you drag any edge.
- **Position Forecast:** a 2-point line tool (entry/source and exit/target) that auto-evaluates as "success" or "failure" as time progresses toward the target.
- **Bars Pattern:** copies a selected historical run of bars/candles and lets you paste/overlay that same shape elsewhere on the chart (e.g., to visually compare current price action to a past pattern) — it doesn't compute anything, it's a visual copy tool.
- **Ghost Feed:** generates synthetic projected candles from a starting point, based on "Avg HL in minticks" and "Variance" settings — a projection tool, not a copy of real data.
- **Anchored VWAP:** 1 anchor point (a specific bar) — plots the volume-weighted average price computed cumulatively **from that bar forward**, recalculating live as new candles form; this is one of the few "drawing tools" that keeps computing off live data rather than being a static shape.
- **Fixed Range / Anchored Volume Profile:** 2 points (or 1 point + present) defining a time range; renders a horizontal histogram of traded volume at each price level within that range, on the side of the chart.
- **Price Range / Date Range / Date and Price Range:** simple 2-point measuring tools — drag from A to B and it shows the price difference / time difference / both, as a floating label, purely a ruler with no lasting market meaning.

---

## 7. Geometric shapes
Standard shape tools, each just a different number of anchor points defining a bounding shape, fillable/strokeable like the line tools:
- **Rectangle / Rotated Rectangle:** 2 points (opposite corners) for rectangle; rotated variant adds a 3rd point to set rotation angle.
- **Circle / Ellipse:** 2 points define bounding box.
- **Triangle:** 3 points, one per vertex.
- **Polyline / Path:** unlimited points, click each vertex, double-click or Esc to finish — an open, straight-segment shape (Path allows curve smoothing between segments).
- **Arc / Curve / Double Curve:** 2–3 points, but rendered as a curved (Bezier-like) line instead of straight, curvature controlled by the middle point.
- **Brush / Highlighter:** freehand — captured as a dense list of (time, price) points sampled continuously while the mouse button is held and dragged, then rendered as a smoothed path; highlighter is the same but semi-transparent/thicker.
- **Arrow / Arrow Marker / Arrow Marks (up/down):** Arrow is a 2-point line with a fixed arrowhead at the end; marker/marks are single-click stamped icons (no drag) placed at one anchor.

---

## 8. Annotation tools
Mostly single-anchor-point tools that place UI, not chart lines:
- **Text:** 1 point, opens an inline editable text box anchored there; grows independent of chart scale (its font size doesn't change when you zoom the price axis).
- **Note / Price Note:** 1 point, shows a small icon that expands into a note popup on hover/click; Price Note additionally shows the exact price it was left at.
- **Pin:** 1 point, drops a map-pin-style marker.
- **Callout:** 2 points — an anchor point plus a separate text-box point connected by a line/arrow (like a comic-style callout).
- **Comment:** 1 point, small speech-bubble icon with a text note.
- **Signpost:** 1 point, a flag/sign-shaped label anchored to a specific bar and price.
- **Table:** places a resizable grid/table overlay anywhere on the chart (for freeform notes), not tied to price data.
- **Image:** 1 point, lets the user upload/place an arbitrary image on the chart at that anchor, resizable via a corner handle.

---

## 9. Icons
Not draggable-shape tools — a picker of emoji/stickers/icons that get stamped as a single-anchor-point image, same underlying mechanism as Arrow Marks (click once to place, drag the single point afterward to reposition).

---

## 10. Utility tools/modes (not drawings themselves)
- **Measure:** click-drag between any two bars → live floating readout of price change, % change, and bar count between them; disappears once you release (not a persistent drawing) unless you hold a modifier to keep it.
- **Zoom In:** drag a box to zoom the visible chart to that price/time range.
- **Magnet mode (weak/strong):** described above under Universal concepts — snaps new/dragged points to OHLC values.
- **Keep drawing tool active:** toggle so the tool stays selected after you finish one drawing, letting you place several in a row without re-selecting it each time.
- **Lock all drawings:** freezes every existing drawing's anchor points against accidental drag (still visually present, not selectable/movable until unlocked).
- **Hide drawings / indicators / positions:** visibility toggles, doesn't delete data.
- **Sync drawings across layouts/symbols:** if on, a drawing placed on one chart tab also appears on other open tabs of the same or different symbol.
- **Remove:** delete-all shortcuts, scoped to drawings, indicators, or both.

---

## 11. Shared "Style" panel fields you'll want on nearly every tool
Regardless of tool type, TradingView exposes a consistent settings dialog with these tabs — mirror this structure and most tools "just work" with shared UI:
1. **Style** – color, opacity, thickness, line style (solid/dashed/dotted), extend-left/right toggles where applicable, price-label toggle, stats toggles.
2. **Text** – optional label text, font size/color/weight, alignment relative to the shape.
3. **Coordinates** – exact numeric (date/time, price) for every anchor point, editable directly.
4. **Visibility** – per-timeframe show/hide (e.g., only show this drawing on 1H and above).
5. **Alert** (line-type tools only) – attach a price-cross alert directly to the object.

---

## 12. Physical, step-by-step interaction walkthrough

Everything above describes settings and definitions. This section describes **exactly what happens on the chart, click by click and drag by drag** — the part the official docs skip.

### Trend Line
1. Click the tool, then **click once** on the chart. Nothing is drawn yet — you're holding one loose end, and a temporary line follows your cursor from that first click to wherever the mouse currently is.
2. **Click a second time** anywhere else. The line locks in between those two exact points. Tool deselects (unless "keep tool active" is on).
3. Click the line once to select it → two small square/circle handles appear, one at each end, plus a small diamond/dot at the midpoint.
4. **Grab the left handle and drag** → the left point slides freely with your cursor (in any direction, any price/time). The right point does not move even 1 pixel. The line's slope/length updates live as you drag.
5. **Grab the right handle instead** → same thing, mirrored: right point moves, left is frozen.
6. **Grab the middle diamond, or click-drag anywhere on the line body itself (not on a handle)** → the entire line translates as one rigid unit — both endpoints shift by the same amount, slope unchanged. This is the "move the whole thing" gesture.
7. If you drag a handle to the right past the last candle, into the empty space — it's allowed. The point can sit at a bar/time that doesn't have a candle yet (this is literally how you'd draw a target level for a future breakout).

### Horizontal Line
1. Click the tool, then **one single click** anywhere on the chart. Done immediately — no second click needed, because there's only one point (a price).
2. A full-width line appears instantly at that exact price, running from the far-left edge of the chart to the far-right edge, including straight through the empty future space past the last candle — no extra step required for that, it's just how the tool always renders.
3. A small tag appears on the right-hand price axis showing the exact price number.
4. Click the line to select it → **one handle only** (there's nothing to fix in place — it's a single point).
5. **Drag anywhere on the line, in any horizontal spot** → the whole line moves vertically only. Left/right mouse movement does nothing to it; only vertical (Y) movement changes its price. The axis tag updates live as you drag.

### Ray
1. Click the tool, **click point 1** (this becomes the fixed origin), **click point 2** anywhere else (this only sets the direction/angle).
2. The visible line is drawn starting exactly at point 1, running through point 2, and continuing on past point 2 all the way to the current right edge of the chart — it never appears to the left of point 1, only forward from it.
3. Select it → two handles, same as trend line.
4. **Drag point 1** → only point 1 moves. Point 2 stays fixed. The ray's origin relocates and the angle pivots around point 2.
5. **Drag point 2** → the origin (point 1) stays exactly still, but the angle the ray shoots off at pivots around point 1, like swinging a stick around a fixed pin.

### Extended Line
1. Click point 1, click point 2 — identical to trend line to place.
2. The instant you place point 2, the rendered line snaps to run infinitely in **both** directions through those two points — you'll see it touch both the left and right edges of the visible chart immediately, even though you only clicked two points close together in the middle.
3. **Dragging point 1** moves only point 1; the line rotates around fixed point 2.
4. **Dragging point 2** moves only point 2; the line rotates around fixed point 1.

### Vertical Line
1. Click the tool, **one click** on any bar/time.
2. An instant full-height line appears through that exact time, top to bottom of the chart.
3. A small tag appears at the bottom on the time axis (not the price axis) showing which bar/date it's on.
4. Drag it → your cursor's vertical (Y) movement is ignored entirely; only horizontal (X) drag matters, snapping the line to a new bar/time.

### Cross Line
1. One click places it — like horizontal + vertical combined.
2. You'll see both a horizontal line and a vertical line crossing at your click point, and axis tags on **both** the price axis and the time axis simultaneously.
3. Drag it → both X and Y respond together (full free-drag, unlike horizontal-only or vertical-only tools) — the cross moves as one unit to wherever you drag, both tags update live.

### Parallel Channel
1. Click point 1, click point 2 → exactly like a trend line, this becomes the main line.
2. **Move your mouse without clicking again** — a second, parallel "ghost" line follows your cursor's distance from the main line (this is the channel width being previewed live).
3. **Click a third time** to lock that width in. Now you have two parallel lines with a shaded band between them.
4. Select it → handles at point 1, point 2 (on the main line), and a third handle on the second line.
5. Drag point 1 or 2 → behaves exactly like a trend line (only that end moves, main line re-slopes).
6. Drag the third handle → it can only move perpendicular to the main line's direction, widening or narrowing the channel; it can't slide along the length of the channel. Horizontal (along-the-line) drag is ignored.
7. Drag the body of either line, or drag anywhere inside the shaded band (not on a handle) → the whole channel — both lines together — translates as a rigid unit.

### Rectangle
1. Click point 1 (one corner), then either click point 2 (opposite corner) or, on most builds, **click-drag directly** from corner to corner in one motion instead of two separate clicks.
2. A filled/stroked box appears with those two points as diagonal corners.
3. Select it → 4 corner handles (and often 4 edge-midpoint handles).
4. **Drag any single corner handle** → only that corner moves; the opposite corner is fixed, the two corners in between (top-right/bottom-left) recompute automatically to keep it a rectangle.
5. **Drag an edge-midpoint handle** (if present) → moves just that one edge (e.g., only the top edge slides up/down), the opposite edge stays fixed, adjacent corners follow.
6. **Drag the fill/body** → the whole rectangle translates, same size and shape.

### Fibonacci Retracement
1. Click point 1 at a swing high (or low), click point 2 at the swing low (or high) — same 2-click gesture as trend line.
2. The instant point 2 is placed, a full set of horizontal levels (0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%, and beyond) snap into place between those two prices — you don't draw these; they appear automatically, computed from the ratio between point 1 and point 2's prices.
3. Each level line, by default, extends from point 1's time across to the current right edge of the chart (through empty future space too) — same "always extends right" behavior as a ray, just repeated at each ratio.
4. Select it → you only ever drag the original 2 anchor points (point 1 / point 2). Dragging either one **rescales every single level line simultaneously** — this is the core difference from every other multi-line tool above: the sub-lines aren't independent objects, they're all slaved to the same 2 points and move together, proportionally, the instant you drag either anchor.

---

## Key takeaways for your implementation

1. **Store every point as (time, price), never as raw pixels.** All the "smooth movement while I drag/pan/zoom" behavior you want comes from recomputing pixel position from (time, price) + the current visible range on every render frame — not from tweening pixel coordinates.
2. **Separate "anchor drag" from "body drag"** at the hit-testing level: a small hit-radius around each point handle vs. anywhere else on the shape. This single distinction gives you the "move one point, other stays fixed" vs. "move the whole line" behavior you described.
3. **"Extends into the future" is a rendering rule, not a data rule.** Ray/Horizontal Line/Extended Line/Cross Line don't store a second real anchor for the far end — they store direction + one anchor, and the renderer just draws to whatever the current right (or left) edge of the visible chart is, every frame. New candles don't require updating the drawing at all.
4. **Price-label visibility is per-tool-default, not universal:** on for horizontal-type tools (their entire point is marking a price), off-by-default-but-toggleable for trend-type tools (their point is direction, not a fixed level).
5. Build one generic engine for "N-anchor-point object + style rules (extend/labels/fill)" and nearly all ~50 tools become **configuration**, not separate code paths — only Brush (freehand sampling), Anchored VWAP/Volume Profile (live recompute off volume data), and Measure (ephemeral, non-persistent) genuinely need custom logic beyond that generic engine.

*Source: TradingView's own Help Center documentation (tradingview.com/support), fetched July 2026.*

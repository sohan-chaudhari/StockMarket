# Drawing Engine Architecture Specification

This document defines the engine architecture for the drawing system. It covers the data model, rendering pipeline, event system, undo/redo, serialization, and other engine-level concerns.

UI interactions (keyboard shortcuts, context menus, Object Tree, drawing templates, touch gestures) are defined in the separate UI/UX Specification (Document 3).

---

## 1. Drawing Object Model

Every drawing is represented by a single generic `Drawing` object:

```
Drawing
├── id              string (uuid)
├── type            "trendline" | "hline" | "ray" | "rectangle" | "fibRetracement" | ...
├── schemaVersion   number (start at 1)
├── name            string | null
├── points[]        [{ time: number | null, price: number | null }]
├── style           { color, opacity, thickness, lineStyle, extendLeft, extendRight, ... }
├── text            { content, fontSize, color, bold, italic, align }
├── visibility      { timeframes: string[] | "all", hidden: bool }
├── locked          bool
├── zIndex          number
├── groupId         string | null
├── createdAt       timestamp
├── updatedAt       timestamp
├── owner           userId
├── alerts[]        [{ condition, enabled, notifyVia }]
└── meta            { toolSpecific data }
```

**Key design rules:**
- `selected` and `hovered` are **not** stored on the Drawing object. They are transient UI state held in the engine's selection set (see §5).
- `schemaVersion` must be included from day one to support future schema migrations.
- `points[i].time` may be `null` for tools without a time component (e.g., Horizontal Line). `points[i].price` may be `null` for tools without a price component (e.g., Vertical Line).
- `name` is auto-generated as `"{Type} {n}"` (e.g., "Trend Line 1") but user-editable.

---

## 2. World Coordinates & Infinite Canvas

**Coordinate system:**
- Every point is stored as `(time, price)` in chart (world) coordinates, never as pixel coordinates.
- The renderer converts world → pixel each frame via `timeToX(time) → number` and `priceToY(price) → number`.

**Boundary contract for `timeToX`/`priceToY`:**
- `timeToX(time)`:
  - Returns the x-coordinate in canvas pixels for the given timestamp.
  - For timestamps outside the loaded data range (future or past): returns a value that may be `< 0` or `> canvasWidth`. The renderer uses canvas clip regions to handle overflow.
  - For `time = null` (Horizontal Line): returns `0` (align to left edge).
- `priceToY(price)`:
  - Returns the y-coordinate in canvas pixels for the given price.
  - For prices outside the visible Y-axis range: returns a value that may be `< 0` or `> canvasHeight`. Clipped by canvas region.
  - For `price = null` (Vertical Line): returns `0` (align to top edge).
- Both functions are monotonic and deterministic for a given visible range.
- If the visible range is empty or undefined, both functions return `NaN` — the renderer should skip drawing for that frame.

**Viewport culling:**
- Bucket drawings into a 2D spatial index (grid or R-tree) keyed by their bounding box `(timeMin, timeMax, priceMin, priceMax)`.
- Only drawings whose bounding box intersects the current visible viewport are passed to the renderer.
- **Exception for infinite-extent tools:** Horizontal Line, Vertical Line, Ray, Extended Line, Cross Line, and Horizontal Ray have degenerate bounding boxes (unbounded in one or both axes). These tools are always included in the render pass regardless of viewport culling.

---

## 3. Rendering Pipeline & Layers

The renderer draws layers in this fixed order:

1. Background grid / axes
2. Candles / price series
3. Indicator overlays (MA, VWAP, etc.)
4. Drawings (sorted by `zIndex` ascending)
5. Selection outline + resize/anchor handles (for selected drawings only — rendered at highest zIndex regardless of the drawing's own zIndex)
6. Crosshair
7. Tooltips / floating labels (price tags, stats boxes)
8. Cursor

**Canvas setup:**
- Use `devicePixelRatio` scaling: `canvas.width = cssWidth * devicePixelRatio; canvas.height = cssHeight * devicePixelRatio; ctx.scale(devicePixelRatio, devicePixelRatio)`.
- This ensures sharp rendering on Retina/HiDPI displays and correct hit-testing coordinates.

**Edge-clamping rules for infinite-extent tools:**
- **Horizontal Line:** rendered as a full-width line from `x=0` to `x=canvasWidth` at the anchor's `priceToY(price)`. No `timeToX` call needed — the line has no time extent.
- **Vertical Line:** rendered as a full-height line from `y=0` to `y=canvasHeight` at the anchor's `timeToX(time)`. No `priceToY` call needed.
- **Cross Line:** renders both a Horizontal Line and a Vertical Line at the same anchor point.
- **Ray:** rendered from the first anchor point through the second anchor point, continuing to the canvas edge. Compute intersection of the line with the canvas boundary.
- **Extended Line:** rendered through both anchor points, continuing to both the left and right canvas edges. Compute both intersection points with the canvas boundary.
- **Horizontal Ray:** rendered from the anchor point rightward to the canvas edge. Compute the right-edge intersection.

**Dirty rendering:**
- Only redraw when: points change, style changes, viewport changes (pan/zoom), or a new candle arrives.
- Use a dirty flag: if nothing changed, skip the frame.
- During drag operations, re-render every `requestAnimationFrame` tick.

---

## 4. Hit Testing & Priority

Hit-testing runs in this order per pointer event:

1. **Edge hit** (for line-type tools): point-to-line-segment distance ≤ threshold (e.g., 4px). Test against each visual segment of the drawing.
2. **Anchor handle hit**: point-to-handle-center distance ≤ handle radius (e.g., 6px). Tested before edge/body so handles take priority.
3. **Body hit** (for line-type tools): point-to-line-segment distance ≤ wider threshold (e.g., 12px). This enables "grab anywhere on the line" for translation.
4. **Fill hit** (for filled shapes — rectangle, circle, ellipse, triangle, channel band): point-in-polygon or point-in-ellipse test on the filled area. Only for shapes that have a fill style/opacity.
5. **Midpoint handle hit**: same as anchor handle but for the midpoint convenience handle.

**Priority across drawings:**
- Within drawings: highest `zIndex` wins.
- Same `zIndex`: most recently created wins (insertion order).
- Drawing vs. candle/indicator: drawings always take priority when the pointer is within their hit zone.

**Priority across overlapping selected drawings:**
- Selection handles for overlapping selected drawings obey `zIndex` order: the drawing with higher `zIndex` has its handles rendered on top.

---

## 5. Selection Model

- The selected set is a `Set<drawingId>` held in the engine, not baked into each Drawing object.
- `hoveredId` is a single string (or null) on the engine — only one drawing can be hovered at a time.
- Selection and hover state are exclusively transient UI state. They are never serialized.
- Multi-selection: Ctrl+Click toggles a drawing in/out of the set. Shift+Click is reserved for Object Tree range selection (see Document 3).
- Marquee selection: drag on empty chart space to select all drawings whose bounding box intersects the marquee rectangle.
- When a drawing is deleted, its `id` is removed from the selected set.

---

## 6. Hover / Selected / Dragging Visual States

These are render-time state transitions driven by the engine, not persisted:

| State | Trigger | Visual |
|-------|---------|--------|
| Normal | Default | Standard style per type |
| Hovered | Pointer over drawing (not on handle) | Glow/highlight, cursor changes to pointer |
| Selected | User clicks drawing | Handles appear on all anchor points + midpoint |
| Hovered + Selected | Both conditions true | Glow + handles (handles take visual priority) |
| Dragging (anchor) | Pointer down on handle | That handle follows cursor; rest of drawing updates live |
| Dragging (body) | Pointer down on body/ fill | Entire drawing translates rigidly |
| Locked | `locked = true` | No selection possible; cursor indicates locked state |

---

## 7. Object Lifecycle & Events

**Lifecycle:**
```
null → preview → [validate] → finalized → update → delete → archived (soft-delete)
                      ↑___________________________|
                            (re-edit after creation)
```

- **preview**: temporary state during placement. The drawing exists but is not yet persisted.
- **validate**: checks for degenerate geometry (zero-length trend line, zero-area rectangle) and tool-specific constraints. Rejects with a user-visible message if invalid.
- **finalized**: drawing is persisted, added to spatial index, and becomes selectable.
- **update**: mutation of points, style, text, visibility, or meta.
- **delete**: soft-delete (archived) with undo support.

**Events (pub-sub, decoupled):**
```
drawing:create      { id, type, points }
drawing:update      { id, changes }
drawing:move        { id, deltaTime, deltaPrice }
drawing:select      { ids[] }
drawing:deselect    { ids[] }
drawing:hover       { id }
drawing:delete      { id }
drawing:lock        { id }
drawing:unlock      { id }
drawing:duplicate   { sourceId, newId }
drawing:visibility  { id, hidden }
drawing:group       { groupId, drawingIds[] }
drawing:ungroup     { groupId }
drawing:alert       { id, alert }
drawing:alert:remove { id, alertId }
viewport:changed    { from, to }
```

---

## 8. Undo / Redo

**Architecture:** Command pattern.

```
Command
├── type       "create" | "delete" | "move" | "resize" | "restyle"
|              | "lock" | "unlock" | "duplicate" | "visibility"
|              | "group" | "ungroup" | "alert" | "alert:remove"
├── before     snapshot of affected drawing(s) before the change
└── after      snapshot after the change
```

**Rules:**
- Each command stores full snapshots (deep-clone the Drawing object) for simplicity.
- Batch drags: while the user is actively dragging a point, do NOT push a command per mouse-move frame. Buffer the drag and push a single `move` command on pointer-up.
- Undo/redo stack is per-symbol per-layout.
- Stack is capped at N=100 entries (configurable). Oldest entries are evicted.
- Creating a new drawing after an undo clears the redo stack.
- Importing/loading drawings from storage does NOT clear the undo stack.

---

## 9. Serialization Format

**Storage unit:** one array of `Drawing` objects per `(user, symbol, layout)` key.

**Per-Drawing JSON format:**
```json
{
  "schemaVersion": 1,
  "id": "d_8f3a1c",
  "type": "trendline",
  "name": "Trend Line 1",
  "points": [
    { "time": 1732400000, "price": 452.30 },
    { "time": 1732650000, "price": 471.10 }
  ],
  "style": {
    "color": "#3366FF",
    "opacity": 0.8,
    "thickness": 2,
    "lineStyle": "solid",
    "extendLeft": false,
    "extendRight": false
  },
  "locked": false,
  "zIndex": 10,
  "groupId": null,
  "createdAt": "2025-11-24T10:30:00Z",
  "updatedAt": "2025-11-24T11:00:00Z",
  "owner": "user_abc",
  "alerts": [],
  "meta": {}
}
```

**Serialization rules:**
- `selected`, `hovered` are NEVER serialized (transient UI state).
- `time` is always a Unix timestamp in seconds. For tools with no time component (Horizontal Line), `time` is `null`.
- `price` is always a float. For tools with no price component (Vertical Line), `price` is `null`.
- Version your schema (`schemaVersion: 1`) from day one — every Drawing object carries its own version.

---

## 10. Autosave

- Debounced save: 1 second after the last mutation (configurable).
- Uses a dirty flag: only saves if at least one drawing has changed since the last save.
- Save is atomic: write the full array of drawings for the current `(user, symbol, layout)` key.
- On load: merge incoming drawings with existing by `id`. If a drawing has a higher `schemaVersion` than the current code, flag it as "needs migration."

---

## 11. Collaboration-Readiness

- **v1:** Last-write-wins per drawing `id`, keyed by `updatedAt`. If two users edit the same drawing simultaneously, the one with the later `updatedAt` wins.
- **v2 (future):** Operational Transform or CRDT at the field level for real-time multi-user editing.
- Conflict detection: compare `updatedAt` before applying a remote update. If the local version is newer, surface a conflict resolution prompt.

---

## 12. Permissions

Three tiers per drawing:
- **Owner** (creator): full control — edit, delete, lock, share.
- **Editor**: can modify points and style, cannot delete or change permissions.
- **Viewer**: can see but not interact (read-only layout).

---

## 13. Snapping / Magnet Priority

When magnet mode is enabled, during point placement or drag:

1. **Strong magnet**: snap to the nearest O/H/L/C of the nearest candle within threshold (e.g., 6px).
2. **Weak magnet**: bias toward the nearest O/H/L/C (smooth attraction, not binary snap).
3. **Disabled**: free placement, no snapping.
4. **Temporary toggle**: holding Shift during a drag temporarily inverts the magnet mode.

Snap priority order: nearest O/H/L/C → nearest anchor point of another drawing → nearest grid line.

---

## 14. Resize Handles by Shape Type

| Shape | Handles |
|-------|---------|
| Line (2pt) | 2 end anchors + 1 midpoint diamond |
| Horizontal Line (1pt) | 1 anchor (mid-line) |
| Vertical Line (1pt) | 1 anchor (mid-line) |
| Ray (2pt) | 2 anchors (origin + direction) |
| Rectangle | 4 corners + 4 edge midpoints |
| Circle/Ellipse | 4 cardinal points (N, S, E, W) |
| Triangle | 3 vertices |
| Channel | 3 anchors (2 for trend line, 1 for width) |
| Polyline/Path | Every vertex |

---

## 15. Rotation

- Rotated Rectangle: uses the third anchor point to set rotation angle.
- General rotation: hold a modifier key (e.g., Ctrl) while dragging a corner handle to rotate the shape around its center.
- Rotation snap: holding Shift during rotation snaps to 45° increments.
- Rotation angle is stored in `meta` as degrees.

---

## 16. Touch / Mobile Support

- Use Pointer Events API (pointerdown, pointermove, pointerup) for unified mouse+touch handling.
- `touchAction: 'none'` on the canvas to prevent browser default gestures (scroll, zoom).
- Pinch zoom and two-finger pan are handled by the chart (not the drawing engine) — the engine only receives the resulting viewport change.
- Long press on a handle enters fine-adjustment mode (slower movement for precision).

---

## 17. Performance Targets

| Drawing Count | Target Behavior |
|---------------|-----------------|
| 0–100 | Instant (sub-frame render, <16ms) |
| 100–500 | Snappy (one frame, <50ms) |
| 500–1000 | Usable with noticeable lag (2–3 frames) |
| 1000–5000 | Graceful degradation (skip offscreen drawings; simplified hit-testing) |

**Performance strategies:**
- Spatial index (2D grid or R-tree) for viewport culling.
- Dirty flag: skip render if no state changed.
- Canvas compositing: batch similar drawings into the same canvas draw call where possible.
- Virtualized Object Tree (UI concern — see Document 3).
- Limit visible handles: when >50 drawings are visible on screen, only render handles for the hovered/selected drawing (not all visible drawings).

---

## 18. Data-Generating Drawings (Exceptions)

The following tools require special engine support beyond the generic static-shape model:

- **Ghost Feed**: generates synthetic candle projections based on "Avg HL in minticks" and "Variance" settings. The engine must support a render callback that draws projected candles extending beyond real data.
- **Anchored VWAP**: recomputes VWAP from the anchor point forward on every new candle. Requires a periodic recompute trigger tied to the price feed.
- **Anchored Volume Profile**: recomputes the volume histogram when the visible range changes. Requires data from the bar/volume data store.
- **Measure**: ephemeral, non-persistent ruler — shows a distance label while the mouse is held, disappears on release. Requires a separate lifecycle path that does not save to storage.

These tools are built as plugins/extensions to the generic engine rather than part of the core model.

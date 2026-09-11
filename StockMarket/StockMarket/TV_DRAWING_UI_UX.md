# TradingView Drawing System — UI/UX Specification

## Purpose

This document defines the complete user interface and user experience for the drawing system.

It intentionally excludes:
- Drawing mathematics and tool behavior (Document 1 — Behavior Specification)
- Rendering architecture and engine internals (Document 2 — Engine Architecture Specification)

Instead it defines how users interact with the system.

---

## 1. Design Principles

**Goals:**
- Match TradingView interaction patterns
- Keep mouse movement minimal
- Fast workflows for professional traders
- Keyboard-first operation
- Predictable behavior
- Zero accidental edits
- Responsive on desktop and mobile
- Consistent across every drawing tool

---

## 2. Layout

**Left Toolbar:**
Contains every drawing category. Collapsed by default; hover expands.

Order:
1. Favorites
2. Cursor
3. Trend Tools
4. Gann & Fibonacci
5. Geometric Shapes
6. Annotation Tools
7. Pattern Tools
8. Prediction Tools
9. Measurement Tools
10. Icons
11. Emoji
12. Brush
13. Eraser

**Top Toolbar:**
- Undo / Redo
- Magnet Mode toggle
- Lock All / Hide All
- Keep Tool Active toggle
- Object Tree
- Templates
- Settings
- Alert Manager
- Layout Options

**Right Sidebar (optional):**
- Properties panel (docked mode)
- Object Tree panel
- Alerts panel
- Templates panel
Visible only when required.

**Bottom Status Bar:**
- Current Tool name
- Magnet Mode status
- Selected Object Count
- Current Time
- Current Price
- Zoom %

---

## 3. Toolbar Behaviour

- **Single click**: tool becomes active once. After placement, returns to cursor.
- **Double click**: "Keep Tool Active" is enabled. User may place unlimited objects. ESC exits.
- **Favorites**: star icon on any tool. Favorite tools appear at the top of the toolbar. Drag to reorder.
- **Search**: type to search tools instantly. Supports aliases (e.g., "Trend", "Line", "Horizontal", "Fib", "Pitchfork", "Rectangle").

---

## 4. Cursor Behaviour

| State | Cursor |
|-------|--------|
| Normal | Arrow |
| Hover drawing | Pointer |
| Hover handle | Crosshair |
| Active placement | Crosshair |
| Dragging | Closed hand / grabbing |
| Pan | Grab hand |
| Locked drawing hover | Arrow (no interaction; no selection possible) |

---

## 5. Placement Workflow

Every tool follows the same generic workflow:

1. Click tool in toolbar (or use keyboard shortcut)
2. Cursor changes to crosshair
3. Preview appears following the cursor
4. Click first anchor point
5. Preview updates to show next segment/point
6. Continue clicking until all required anchors are placed
7. Drawing is finalized
8. If "Keep Tool Active" is ON: stay in tool mode for next placement
9. If "Keep Tool Active" is OFF: return to cursor

Exception: Brush tool uses click-drag (not click-click), sampling points continuously while the mouse is held.

---

## 6. Selection UX

**Hover:**
- Glow/highlight on the drawing body
- Cursor changes to pointer

**Selection:**
- Handles appear on every anchor point (and midpoint for line tools)
- Selection outline visible around the drawing

**Multi-selection:**
- **Ctrl + Click**: toggle individual drawing in/out of the selection set
- **Marquee selection**: click empty chart space and drag a rectangle — selects all drawings whose bounding box intersects the marquee
- Selected drawings can be moved, deleted, or copied as a group

**Selection priority (when clicking overlapping drawings):**
1. Top-most (highest zIndex)
2. Newest (most recently created)
3. Oldest

**Floating drawing toolbar:**
When a drawing is selected, a compact action bar appears near the drawing with buttons for: Style, Lock/Unlock, Delete, Alert (clock icon), and Clone.

---

## 7. Drag UX

| Action | Behavior |
|--------|----------|
| Drag anchor handle | Moves only that anchor; other points stay fixed |
| Drag body (line/fill) | Translates entire drawing rigidly |
| Drag body (multi-selection) | Translates all selected drawings as a group |
| Locked drawing | Cannot drag; cursor indicates locked state; no handles shown |
| Drag near viewport edge | Auto-scrolls chart while pointer is held near edge (smooth rAF-based pan) |

---

## 8. Context Menu

Right-click on a drawing (or on empty chart space with a drawing selected) shows:

- Cut
- Copy
- Paste
- Duplicate
- Delete
- Lock / Unlock
- Hide
- Bring Forward
- Send Backward
- Bring to Front
- Send to Back
- Reset Style
- Clone
- Save as Template / Apply Template
- Create Alert
- Properties (opens settings dialog)

---

## 9. Object Tree

The Object Tree panel lists every drawing on the current chart. Each row shows:

- Eye icon (visibility toggle)
- Lock icon (lock/unlock toggle)
- Drawing type icon
- Drawing name (auto-generated, user-editable)
- Alert indicator (bell icon if alerts exist)
- Group indicator (if the drawing belongs to a group)

**Supported actions:**
- Rename (inline edit)
- Reorder by zIndex (drag to reorder z-order)
- Group / Ungroup (right-click or drag-and-drop)
- Hide (eye toggle)
- Lock (lock toggle)
- Delete (trash icon or keyboard)
- Search / Filter by name

---

## 10. Properties Dialog

Opens on double-click or via right-click → Properties. Contains these tabs:

**Style tab:**
- Color, Width, Opacity, Dash pattern
- Fill color/fill opacity (for filled shapes)
- Extend Left / Extend Right toggles
- Arrow ends (start/end arrowhead toggle)
- Background color/opacity

**Text tab:**
- Font family, Size, Bold, Italic
- Alignment (left/center/right relative to drawing)
- Rotation
- Background color
- Inline edit: click text on chart to edit directly

**Coordinates tab:**
- Date/time and price for every anchor point, editable as numeric fields
- Precision control (decimal places)

**Visibility tab:**
- Per-timeframe show/hide (e.g., only on 1H and above)
- Per-layout visibility
- Per-session visibility

**Alerts tab:**
- List of attached alerts
- Create / Edit / Delete / Enable / Disable

---

## 11. Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| **Drawing tools** | |
| Alt + H | Select Horizontal Line tool |
| Alt + T | Select Trend Line tool |
| Alt + F | Select Fibonacci Retracement tool |
| Alt + V | Select Vertical Line tool |
| Alt + C | Select Cross Line tool |
| Alt + A | Create alert at cursor position |
| **Editing** | |
| Delete / Backspace | Delete selected drawing(s) |
| Ctrl + C | Copy selected drawing(s) |
| Ctrl + V | Paste copied drawing(s) |
| Ctrl + X | Cut selected drawing(s) |
| Ctrl + D | Duplicate selected drawing(s) |
| Ctrl + A | Select all drawings on the chart |
| **Undo / Redo** | |
| Ctrl + Z | Undo last action |
| Ctrl + Y | Redo last undone action |
| **View / Navigation** | |
| Arrow keys | Nudge selected drawing 1px (holding Shift = 10px) |
| Shift + drag | Constrain to 45° angle during placement (trend line, channel) |
| Shift + drag | Constrain to horizontal/vertical movement during handle/body drag |
| ESC | Deselect all; exit active tool mode; close properties dialog |
| Space + drag | Pan the chart |
| Ctrl + Space + drag | Pan the chart (alternative) |
| Tab / Shift + Tab | Cycle focus between panels / dialogs |
| Enter | Confirm inline edit; confirm dialog |

**Mac equivalents:**
- Alt → Option
- Ctrl → Cmd (Command)
- Delete → Backspace

Custom shortcuts can be remapped in Settings → Hotkeys.

---

## 12. Mouse Behaviour

| Action | Result |
|--------|--------|
| Left click on drawing | Select (or toggle in multi-select with Ctrl) |
| Left click on empty chart | Deselect; start marquee selection if dragged |
| Double click on drawing | Open Properties dialog |
| Double click on text label | Enter inline text editing |
| Right click on drawing / chart | Open Context Menu |
| Middle click + drag | Pan the chart |
| Space + drag | Pan the chart (primary method for users without middle button) |
| Mouse wheel | Zoom in/out (vertical zoom) |
| Shift + mouse wheel | Horizontal scroll |
| Drag handle near viewport edge | Auto-scroll chart (smooth continuous pan) |

---

## 13. Touch UX

| Gesture | Result |
|---------|--------|
| Tap | Select drawing |
| Double tap | Open Properties dialog |
| Long press (1s) | Context menu |
| Drag (on handle) | Move that anchor |
| Drag (on body) | Move entire drawing |
| Two-finger drag | Pan the chart |
| Pinch | Zoom in/out |
| Long press on handle | Enter fine-adjustment mode (slower movement for precision) |

---

## 14. Mobile UX

- Floating toolbar (collapsible, positioned at bottom of screen)
- Bottom sheet for properties (slides up from bottom, dismissible by swipe-down)
- Larger handle touch targets (minimum 44x44px)
- Gesture priority: chart pan → drawing select → drawing drag
- Touch snapping: snap to nearest OHLC with visual indicator

---

## 15. Snapping UX

| Mode | Behavior |
|------|----------|
| Strong | Snap to nearest O/H/L/C within threshold (6px at current zoom) |
| Weak | Bias toward nearest O/H/L/C (smooth attraction, not binary snap) |
| Disabled | Free placement |

- Visual snap indicator: a small circle or highlight appears at the snap point when active
- Temporary snap preview: shows the snapped position before the user releases
- Holding Shift during a drag temporarily inverts the current magnet mode
- Snap priority: OHLC → other drawing anchors → grid lines

---

## 16. Alerts UX

- Bell icon appears on selected drawing's floating toolbar
- Alert badge (small number) on the Object Tree row
- Alert editor dialog: condition (crossing up / crossing down / crossing), periodicity, notification method (popup, email, webhook)
- Alerts are created, edited, enabled, disabled, and deleted from the Properties dialog (Alerts tab) or from the Alert Manager in the top toolbar

---

## 17. Templates UX

- Save current drawing style as a named template
- Apply template to selected drawing
- Rename / Delete templates
- Import / Export templates (JSON file)
- Default template per tool type
- Per-tool templates (e.g., "My Trend Line Style" applies to all new trend lines)
- Template management panel in the right sidebar

---

## 18. Visibility UX

- Hide / Show individual drawings (Object Tree eye toggle)
- Hide Others (hide all except selected)
- Show All (unhide everything)
- Per-timeframe visibility (Properties → Visibility tab)
- Per-layout visibility
- Per-workspace visibility
- Lock All Drawings / Unlock All Drawings (top toolbar)

---

## 19. Drawing Lifecycle UX

| Phase | User Action | Visual State | Persisted? |
|-------|-------------|--------------|------------|
| Create | Select tool, click anchors | Preview follows cursor | No |
| Hover | Pointer moves over drawing | Glow/highlight | No |
| Select | Click drawing | Handles appear | No |
| Edit | Drag handle or body | Live update during drag | No (saved on release) |
| Edit (properties) | Double-click | Properties dialog opens | On confirm |
| Duplicate | Ctrl+D or right-click | New copy appears with slight offset | Yes |
| Lock | Click lock icon/toggle | Handles disappear; no drag possible | Yes |
| Unlock | Click lock icon/toggle | Handles reappear | Yes |
| Hide | Toggle eye icon | Drawing invisible; still in Object Tree | Yes |
| Delete | Delete key or menu | Drawing removed; undoable | Archived |
| Undo | Ctrl+Z | Previous state restored | Yes |
| Redo | Ctrl+Y | Next state restored | Yes |
| Restore | Undo after delete | Drawing reappears at original position | Yes |

---

## 20. Collaboration UX (Future)

- Presence indicators: show which other users are viewing the same symbol
- Selection indicators: highlight drawings currently selected by other users
- Conflict resolution messages: "User X modified this drawing while you were editing. Keep yours / Accept theirs / Review changes"
- Live updates: remote changes appear in real-time without page refresh

---

## 21. Accessibility

- Full keyboard navigation (Tab through all panels, dialogs, and toolbar items)
- Screen reader labels on all interactive elements (toolbar buttons, handles, Object Tree rows)
- High-contrast mode (theme option)
- Large handle targets (minimum 44px for touch; minimum 32px for mouse)
- Reduced motion option (disable animations)
- Color-blind friendly palette option
- Focus indicators (visible outline on focused elements)
- Focus management: after creating a drawing, focus stays on the chart canvas. After deleting, focus moves to the Object Tree or next drawing. After ESC, focus returns to the toolbar search.

---

## 22. Empty States

| Scenario | Display |
|----------|---------|
| No drawings on chart | "No drawings yet. Select a tool from the toolbar or press Alt+T to draw a trend line." |
| No search results (Object Tree) | "No drawings match your search." |
| No templates saved | "No templates saved. Right-click a drawing and select 'Save as Template' to create one." |
| No alerts | "No alerts set. Select a drawing and click the bell icon to add an alert." |
| No favorites | "Star your most-used tools to add them here." |
| No object selected | Properties panel shows "Select a drawing to view its properties." |

---

## 23. Error Handling

| Scenario | Message / Behavior |
|----------|-------------------|
| Invalid coordinate (e.g., time in the far future) | "This coordinate is outside the available data range. The point will be placed but may not be visible." |
| Cannot place drawing (degenerate geometry) | "Cannot place a zero-length line. Move the cursor further from the starting point." |
| Read-only layout | "This layout is read-only. Duplicate it to make changes." |
| Maximum drawings reached | "Maximum drawing limit reached. Remove some drawings to add more." |
| Failed save | "Could not save drawings. Check your connection and try again." (Auto-retries) |
| Failed sync (collaboration) | "Could not sync changes. Your local changes are preserved." |

---

## 24. Animations

| Element | Animation | Duration |
|---------|-----------|----------|
| Toolbar expand/collapse | Fade + slide | 150ms |
| Context menu open | Fade + scale from cursor | 100ms |
| Selection handles appear | Fade in | 100ms |
| Handles on hover | Scale up (1.0 → 1.2) | 80ms |
| Properties dialog open | Fade + scale from drawing center | 150ms |
| Preview during placement | Continuous update (no animation) | Instant |
| Object Tree filter | List items fade in/out | 100ms |

---

## 25. Visual Design Tokens

| Token | Value |
|-------|-------|
| Spacing unit | 4px |
| Border radius (handles) | 2px |
| Border radius (dialogs) | 6px |
| Handle size (mouse) | 8×8px |
| Handle size (touch) | 12×12px minimum |
| Hit-test threshold (edge) | 4px |
| Hit-test threshold (body) | 12px |
| Selection outline width | 2px |
| z-index: chart canvas | 1 |
| z-index: drawings | 10–999 |
| z-index: selection handles | 1000 |
| z-index: floating toolbar | 1100 |
| z-index: context menu | 2000 |
| z-index: properties dialog | 3000 |
| z-index: tooltips | 4000 |

---

## 26. Performance UX

- Hover latency: <50ms (must feel instant)
- Selection response: <16ms (one frame)
- Handle visibility: only render handles for hovered/selected drawings when >50 drawings are visible
- Loading indicator: show a spinner when loading drawings from storage takes >500ms
- Object Tree virtualization: virtualized list for >100 drawings to maintain smooth scrolling

---

## 27. Complete User Flows

For tool-specific navigation, creation, editing, and deletion flows, refer to Document 1 (Behavior Specification) for the exact anchor-point behavior of each tool. The generic workflows in this document (§5–§7, §19) apply to all tools with the per-tool anchor counts, drag behaviors, and extension rules defined in Document 1.

Key flows covered generically:
- Creating any drawing: §5 Placement Workflow
- Editing any drawing: §7 Drag UX + §10 Properties Dialog
- Multi-selection: §6 Selection UX
- Copy/Paste: §11 Keyboard Shortcuts
- Undo/Redo: §11 Keyboard Shortcuts
- Templates: §17 Templates UX
- Alerts: §16 Alerts UX
- Lock/Unlock: §19 Drawing Lifecycle UX
- Hide/Show: §18 Visibility UX
- Object Tree management: §9 Object Tree
- Mobile interaction: §14 Mobile UX
- Keyboard-only workflow: §11 Keyboard Shortcuts

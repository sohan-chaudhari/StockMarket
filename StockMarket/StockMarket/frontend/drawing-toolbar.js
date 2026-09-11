/**
 * drawing-toolbar.js - Interactive Drawing Toolbar & Draggable Floating Favorites Bar
 * Renders nested tool families, dynamic sidebar icon swaps, flyout submenus with stars,
 * and a standalone draggable floating favorites bar overlaying the chart.
 */

(function () {
  'use strict';

  const { ICONS, TOOLBAR_FAMILIES, ALL_TOOLS_MAP, store } = window.ToolbarConfig;

  // Add Close icon to ICONS if not present
  ICONS.close = `<svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;

  class DrawingToolbarManager {
    constructor() {
      this.toolbarEl = null;
      this.floatingBarEl = null;
      this.isDraggingFloatingBar = false;
      this.dragOffset = { x: 0, y: 0 };
      this.activeOpenFamilyId = null;

      // Bind store events
      store.subscribe((event, data) => {
        if (event === 'activeToolChange') {
          this.updateActiveHighlights(data);
        } else if (event === 'lastSelectedChange') {
          this.updateParentIcon(data.familyId, data.toolId);
        } else if (event === 'favoritesChange') {
          this.updateStarsUI();
          this.renderFloatingFavoritesBar();
        }
      });
    }

    init() {
      this.toolbarEl = document.getElementById('drawing-toolbar');
      if (this.toolbarEl) {
        this.renderLeftSidebar();
      }
      this.renderFloatingFavoritesBar();
      this.bindGlobalEvents();
      this.updateActiveHighlights(store.activeDrawingTool);
    }

    /**
     * Render the Left Sidebar Toolbar
     */
    renderLeftSidebar() {
      if (!this.toolbarEl) return;
      this.toolbarEl.innerHTML = '';

      TOOLBAR_FAMILIES.forEach((family, index) => {
        const familyItem = document.createElement('div');
        familyItem.className = 'toolbar-item family-item';
        familyItem.setAttribute('data-tool-family', family.id);
        familyItem.setAttribute('title', family.title);

        const currentToolId = store.getLastSelectedToolId(family.id);
        const currentTool = ALL_TOOLS_MAP[currentToolId] || { name: family.title, icon: ICONS[currentToolId] || ICONS[family.defaultToolId] };

        // Parent Icon Container
        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'family-icon-wrapper';
        iconWrapper.innerHTML = currentTool.icon;
        familyItem.appendChild(iconWrapper);

        // Side Arrow (Chevron right `>` visible on hover with clean external gap)
        const sideArrow = document.createElement('div');
        sideArrow.className = 'side-arrow-indicator';
        sideArrow.setAttribute('title', 'Open ' + family.title + ' menu');
        sideArrow.innerHTML = `<svg viewBox="0 0 8 14"><path d="M1.5 2L6.5 7L1.5 12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        familyItem.appendChild(sideArrow);

        // Flyout Submenu Panel
        const submenu = document.createElement('div');
        submenu.className = 'drawing-submenu';
        submenu.id = `submenu-${family.id}`;

        if (family.groups) {
          family.groups.forEach((group) => {
            if (group.header) {
              const header = document.createElement('div');
              header.className = 'submenu-header';
              header.textContent = group.header;
              submenu.appendChild(header);
            }
            group.items.forEach(tool => {
              submenu.appendChild(this._createSubmenuItem(tool, family.id));
            });
          });
        } else if (family.items) {
          family.items.forEach(tool => {
            submenu.appendChild(this._createSubmenuItem(tool, family.id));
          });
        }

        familyItem.appendChild(submenu);

        // --- Click to Open Flyout Menu ---
        familyItem.addEventListener('click', (e) => {
          if (e.target.closest('.drawing-submenu')) return;

          e.stopPropagation();
          e.preventDefault();
          const isOpen = submenu.classList.contains('visible');
          if (isOpen) {
            this.closeAllSubmenus();
          } else {
            this.openSubmenu(family.id);
          }
        });

        this.toolbarEl.appendChild(familyItem);

        // Add separators between major family groups
        if (index === 0 || index === 2 || index === 4 || index === 6 || index === 7) {
          const sep = document.createElement('div');
          sep.className = 'toolbar-separator';
          this.toolbarEl.appendChild(sep);
        }
      });

      // Render Bottom Action & Toggle Buttons
      this._renderBottomActions();
    }

    _createSubmenuItem(tool, familyId) {
      const item = document.createElement('div');
      item.className = 'submenu-item';
      item.setAttribute('data-tool-id', tool.id);

      // Icon
      const iconEl = document.createElement('div');
      iconEl.className = 'submenu-item-icon';
      iconEl.innerHTML = tool.icon;
      item.appendChild(iconEl);

      // Label
      const label = document.createElement('span');
      label.className = 'submenu-item-name';
      label.textContent = tool.name;
      item.appendChild(label);

      // Shortcut Badge
      if (tool.shortcut) {
        const shortcut = document.createElement('span');
        shortcut.className = 'tool-shortcut-badge';
        shortcut.textContent = tool.shortcut;
        item.appendChild(shortcut);
      }

      // Favorite Star Toggle
      const starBtn = document.createElement('button');
      starBtn.className = 'favorite-star-btn';
      starBtn.setAttribute('title', store.isFavorited(tool.id) ? 'Remove from favorites' : 'Add to favorites');
      starBtn.setAttribute('data-tool-id', tool.id);
      starBtn.innerHTML = store.isFavorited(tool.id) ? ICONS.star_filled : ICONS.star_empty;
      if (store.isFavorited(tool.id)) starBtn.classList.add('favorited');

      // Intercept pointerdown & click on the star so clicking star NEVER selects tool or closes menu
      const toggleStarHandler = (e) => {
        e.stopPropagation();
        e.stopImmediatePropagation();
        e.preventDefault();
        store.toggleFavorite(tool.id);
        this.updateStarsUI();
        this.renderFloatingFavoritesBar();
      };

      starBtn.addEventListener('pointerdown', toggleStarHandler);
      starBtn.addEventListener('click', toggleStarHandler);
      item.appendChild(starBtn);

      // Item Click: activate tool, update last-selected, update parent icon, close submenu
      item.addEventListener('click', (e) => {
        if (e.target.closest('.favorite-star-btn')) return;
        e.stopPropagation();
        store.setActiveTool(tool.id);
        if (window.activateTool) {
          window.activateTool(tool.id, e);
        }
        this.closeAllSubmenus();
      });

      return item;
    }

    _renderBottomActions() {
      // 1. Magnet Mode Toggle
      const magnetItem = document.createElement('div');
      magnetItem.className = 'toolbar-item action-item';
      magnetItem.setAttribute('title', 'Magnet Mode');
      magnetItem.setAttribute('data-action', 'magnet');
      magnetItem.innerHTML = ICONS.magnet;
      magnetItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleMagnet) window.toggleMagnet(magnetItem);
      });
      this.toolbarEl.appendChild(magnetItem);

      // 2. Stay in Drawing Mode Toggle
      const stayItem = document.createElement('div');
      stayItem.className = 'toolbar-item action-item';
      stayItem.setAttribute('title', 'Stay in Drawing Mode');
      stayItem.setAttribute('data-action', 'stay_mode');
      stayItem.innerHTML = ICONS.stay_mode;
      stayItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleStayMode) window.toggleStayMode(stayItem);
      });
      this.toolbarEl.appendChild(stayItem);

      // 3. Lock All Drawings Toggle
      const lockItem = document.createElement('div');
      lockItem.className = 'toolbar-item action-item';
      lockItem.setAttribute('title', 'Lock All Drawings');
      lockItem.setAttribute('data-action', 'lock_all');
      lockItem.innerHTML = ICONS.lock_all;
      lockItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleLockAll) window.toggleLockAll(lockItem);
      });
      this.toolbarEl.appendChild(lockItem);

      // 4. Hide All Drawings Toggle
      const hideItem = document.createElement('div');
      hideItem.className = 'toolbar-item action-item';
      hideItem.setAttribute('title', 'Hide All Drawings');
      hideItem.setAttribute('data-action', 'hide_all');
      hideItem.innerHTML = ICONS.hide_all;
      hideItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleHideAll) window.toggleHideAll(hideItem);
      });
      this.toolbarEl.appendChild(hideItem);

      const sep = document.createElement('div');
      sep.className = 'toolbar-separator';
      this.toolbarEl.appendChild(sep);

      // 5. Trash / Delete Drawings Dropdown
      const trashItem = document.createElement('div');
      trashItem.className = 'toolbar-item action-item';
      trashItem.setAttribute('title', 'Remove Drawings');
      trashItem.setAttribute('data-action', 'trash');
      trashItem.innerHTML = ICONS.trash + `<div class="side-arrow-indicator"><svg viewBox="0 0 8 14"><path d="M1.5 2L6.5 7L1.5 12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></div>`;

      const trashSubmenu = document.createElement('div');
      trashSubmenu.className = 'drawing-submenu';
      trashSubmenu.id = 'submenu-remove';

      const delSelected = document.createElement('div');
      delSelected.className = 'submenu-item';
      delSelected.innerHTML = `${ICONS.remove_selected}<span>Remove Selected</span><span class="tool-shortcut-badge">Del</span>`;
      delSelected.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeAllSubmenus();
        if (window.toolManager && window.toolManager.deleteSelected) {
          window.toolManager.deleteSelected();
        }
      });
      trashSubmenu.appendChild(delSelected);

      const delAll = document.createElement('div');
      delAll.className = 'submenu-item';
      delAll.innerHTML = `${ICONS.remove_all}<span>Remove All Drawings</span>`;
      delAll.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeAllSubmenus();
        if (window.toolManager && window.toolManager.clearAll) {
          window.toolManager.clearAll();
        }
      });
      trashSubmenu.appendChild(delAll);

      trashItem.appendChild(trashSubmenu);
      trashItem.addEventListener('click', (e) => {
        if (e.target.closest('.drawing-submenu')) return;
        this.toggleSubmenu('remove', e);
      });

      this.toolbarEl.appendChild(trashItem);
    }

    /**
     * Updates parent icon on left sidebar when a new tool from that family is selected
     */
    updateParentIcon(familyId, toolId) {
      if (!this.toolbarEl) return;
      const familyItem = this.toolbarEl.querySelector(`.toolbar-item[data-tool-family="${familyId}"]`);
      if (!familyItem) return;

      const tool = ALL_TOOLS_MAP[toolId];
      if (!tool) return;

      const iconWrapper = familyItem.querySelector('.family-icon-wrapper');
      if (iconWrapper) {
        iconWrapper.innerHTML = tool.icon;
      }
      familyItem.setAttribute('title', tool.name);
    }

    /**
     * Updates active visual highlights across sidebar, submenus, and floating bar
     */
    updateActiveHighlights(activeToolId) {
      document.querySelectorAll('.toolbar-item, .submenu-item, .fav-tool-btn').forEach(el => {
        el.classList.remove('active');
        el.removeAttribute('data-active');
      });

      if (!activeToolId) return;

      const tool = ALL_TOOLS_MAP[activeToolId];
      if (tool) {
        const familyItem = document.querySelector(`.toolbar-item[data-tool-family="${tool.familyId}"]`);
        if (familyItem) {
          familyItem.classList.add('active');
          familyItem.setAttribute('data-active', 'true');
        }

        const subItem = document.querySelector(`.submenu-item[data-tool-id="${activeToolId}"]`);
        if (subItem) {
          subItem.classList.add('active');
        }

        if (this.floatingBarEl) {
          const favBtn = this.floatingBarEl.querySelector(`.fav-tool-btn[data-tool-id="${activeToolId}"]`);
          if (favBtn) {
            favBtn.classList.add('active');
          }
        }
      }
    }

    /**
     * Updates star toggle icons in all open and closed flyout menus
     */
    updateStarsUI() {
      document.querySelectorAll('.favorite-star-btn').forEach(btn => {
        const toolId = btn.getAttribute('data-tool-id');
        const isFav = store.isFavorited(toolId);
        btn.innerHTML = isFav ? ICONS.star_filled : ICONS.star_empty;
        btn.setAttribute('title', isFav ? 'Remove from favorites' : 'Add to favorites');
        if (isFav) {
          btn.classList.add('favorited');
        } else {
          btn.classList.remove('favorited');
        }
      });
    }

    /**
     * Render / Update Draggable Floating Favorites Bar Component
     */
    renderFloatingFavoritesBar() {
      const container = document.getElementById('chart-container') || document.querySelector('.center-area') || document.body;
      if (!this.floatingBarEl) {
        this.floatingBarEl = document.createElement('div');
        this.floatingBarEl.id = 'floating-favorites-toolbar';
        this.floatingBarEl.className = 'floating-favorites-toolbar';
        container.appendChild(this.floatingBarEl);
        this._initFloatingBarDrag();
      }

      const favoritedIds = store.favoritedToolIds;

      // Auto-hide completely if favorites is empty or hidden
      if (!favoritedIds || favoritedIds.length === 0 || store.isFloatingBarClosed) {
        this.floatingBarEl.style.display = 'none';
        return;
      }

      this.floatingBarEl.style.display = 'flex';
      this.floatingBarEl.innerHTML = '';

      // Position from saved coordinates
      const pos = store.floatingToolbarPos;
      this.floatingBarEl.style.left = `${pos.x}px`;
      this.floatingBarEl.style.top = `${pos.y}px`;

      // 1. Drag Handle Grip
      const grip = document.createElement('div');
      grip.className = 'fav-drag-handle';
      grip.setAttribute('title', 'Drag to reposition toolbar');
      grip.innerHTML = ICONS.drag_grip;
      this.floatingBarEl.appendChild(grip);

      // 2. Favorite Tool Buttons
      favoritedIds.forEach(toolId => {
        const tool = ALL_TOOLS_MAP[toolId];
        if (!tool) return;

        const btn = document.createElement('button');
        btn.className = 'fav-tool-btn';
        btn.setAttribute('data-tool-id', tool.id);
        btn.setAttribute('title', `${tool.name}${tool.shortcut ? ' (' + tool.shortcut + ')' : ''} (Right-click to remove)`);
        btn.innerHTML = tool.icon;

        if (store.activeDrawingTool === tool.id) {
          btn.classList.add('active');
        }

        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          store.setActiveTool(tool.id);
          if (window.activateTool) {
            window.activateTool(tool.id, e);
          }
        });

        // Right click to delete / remove tool directly from the floating favorites bar on chart
        btn.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          e.stopPropagation();
          store.toggleFavorite(tool.id);
          this.updateStarsUI();
          this.renderFloatingFavoritesBar();
        });

        this.floatingBarEl.appendChild(btn);
      });


    }

    /**
     * Initialize smooth pointer drag-and-drop for Floating Favorites Bar
     */
    _initFloatingBarDrag() {
      const bar = this.floatingBarEl;
      if (!bar) return;

      bar.addEventListener('pointerdown', (e) => {
        const handle = e.target.closest('.fav-drag-handle');
        if (!handle) return;

        e.preventDefault();
        e.stopPropagation();
        this.isDraggingFloatingBar = true;
        bar.setPointerCapture(e.pointerId);
        bar.classList.add('is-dragging');

        const rect = bar.getBoundingClientRect();
        this.dragOffset.x = e.clientX - rect.left;
        this.dragOffset.y = e.clientY - rect.top;
      });

      bar.addEventListener('pointermove', (e) => {
        if (!this.isDraggingFloatingBar) return;
        e.preventDefault();

        const parent = bar.parentElement || document.body;
        const parentRect = parent.getBoundingClientRect();

        let newX = e.clientX - parentRect.left - this.dragOffset.x;
        let newY = e.clientY - parentRect.top - this.dragOffset.y;

        const minX = 10;
        const maxX = parentRect.width - bar.offsetWidth - 10;
        const minY = 10;
        const maxY = parentRect.height - bar.offsetHeight - 10;

        newX = Math.max(minX, Math.min(newX, maxX));
        newY = Math.max(minY, Math.min(newY, maxY));

        bar.style.left = `${newX}px`;
        bar.style.top = `${newY}px`;
      });

      const stopDrag = (e) => {
        if (!this.isDraggingFloatingBar) return;
        this.isDraggingFloatingBar = false;
        try { bar.releasePointerCapture(e.pointerId); } catch (_) {}
        bar.classList.remove('is-dragging');

        const left = parseInt(bar.style.left, 10) || 72;
        const top = parseInt(bar.style.top, 10) || 120;
        store.savePosition(left, top);
      };

      bar.addEventListener('pointerup', stopDrag);
      bar.addEventListener('pointercancel', stopDrag);
    }

    openSubmenu(familyId) {
      this.closeAllSubmenus();
      const submenu = document.getElementById(`submenu-${familyId}`);
      if (submenu) {
        submenu.classList.add('visible');
        this.activeOpenFamilyId = familyId;
        const item = this.toolbarEl?.querySelector(`.toolbar-item[data-tool-family="${familyId}"]`);
        if (item) item.classList.add('menu-open');
      }
    }

    toggleSubmenu(familyId) {
      const submenu = document.getElementById(`submenu-${familyId}`);
      if (!submenu) return;
      const isVisible = submenu.classList.contains('visible');
      this.closeAllSubmenus();
      if (!isVisible) {
        submenu.classList.add('visible');
        this.activeOpenFamilyId = familyId;
        const item = this.toolbarEl?.querySelector(`.toolbar-item[data-tool-family="${familyId}"]`);
        if (item) item.classList.add('menu-open');
      }
    }

    closeAllSubmenus() {
      document.querySelectorAll('.drawing-submenu').forEach(el => el.classList.remove('visible'));
      document.querySelectorAll('.toolbar-item.family-item').forEach(el => el.classList.remove('menu-open'));
      this.activeOpenFamilyId = null;
    }

    bindGlobalEvents() {
      // Close on click outside sidebar & submenus
      document.addEventListener('pointerdown', (e) => {
        if (!e.target.closest('.toolbar-item') && !e.target.closest('.drawing-submenu') && !e.target.closest('.floating-favorites-toolbar')) {
          this.closeAllSubmenus();
        }
      });

      window.updateMagnetUI = () => {
        if (!window.toolManager) return;
        const isActive = window.toolManager.engine.snapping.isActive;
        const el = document.querySelector('.toolbar-item[data-action="magnet"]');
        if (el) {
          el.setAttribute('data-active', isActive ? 'true' : 'false');
          el.setAttribute('title', isActive ? 'Magnet ON' : 'Magnet OFF');
        }
      };

      window.toggleSubmenu = (id, event) => {
        this.toggleSubmenu(id.replace('submenu-', ''));
      };
    }
  }

  window.drawingToolbarManager = new DrawingToolbarManager();

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => window.drawingToolbarManager.init());
  } else {
    window.drawingToolbarManager.init();
  }

})();

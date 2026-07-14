/**
 * LEVERAGE — Cross-Page API Cache + Navigation Speed System
 * ============================================================
 * Provides stale-while-revalidate caching for ALL pages.
 * Works via localStorage (survives page navigation).
 *
 * Usage:
 *   PageCache.fetch('/api/live-prices', { method:'POST', body:... }, 15000, function(data) {
 *       renderIndices(data);
 *   });
 *
 * On first visit: fetches → renders → caches
 * On revisit:     renders cached immediately → fetches in bg → re-renders silently
 */

(function () {
    'use strict';

    var STORE_PREFIX = 'lv_pc_';
    var MAX_KEYS = 40; // max number of cached URLs before eviction

    function _key(url, bodyStr) {
        return STORE_PREFIX + (url + (bodyStr || '')).replace(/[^a-zA-Z0-9]/g, '_').slice(0, 120);
    }

    function _read(k) {
        try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; }
    }

    function _write(k, data) {
        try {
            // Evict oldest if too many keys
            var allKeys = Object.keys(localStorage).filter(function (x) { return x.startsWith(STORE_PREFIX); });
            if (allKeys.length >= MAX_KEYS) {
                // Sort by cachedAt and remove the oldest
                var withTs = allKeys.map(function (x) {
                    try { return { k: x, ts: (JSON.parse(localStorage.getItem(x)) || {}).cachedAt || 0 }; } catch (e) { return { k: x, ts: 0 }; }
                }).sort(function (a, b) { return a.ts - b.ts; });
                for (var i = 0; i < Math.ceil(MAX_KEYS * 0.25); i++) {
                    try { localStorage.removeItem(withTs[i].k); } catch (e) {}
                }
            }
            localStorage.setItem(k, JSON.stringify({ data: data, cachedAt: Date.now() }));
        } catch (e) {
            // localStorage full — clear old lv_pc_ keys
            try {
                Object.keys(localStorage)
                    .filter(function (x) { return x.startsWith(STORE_PREFIX); })
                    .forEach(function (x) { localStorage.removeItem(x); });
                localStorage.setItem(k, JSON.stringify({ data: data, cachedAt: Date.now() }));
            } catch (e2) {}
        }
    }

    /**
     * Stale-while-revalidate fetch.
     * @param {string} url
     * @param {object|null} fetchOpts - fetch options (method, headers, body). Pass null for GET.
     * @param {number} ttlMs - how long before cache is considered stale (still shown, bg refresh)
     * @param {function} callback - called with data immediately (from cache) and again when fresh
     * @returns {Promise<any>}
     */
    window.PageCache = {
        fetch: async function (url, fetchOpts, ttlMs, callback) {
            var bodyStr = (fetchOpts && fetchOpts.body) ? String(fetchOpts.body) : '';
            var k = _key(url, bodyStr);
            var entry = _read(k);
            var now = Date.now();
            var isStale = !entry || (now - entry.cachedAt) > ttlMs;
            var hasCached = entry && entry.data;

            // Immediately paint from cache
            if (hasCached && callback) {
                try { callback(entry.data, true /* fromCache */); } catch (e) {}
            }

            // Fetch fresh in background (or foreground if no cache)
            var doFetch = async function () {
                try {
                    var opts = fetchOpts || {};
                    var res = await fetch(url, opts);
                    if (!res.ok) return null;
                    var data = await res.json();
                    _write(k, data);
                    if (callback) {
                        try { callback(data, false /* fresh */); } catch (e) {}
                    }
                    return data;
                } catch (e) { return null; }
            };

            if (!hasCached) {
                // No cache — must wait
                return await doFetch();
            } else if (isStale) {
                // Has stale cache — background refresh
                doFetch();
                return entry.data;
            } else {
                // Fresh cache — no fetch needed
                return entry.data;
            }
        },

        /** Clear all page cache entries */
        clear: function () {
            Object.keys(localStorage)
                .filter(function (x) { return x.startsWith(STORE_PREFIX); })
                .forEach(function (x) { localStorage.removeItem(x); });
        },

        /** Invalidate a specific URL */
        invalidate: function (url, bodyStr) {
            try { localStorage.removeItem(_key(url, bodyStr || '')); } catch (e) {}
        }
    };

    // ─── Navigation Progress Bar ───────────────────────────────────────────────
    // Shows a slim top bar on navigation clicks (makes pages feel instant)
    (function initProgressBar() {
        var bar = document.createElement('div');
        bar.id = 'lv-nav-bar';
        bar.style.cssText = [
            'position:fixed',
            'top:0',
            'left:0',
            'height:2px',
            'width:0%',
            'background:linear-gradient(90deg,#007AFF,#00e5ff)',
            'z-index:99999',
            'transition:width 0.25s ease,opacity 0.3s ease',
            'pointer-events:none',
            'box-shadow:0 0 8px rgba(0,122,255,0.6)',
            'opacity:0',
        ].join(';');
        document.documentElement.appendChild(bar);

        var _timer = null;
        var _started = false;

        function startBar() {
            if (_started) return;
            _started = true;
            bar.style.opacity = '1';
            bar.style.width = '0%';
            bar.style.transition = 'width 0.25s ease,opacity 0.3s ease';
            // Animate to 85% quickly, stall there
            setTimeout(function () { bar.style.width = '70%'; }, 10);
            setTimeout(function () { bar.style.width = '85%'; }, 300);
            _timer = setTimeout(function () { bar.style.width = '95%'; }, 1500);
        }

        function finishBar() {
            clearTimeout(_timer);
            bar.style.transition = 'width 0.15s ease,opacity 0.4s ease 0.1s';
            bar.style.width = '100%';
            setTimeout(function () {
                bar.style.opacity = '0';
                setTimeout(function () { bar.style.width = '0%'; _started = false; }, 400);
            }, 150);
        }

        // Intercept nav clicks — start bar before browser starts loading
        document.addEventListener('click', function (e) {
            var a = e.target.closest('a[href]');
            if (!a) return;
            var href = a.getAttribute('href');
            if (!href || href === '#' || href.startsWith('javascript') || href.startsWith('mailto') || href.startsWith('tel')) return;
            if (a.target === '_blank') return;
            // Only trigger for same-origin .html navigation
            if (href.endsWith('.html') || href.match(/^[^/][^.]*\.html/)) {
                startBar();
            }
        }, true);

        // Finish bar when new page is painted
        window.addEventListener('pageshow', finishBar);
        document.addEventListener('DOMContentLoaded', finishBar);
        window.addEventListener('load', finishBar);

        window._lvNavBarStart = startBar;
        window._lvNavBarFinish = finishBar;
    })();

    // ─── Page Fade-In Animation ────────────────────────────────────────────────
    (function initPageFade() {
        var style = document.createElement('style');
        style.textContent = [
            '@keyframes lv-fade-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}',
            'body.lv-page-ready main,.lv-page-ready .page-content{animation:lv-fade-in 0.22s ease forwards}',
        ].join('');
        document.head.appendChild(style);

        function markReady() {
            document.body.classList.add('lv-page-ready');
            if (window._lvNavBarFinish) window._lvNavBarFinish();
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', markReady);
        } else {
            // Already ready (e.g. bfcache restore)
            requestAnimationFrame(markReady);
        }

        // bfcache restore (back/forward button)
        window.addEventListener('pageshow', function (e) {
            if (e.persisted) {
                // Page restored from bfcache — instant, no reload needed
                document.body.classList.remove('lv-page-ready');
                requestAnimationFrame(function () {
                    document.body.classList.add('lv-page-ready');
                    if (window._lvNavBarFinish) window._lvNavBarFinish();
                });
            }
        });
    })();

})();

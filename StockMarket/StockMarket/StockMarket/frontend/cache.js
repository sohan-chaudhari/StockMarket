(function () {
  var DB_NAME = 'stockChartCache';
  var DB_VERSION = 2;
  var STORE_NAME = 'candle_data';
  var CACHE_SCHEMA_VERSION = 3;
  var _dbPromise = null;
  var _pruning = false;

  function _getTTL(interval) {
    var baseMin = { '5m':5, '15m':10, '30m':15, '1h':20, '1D':30, '1W':60, '1M':120, '3M':240, '6M':360, '1Y':720, 'ALL':1440 }[interval] || 30;
    if (!window._marketOpen) baseMin *= 4;
    return baseMin * 60 * 1000;
  }

  function _openDB() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function (e) {
        var db = e.target.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'cacheKey' });
        }
      };
      req.onsuccess = function (e) { resolve(e.target.result); };
      req.onerror = function (e) { _dbPromise = null; reject(e.target.error); };
    });
    return _dbPromise;
  }

  window._getStorageBudget = async function () {
    try {
      if (!navigator.storage || !navigator.storage.estimate) return 50 * 1024 * 1024;
      var est = await navigator.storage.estimate();
      var available = est.quota - est.usage;
      return Math.max(20 * 1024 * 1024, Math.min(200 * 1024 * 1024, Math.floor(available * 0.2)));
    } catch (e) {
      return 50 * 1024 * 1024;
    }
  };

  window._idbGetCandles = async function (ticker, interval, range) {
    try {
      var cacheKey = ticker + '|' + interval + '|' + range;
      var db = await _openDB();
      return new Promise(function (resolve) {
        var tx = db.transaction(STORE_NAME, 'readonly');
        var req = tx.objectStore(STORE_NAME).get(cacheKey);
        req.onsuccess = function () {
          var entry = req.result;
          if (!entry || entry.schemaVersion !== CACHE_SCHEMA_VERSION) { resolve(null); return; }
          if (Date.now() - entry.cachedAt > _getTTL(interval)) { resolve(null); return; }
          resolve(entry.data);
        };
        req.onerror = function () { resolve(null); };
      });
    } catch (e) { return null; }
  };

  window._idbSetCandles = async function (ticker, interval, range, data) {
    try {
      var cacheKey = ticker + '|' + interval + '|' + range;
      var db = await _openDB();
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).put({ cacheKey: cacheKey, schemaVersion: CACHE_SCHEMA_VERSION, data: data, cachedAt: Date.now() });
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    } catch (e) {}
  };

  window._idbDeleteCandles = async function (ticker, interval, range) {
    try {
      var cacheKey = ticker + '|' + interval + '|' + range;
      var db = await _openDB();
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE_NAME, 'readwrite');
        tx.objectStore(STORE_NAME).delete(cacheKey);
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { reject(tx.error); };
      });
    } catch (e) {}
  };

  window._recentRanges = {
    _map: new Map(),
    _max: 10,
    get: function (key) {
      var entry = this._map.get(key);
      if (!entry) return null;
      var parts = key.split('|');
      var interval = parts[1] || '';
      if (Date.now() - entry.cachedAt > _getTTL(interval)) { this._map.delete(key); return null; }
      this._map.delete(key); this._map.set(key, entry);
      return entry.data;
    },
    set: function (key, data) {
      if (this._map.has(key)) this._map.delete(key);
      while (this._map.size >= this._max) {
        var oldest = this._map.keys().next().value;
        this._map.delete(oldest);
      }
      this._map.set(key, { data: data, cachedAt: Date.now() });
    },
    updateMax: function (budget) {
      this._max = Math.max(10, Math.min(100, Math.floor(budget / 50000)));
    }
  };

  window._pruneExpiredCache = async function () {
    if (_pruning) return;
    _pruning = true;
    try {
      var budget = await window._getStorageBudget();
      var db = await _openDB();
      var tx = db.transaction(STORE_NAME, 'readwrite');
      tx.oncomplete = function () { _pruning = false; };
      tx.onerror = function () { _pruning = false; };
      var allReq = tx.objectStore(STORE_NAME).getAll();
      allReq.onsuccess = function () {
        var entries = allReq.result || [];
        var toDelete = [];
        var totalSize = 0;
        var sizes = {};
        entries.forEach(function (entry) {
          var interval = (entry.cacheKey || '').split('|')[1] || '';
          var est = JSON.stringify(entry).length;
          totalSize += est;
          sizes[entry.cacheKey] = est;
          if (Date.now() - entry.cachedAt > _getTTL(interval)) {
            toDelete.push(entry.cacheKey);
          }
        });
        if (totalSize > budget * 0.8) {
          entries.sort(function (a, b) { return a.cachedAt - b.cachedAt; });
          var target = budget * 0.5;
          for (var i = 0; i < entries.length && totalSize > target; i++) {
            var k = entries[i].cacheKey;
            if (toDelete.indexOf(k) === -1) { toDelete.push(k); totalSize -= (sizes[k] || 0); }
          }
        }
        toDelete.forEach(function (k) { tx.objectStore(STORE_NAME).delete(k); });
      };
    } catch (e) { _pruning = false; }
    if (document.hidden) return;
    window._pruneCacheTimer = setTimeout(window._pruneExpiredCache, 60000);
  };

  window._getStorageBudget().then(function (b) { window._recentRanges.updateMax(b); }).catch(function () {});
  setTimeout(window._pruneExpiredCache, 15000);
})();

  window._fetchCached = async function(url, ttlMs, callback) {
      try {
          var cached = sessionStorage.getItem(url);
          var fetchBg = async function() {
              try {
                  var r = await fetch(url);
                  if (!r.ok) return null;
                  var data = await r.json();
                  sessionStorage.setItem(url, JSON.stringify({ts: Date.now(), data: data}));
                  if (callback) callback(data);
                  return data;
              } catch (e) { return null; }
          };
          if (cached) {
              var parsed = JSON.parse(cached);
              if (callback) callback(parsed.data);
              if (Date.now() - parsed.ts > ttlMs) {
                  fetchBg(); // stale-while-revalidate
              }
              return parsed.data;
          } else {
              var data = await fetchBg();
              return data;
          }
      } catch (err) {
          try {
             var res = await fetch(url);
             var d = await res.json();
             if (callback) callback(d);
             return d;
          } catch(e) { return null; }
      }
  };


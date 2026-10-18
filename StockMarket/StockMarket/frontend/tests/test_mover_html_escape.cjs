/**
 * SEC-07 regression: unescaped innerHTML interpolation in dashboard.js.
 *
 * renderMoverList() (the Top Gainers / Top Losers / Most Active lists) built its
 * rows with innerHTML and interpolated four provider/DB-sourced strings with no
 * escaping: s.sector, s.logo (into src="..."), s.ticker (into <h4>), and the
 * avatar initials (into an inline JS string inside onerror=). Any of those
 * carrying `"><img src=x onerror=...>` executes script in every visitor's page.
 *
 * Two details this test pins down, because they are the reason the fix is not a
 * naive "wrap it in escapeHTML()" change:
 *   1. the pre-existing escapeHTML() round-trips through a text node, so it
 *      escapes &, < and > but NOT quotes -- useless inside src="...";
 *   2. the avatar initials sat inside an inline event-handler JS string, where
 *      HTML entities are decoded BEFORE the JS is parsed, so entity escaping of
 *      the quote does not stop a breakout -- they must be non-special by
 *      construction.
 *
 * It drives the REAL renderMoverList extracted from the shipped dashboard.js
 * (not a reimplementation), so a future edit that drops the escaping fails here.
 */
const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function ok(c, n) { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } }

const DASH = path.join(__dirname, '..', 'dashboard.js');
const src = fs.readFileSync(DASH, 'utf8');

// ---- extract a top-level function by name -----------------------------------
// These are top-level declarations, so the closing brace is the first line that
// is exactly "}" (a one-line function closes on its own line). Line-based
// extraction avoids having to tokenise regex literals such as /'/g. The result
// is validated by compiling it, so a wrong slice fails loudly instead of
// silently testing a fragment.
function extractFn(source, name) {
  const lines = source.split('\n');
  const startIdx = lines.findIndex((l) => l.startsWith('function ' + name + '('));
  if (startIdx < 0) throw new Error('function not found in dashboard.js: ' + name);
  const oneLine = /^function \S+\(.*\)\s*\{.*\}\s*$/.test(lines[startIdx]);
  if (oneLine) {
    new Function(lines[startIdx] + '\nreturn ' + name + ';');
    return lines[startIdx];
  }
  for (let i = startIdx + 1; i < lines.length; i++) {
    if (/^\}\s*$/.test(lines[i])) {
      const body = lines.slice(startIdx, i + 1).join('\n');
      new Function(body + '\nreturn ' + name + ';'); // throws if the slice is wrong
      return body;
    }
  }
  throw new Error('unterminated function: ' + name);
}

// A document stub faithful to the browser on the one point that matters:
// text-node -> innerHTML escapes &, <, > but leaves quotes alone.
function makeDoc(elements) {
  return {
    getElementById: (id) => (elements && elements[id]) || null,
    createElement: () => {
      const el = { _t: '' };
      el.appendChild = (node) => { el._t = String(node.textContent); };
      Object.defineProperty(el, 'innerHTML', {
        get() { return el._t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
      });
      return el;
    },
    createTextNode: (t) => ({ textContent: t }),
  };
}

// ---- 1. the root cause: escapeHTML() does not escape quotes -------------------
{
  const escHtml = new Function('document', extractFn(src, 'escapeHTML') + '; return escapeHTML;')(makeDoc({}));
  ok(escHtml('"') === '"', 'escapeHTML() leaves a double quote intact (why it is unsafe in src="...")');
  ok(escHtml("'") === "'", 'escapeHTML() leaves a single quote intact (unsafe in an inline handler)');
  ok(escHtml('<b>') === '&lt;b&gt;', 'escapeHTML() does escape < and > (so it is only a text-context helper)');
}

// ---- 2. the new strict escaper ------------------------------------------------
let escapeHTMLStrict;
{
  escapeHTMLStrict = new Function('document', extractFn(src, 'escapeHTMLStrict') + '; return escapeHTMLStrict;')(makeDoc({}));
  const payload = '"><img src=x onerror=alert(1)>';
  const out = escapeHTMLStrict(payload);
  ok(!/[<>"]/.test(out), 'escapeHTMLStrict() removes <, > and " from an attribute-breaking payload');
  ok(out.indexOf('&quot;') !== -1 && out.indexOf('&lt;img') !== -1, 'escapeHTMLStrict() encodes the payload instead');
  ok(escapeHTMLStrict("'") === '&#39;', 'escapeHTMLStrict() encodes a single quote');
  ok(escapeHTMLStrict(null) === '' && escapeHTMLStrict(undefined) === '', 'escapeHTMLStrict() is null-safe');
  ok(escapeHTMLStrict(123) === '123', 'escapeHTMLStrict() coerces non-strings');
}

// ---- 3. renderMoverList end-to-end with a hostile row -------------------------
const container = { innerHTML: '' };
const doc = makeDoc({ gainersList: container });
const factory = new Function(
  'document', 'formatVolumeCompact', '_tickerColor',
  extractFn(src, 'escapeHTMLStrict') + '\n' + extractFn(src, 'renderMoverList') +
  '\nreturn renderMoverList;'
);
const renderMoverList = factory(doc, (v) => String(v), () => '#123456');

const HOSTILE = {
  ticker: '"><img src=x onerror=alert(1)>',
  sector: '"><img src=x onerror=alert(2)>',
  logo: 'https://evil.example/x.png" onerror="alert(3)',
  company_name: '"><img src=x onerror=alert(4)>',
  current_price: 100, change_pct: 5, prev_close: 95, volume: 1234,
};

renderMoverList('gainersList', [HOSTILE]);
const html = container.innerHTML;

ok(html.length > 0, 'renderMoverList() produced rows for the hostile entry');
ok(html.indexOf('<img src=x') === -1, 'no attacker-supplied <img> element is emitted');
ok(html.indexOf('onerror="alert') === -1, 'no attacker-supplied onerror attribute is emitted');
ok(html.indexOf('onerror="alert') === -1, 'the logo payload cannot break out of src="..." into an attribute');
ok((html.match(/<img\b/g) || []).length === 1, 'exactly one <img> per row (the legitimate avatar), not one from data');
ok(html.indexOf('&lt;img src=x') !== -1, 'the hostile sector/ticker is rendered inert, entity-encoded');
// the only onerror ATTRIBUTE in the markup must be our own avatar fallback; the
// payload text "onerror=alert(1)" survives only as inert encoded text (&lt;img…)
const onerrorAttrs = html.match(/onerror="[^"]*"/g) || [];
ok(onerrorAttrs.length === 1, 'exactly one onerror attribute is emitted, and it is not from data');
ok(/^onerror="this\.onerror=null;/.test(onerrorAttrs[0] || ''), 'that one onerror attribute is the legitimate avatar fallback');
ok(onerrorAttrs.join('').indexOf('alert') === -1, 'no onerror attribute contains an attacker-supplied call');

// the legitimate avatar fallback must survive, and its initials must be inert:
// they sit in onerror="...innerHTML='X'", where entities are decoded before the
// JS is parsed, so only a non-special character set is actually safe.
const initials = html.match(/innerHTML='([^']*)'/) || [];
ok(initials.length === 2, 'the avatar fallback still sets initials');
ok(/^[A-Z0-9?]{1,2}$/.test(initials[1] || ''), 'avatar initials are [A-Z0-9] only, so no JS-string breakout: "' + initials[1] + '"');

// ---- 4. the ticker inside the inline onclick JS string ------------------------
{
  container.innerHTML = '';
  renderMoverList('gainersList', [{ ticker: "a'b", current_price: 10, change_pct: 1, prev_close: 9 }]);
  const h = container.innerHTML;
  ok(h.indexOf('ticker=a%27b') !== -1, "a single quote in the ticker is percent-encoded in the onclick payload");
  ok(h.indexOf("ticker=a'b") === -1, "the raw single quote never reaches the inline JS string");
  // document why: encodeURIComponent alone leaves ' unescaped
  ok(encodeURIComponent("'") === "'", "encodeURIComponent() alone does NOT encode ' (the reason for the extra replace)");
}

// ---- 5. normal data still renders correctly ----------------------------------
{
  container.innerHTML = '';
  renderMoverList('gainersList', [{
    ticker: 'TCS', sector: 'IT', logo: '/logos/TCS.svg',
    current_price: 4000, change_pct: 1.25, prev_close: 3950, volume: 1500000,
  }]);
  const h = container.innerHTML;
  ok(h.indexOf('<h4>TCS') !== -1, 'a normal ticker renders in <h4>');
  ok(h.indexOf('class="mover-sector-tag">IT<') !== -1, 'a normal sector renders in its tag');
  ok(h.indexOf('src="/logos/TCS.svg"') !== -1, 'a normal logo renders unchanged in src');
  ok(h.indexOf('IT') !== -1 && h.indexOf('&amp;') === -1, 'normal values are not double-encoded');
}

// ---- 6. the shipped source must keep using the escaper ------------------------
{
  const body = extractFn(src, 'renderMoverList');
  ok(body.indexOf('escapeHTMLStrict(s.sector)') !== -1, 'source: s.sector is escaped');
  ok(body.indexOf('escapeHTMLStrict(logoSrc)') !== -1, 'source: logoSrc is escaped inside src="..."');
  ok(body.indexOf('escapeHTMLStrict(ticker)') !== -1, 'source: the ticker is escaped inside <h4>');
  ok(body.indexOf('+ s.sector +') === -1, 'source: no remaining raw + s.sector + interpolation');
  ok(body.indexOf("+ '" + '<' + "h4>' + ticker") === -1, 'source: no remaining raw ticker interpolation');
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);

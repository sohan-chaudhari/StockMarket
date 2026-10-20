/** Contract test: cross-tab verification.
 *  Behavioural cross-tab testing is not possible in this harness, so this pins the
 *  exact contract: broadcast on success, react only to a fresh payload for THIS
 *  tab's account, then show the verified screen. */
const fs = require('fs'); const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'verify-email.html'), 'utf8');
let pass = 0, fail = 0;
const ok = (c, n) => { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } };

console.log('\n[1] the verifying tab broadcasts');
ok(html.includes("localStorage.setItem('mfv1:auth:verified'"), 'writes mfv1:auth:verified');
ok(/JSON\.stringify\(\{[\s\S]{0,120}email:[\s\S]{0,80}at: Date\.now\(\)/.test(html), 'payload is { email, at }');
ok(/if \(data\.message === 'verified'\) \{[\s\S]{0,600}mfv1:auth:verified/.test(html), 'broadcast happens on the verified response');

console.log('\n[2] other tabs react, safely');
ok(html.includes("addEventListener('storage', _onVerifiedElsewhere)"), 'storage listener registered');
ok(/ev\.key !== 'mfv1:auth:verified'/.test(html), 'ignores unrelated keys');
ok(/Date\.now\(\) - Number\(payload\.at \|\| 0\) > 5 \* 60 \* 1000/.test(html), 'ignores payloads older than 5 minutes');
ok(/_pendingVerifyEmail/.test(html) && /mine !== String\(payload\.email\)\.toLowerCase\(\)/.test(html), 'ignores a different account');
ok(/showPage\('verifiedPage'\)/.test(html), 'shows the verified screen');

console.log('\n[3] the tab knows which account it is waiting for');
ok(/async function startVerificationPolling\(email\) \{[\s\S]{0,200}_pendingVerifyEmail = String\(email/.test(html), 'pending email captured from the polling call');

console.log('\n[4] nothing else in the auth flow changed');
ok(/fetch\(/.test(html), 'existing API calls intact');
ok(!/removeItem\('token'\)/.test(html), 'no token handling added');

console.log('\n  PASS: ' + pass + '   FAIL: ' + fail);
process.exit(fail ? 1 : 0);
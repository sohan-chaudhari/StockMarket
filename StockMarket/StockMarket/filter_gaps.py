import re

with open('frontend/dashboard.js', 'r', encoding='utf-8') as f:
    js = f.read()

# We need to filter the data array in loadChartData and other fetch calls
# The best place is to create a filter function and call it on `data` before it is mapped.

filter_func = """
  // Filter out non-market hours (weekends and times outside 09:15-15:30 IST)
  function filterMarketHours(data) {
    if (!data) return [];
    return data.filter(function(p) {
      if (!p || !p.time) return false;
      var t = new Date(p.time);
      if (isNaN(t.getTime())) return true; // keep if we can't parse
      var day = t.getUTCDay(); // Actually, API might return UTC strings.
      // Convert to IST
      var ist = new Date(t.getTime() + 5.5 * 60 * 60 * 1000);
      var istDay = ist.getUTCDay();
      if (istDay === 0 || istDay === 6) return false; // Skip weekend
      var hours = ist.getUTCHours();
      var mins = ist.getUTCMinutes();
      var timeval = hours * 100 + mins;
      if (timeval < 915 || timeval > 1530) return false;
      return true;
    });
  }
"""

if "function filterMarketHours" not in js:
    js = js.replace('function loadChartData(ticker) {', filter_func + '\n  function loadChartData(ticker) {')

# Now apply filterMarketHours(data) in the fetch callbacks
js = js.replace('if (!Array.isArray(data) || data.length === 0) return;', 'if (!Array.isArray(data) || data.length === 0) return;\n      data = filterMarketHours(data);')
js = js.replace('if (resp && resp.data && resp.data.length > 0) {', 'if (resp && resp.data && resp.data.length > 0) {\n            resp.data = filterMarketHours(resp.data);')
js = js.replace('if (!Array.isArray(missed) || missed.length === 0) return;', 'if (!Array.isArray(missed) || missed.length === 0) return;\n                missed = filterMarketHours(missed);')

with open('frontend/dashboard.js', 'w', encoding='utf-8') as f:
    f.write(js)
print("Updated dashboard.js with market hours filter")

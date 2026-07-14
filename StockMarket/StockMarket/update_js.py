import re

with open('frontend/dashboard.js', 'r', encoding='utf-8') as f:
    js = f.read()

# 1. Update titleEl (ticker-name) to also update chart-ticker-name
js = js.replace("var titleEl = document.getElementById('ticker-name');\n  if (titleEl) titleEl.innerText = ticker;", 
"var titleEl = document.getElementById('ticker-name');\n  if (titleEl) titleEl.innerText = ticker;\n  var chartTicker = document.getElementById('chart-ticker-name');\n  if (chartTicker) chartTicker.innerText = ticker + ' 15m';")

# 2. Update price to also update chart-ticker-price
# Look for where header-price is set
# There are multiple places.
js = js.replace("var pEl = document.getElementById('header-price');  if (pEl) pEl.innerText = fmtPrice(last.close);",
"var pEl = document.getElementById('header-price');  if (pEl) pEl.innerText = fmtPrice(last.close);\n      var cpEl = document.getElementById('chart-ticker-price'); if (cpEl) cpEl.innerText = fmtPrice(last.close);")

js = js.replace("var pEl2 = document.getElementById('header-price');",
"var pEl2 = document.getElementById('header-price'); var cpEl2 = document.getElementById('chart-ticker-price');")

js = js.replace("if (pEl2) pEl2.innerText = fmtPrice(price);",
"if (pEl2) pEl2.innerText = fmtPrice(price);\n      if (cpEl2) cpEl2.innerText = fmtPrice(price);")

with open('frontend/dashboard.js', 'w', encoding='utf-8') as f:
    f.write(js)
print("Updated dashboard.js")

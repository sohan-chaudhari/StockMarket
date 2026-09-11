"""Sync primary drawings.js/drawings.css over the secondary copies."""
import shutil, os

base = "C:\\Users\\sohan\\Desktop\\StockMarket\\StockMarket\\StockMarket"
pairs = [
    (base + "\\frontend\\drawings.js", base + "\\StockMarket\\frontend\\drawings.js"),
    (base + "\\frontend\\drawings.css", base + "\\StockMarket\\frontend\\drawings.css"),
]

for src, dst in pairs:
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print("  " + os.path.basename(src) + " -> secondary OK")
    else:
        print("  WARN: " + src + " not found")

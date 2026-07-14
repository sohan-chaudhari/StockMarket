import re

with open('frontend/stock.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Move nav-root outside container
html = html.replace('<div id="nav-root"></div>', '')

# We will inject nav-root right after body tag
html = html.replace('<body style="background-color: #0A0A0B;">', '<body style="background-color: #0A0A0B;">\n  <div id="nav-root"></div>\n  <div class="main-terminal-area">')

# Close main-terminal-area before the Premium Loader
html = html.replace('</div>\n\n  <!-- Premium Loader -->', '</div>\n  </div> <!-- end main-terminal-area -->\n\n  <!-- Premium Loader -->')

# 2. Extract OHLC div and put it inside chart-container
ohlc_regex = re.compile(r'(<div style="display: flex; align-items: center; gap: 15px; font-family: \'Roboto Mono\', monospace; font-size: 14px; color: #b2b5be; margin-top: 4px;">\s*<span><span style="font-weight: 700;">O:</span>.*?</span>\s*<span><span style="font-weight: 700;">H:</span>.*?</span>\s*<span><span style="font-weight: 700;">L:</span>.*?</span>\s*<span><span style="font-weight: 700;">C:</span>.*?</span>\s*</div>)', re.DOTALL)

match = ohlc_regex.search(html)
if match:
    ohlc_block = match.group(1)
    # Remove it from header
    html = html.replace(ohlc_block, '')
    
    # Inject it inside chart-container
    new_ohlc = ohlc_block.replace('margin-top: 4px;', 'position: absolute; top: 10px; left: 15px; z-index: 10; margin-top: 0;')
    # We should also add id="ohlc-floating-legend"
    new_ohlc = new_ohlc.replace('<div style="', '<div id="ohlc-floating-legend" style="')
    
    html = html.replace('<div id="chart-container" style="position: relative;">', f'<div id="chart-container" style="position: relative;">\n      {new_ohlc}')
else:
    print('OHLC block not found!')

# 3. Take trade buttons out of the OHLC div and put them in header price display
# Wait, currently the trade buttons are inside the OHLC block parent.
# Let's check if there are trade buttons inside the OHLC block parent.

with open('frontend/stock.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Done!')

import re

with open('frontend/stock.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Reduce padding in sidebar-content
html = html.replace('class="sidebar-content" style="padding: 20px; flex: 1; overflow-y: auto;"', 'class="sidebar-content" style="padding: 12px; flex: 1; overflow-y: auto;"')

# Reduce margin bottom of the title area
html = html.replace('margin-bottom:20px;', 'margin-bottom:12px;')

# Reduce margin bottom of position type toggle
html = html.replace('margin-bottom:18px;', 'margin-bottom:10px;')
html = html.replace('padding:10px;', 'padding:8px;')

# Reduce gap in inputs
html = html.replace('flex-direction:column; gap:12px;', 'flex-direction:column; gap:8px;')
html = html.replace('padding:10px 12px;', 'padding:8px 10px;')

# Reduce margin top of submit button
html = html.replace('margin-top:18px;', 'margin-top:12px;')
html = html.replace('padding:13px;', 'padding:10px;')

with open('frontend/stock.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("Updated right sidebar padding")

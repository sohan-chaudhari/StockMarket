import re
import os

with open('frontend/stock.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Update body tag styling
body_regex = re.compile(r'<body style="background-color: #0A0A0B;">')
html = body_regex.sub('<body style="background-color: #0A0A0B; display: flex; flex-direction: column; height: 100vh; overflow: hidden; margin: 0;">', html)

# 2. Re-arrange top-level layout
# Current: <body><div nav-root><div main-terminal-area> [ drawing-toolbar, container[ sticky-header, chart-container ] ]
# We need to extract sticky-header, openPositionsPanel, and tradeModal.

# Extract sticky-header
sticky_header_regex = re.compile(r'(<div class="sticky-header-container".*?</div> <!-- End Sticky Header -->)', re.DOTALL)
sticky_match = sticky_header_regex.search(html)
sticky_html = sticky_match.group(1) if sticky_match else ''
html = sticky_header_regex.sub('', html)

# Insert sticky-header right after nav-root
html = html.replace('<div id="nav-root"></div>', f'<div id="nav-root"></div>\n  {sticky_html}')

# Extract openPositionsPanel
positions_regex = re.compile(r'(<!-- Open Positions Section.*?<div id="openPositionsPanel" style="display:none; width:100%; padding:0 0 24px 0;">.*?</div>\n  </div>)', re.DOTALL)
pos_match = positions_regex.search(html)
pos_html = pos_match.group(1) if pos_match else ''
html = positions_regex.sub('', html)

# Modify pos_html to be a bottom pane
if pos_html:
    pos_html = pos_html.replace('padding:0 0 24px 0;', 'height: 250px; flex-shrink: 0; overflow-y: auto; background: #0A0A0B;')
    pos_html = pos_html.replace('border-radius:14px; padding:20px 24px;', 'padding:10px 15px;')
    pos_html = pos_html.replace('border:1px solid rgba(255,255,255,0.07);', 'border-top:1px solid #2a2e39;')

# Extract tradeModal
trade_modal_regex = re.compile(r'(<!-- Trade Modal.*?<div id="tradeModal" class="modal">.*?</div>\n  </div>)', re.DOTALL)
trade_match = trade_modal_regex.search(html)
trade_html = trade_match.group(1) if trade_match else ''
html = trade_modal_regex.sub('', html)

# Modify trade_html to be a sidebar
if trade_html:
    # Remove modal class
    trade_html = trade_html.replace('class="modal"', 'style="flex: 1; display: flex; flex-direction: column;"')
    trade_html = trade_html.replace('class="modal-content"', 'class="sidebar-content"')
    # Remove max-width constraints on inner content
    trade_html = re.sub(r'style="min-width:380px; max-width:460px; text-align:left; padding:28px 32px;"', 'style="padding: 20px; flex: 1; overflow-y: auto;"', trade_html)

# Wrap chart-container and openPositionsPanel in center-area
chart_regex = re.compile(r'(<!-- Chart -->.*?</div>\n    </div>)', re.DOTALL)
chart_match = chart_regex.search(html)
if chart_match:
    chart_html = chart_match.group(1)
    html = chart_regex.sub('', html)
    
    # We will build center-area
    center_area = f"""
    <!-- CENTER AREA -->
    <div class="center-area" style="flex: 1; display: flex; flex-direction: column; min-width: 0; position: relative;">
      {chart_html}
      {pos_html}
    </div>
    """
    
    # We will build right-sidebar
    right_sidebar = f"""
    <!-- RIGHT SIDEBAR -->
    <div class="right-sidebar" style="width: 320px; flex-shrink: 0; border-left: 1px solid #2a2e39; background: #0A0A0B; display: flex; flex-direction: column; z-index: 5;">
      {trade_html}
    </div>
    """
    
    # Inject center and right sidebar back into the container
    # Currently we have <div class="container"> inside <div class="main-terminal-area">
    # We will replace <div class="container"> entirely, it's unnecessary now.
    # Wait, the drawing-toolbar is inside main-terminal-area.
    
    # Let's find main-terminal-area and its contents
    # The container div opens right after drawing-toolbar.
    container_regex = re.compile(r'<div class="container">\s*(</div>)?')
    html = container_regex.sub('', html)
    
    # Let's find drawing-toolbar and append center and right to it.
    toolbar_end_regex = re.compile(r'(<div class="drawing-toolbar".*?</div>\n  </div>)', re.DOTALL)
    # Actually, drawing-toolbar ends with </div>, then there was container.
    # We can just look for the end of drawing toolbar.
    
    # A safer way: we know main-terminal-area starts with drawing-toolbar.
    # Let's replace the drawing-toolbar with toolbar + center + right.
    toolbar_regex = re.compile(r'(<div class="drawing-toolbar".*?</div>\n    </div>\n  </div>)', re.DOTALL)
    toolbar_match = toolbar_regex.search(html)
    if toolbar_match:
        toolbar_full = toolbar_match.group(1)
        # Update drawing-toolbar styles to not overlap
        toolbar_new = toolbar_full.replace('<div class="drawing-toolbar">', '<div class="drawing-toolbar" style="position: relative !important; top: 0; left: 0; height: 100%; z-index: 10;">')
        
        replacement = f"""
        {toolbar_new}
        {center_area}
        {right_sidebar}
        """
        html = toolbar_regex.sub(replacement.replace('\\', '\\\\'), html)
    else:
        print("Toolbar not found!")

# Let's fix chart-container height inside center-area
# Make sure chart-container flexes
html = html.replace('<div id="chart-container" style="position: relative;">', '<div id="chart-container" style="flex: 1; position: relative; min-height: 0;">')

# Ensure drawing canvas doesn't break flex
html = html.replace('<div id="drawing-canvas-container">', '<div id="drawing-canvas-container" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; pointer-events: none;">')

with open('frontend/stock_restructured.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("Saved to stock_restructured.html")

import re

with open('frontend/stock.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. We need to extract the ticker name and price from header-identity
# In Quantum Trader, the top-left of the chart says "AAPL 15m | D | O: ... H: ... L: ... C: ..."
# We will inject the ticker name and price into ohlc-floating-legend.

# In stock.html, we have:
# <h1 id="ticker-name">--</h1>
# <span id="ticker-badge" class="stock-badge">--</span>
# <div id="ticker-symbol-small">--</div>
# <span id="header-price">--</span>
# <span id="header-change">--</span>
# We'll just update stock-logic.js to populate these in the floating legend too, or we can just move these exact span IDs into the floating legend!

ohlc_legend_regex = re.compile(r'(<div id="ohlc-floating-legend".*?>)')
match = ohlc_legend_regex.search(html)

if match:
    ohlc_start = match.group(1)
    # We will inject the ticker name and price spans right inside the legend!
    # We'll style it to look exactly like QT: AAPL 15m | O: ...
    ticker_html = """
        <span id="chart-ticker-name" style="font-weight: 700; color: #fff; font-size: 14px;">--</span>
        <span id="chart-ticker-price" style="font-weight: 700; color: #4CAF50; font-size: 14px; margin-left: 8px;">--</span>
        <span style="color: #555; margin: 0 8px;">|</span>
    """
    html = html.replace(ohlc_start, ohlc_start + '\n' + ticker_html)
    
# 2. We need to completely hide or gut the sticky-header-container!
# Wait, sticky-header-container has the `header-controls` (Back button, timeframes, indicators).
# Let's just make sticky-header-container extremely slim: 40px tall, no margins, no padding, border-bottom 1px solid #2a2e39.
# Let's add extremely aggressive CSS overrides to the <head>.

css_overrides = """
    /* EXACT QUANTUM TRADER STYLING */
    .sticky-header-container {
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        height: 40px !important;
        padding: 0 15px !important;
        background: #0A0A0B !important;
        border-radius: 0 !important;
        margin: 0 !important;
        border-bottom: 1px solid #2a2e39 !important;
    }
    .header-controls {
        display: flex !important;
        align-items: center !important;
        width: 100% !important;
        margin: 0 !important;
    }
    .header-identity {
        display: none !important; /* Hide the bulky old header entirely! */
    }
    .main-terminal-area {
        background: #0A0A0B !important;
    }
    .drawing-toolbar {
        background: #0A0A0B !important;
        border-right: 1px solid #2a2e39 !important;
        padding-top: 10px !important;
    }
    #chart-container {
        border-radius: 0 !important;
        margin: 0 !important;
        border: none !important;
    }
    .container {
        padding: 0 !important;
        margin: 0 !important;
    }
    /* Move News Sentiment to the right sidebar */
    #newsSentimentSidebarBtn {
        width: 100%;
        margin-bottom: 15px;
        padding: 10px;
        background: #1a1a24;
        border: 1px solid #2a2e39;
        border-radius: 6px;
        color: #d1d4dc;
        cursor: pointer;
    }
    #newsSentimentSidebarBtn:hover { background: #2a2e39; }
"""

# Inject CSS overrides right before </style>
html = html.replace('</style>', css_overrides + '\n</style>')

# 3. Add News Sentiment button to the Right Sidebar, above the Trade Modal
right_sidebar_regex = re.compile(r'(<div class="right-sidebar".*?>)')
match2 = right_sidebar_regex.search(html)
if match2:
    rs_start = match2.group(1)
    news_btn = '<button id="newsSentimentSidebarBtn" onclick="toggleNewsModal()">📰 News Sentiment</button>\n'
    html = html.replace(rs_start, rs_start + '\n      <div style="padding: 15px 15px 0 15px;">' + news_btn + '</div>')

with open('frontend/stock.html', 'w', encoding='utf-8') as f:
    f.write(html)
print("Updated stock.html styles!")

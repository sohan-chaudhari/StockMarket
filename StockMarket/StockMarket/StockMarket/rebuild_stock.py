import re

# Read index_chart.html
with open('frontend/index_chart.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Replace <title>
html = html.replace('<title>Index Chart - Live</title>', '<title>Stock Analysis - Live</title>')

# Find the end of the main-container (which contains drawing-toolbar and chart-wrapper)
# In index_chart.html, it ends around line 592 with two </div>'s before the script tags.
# We will split at the first script tag.
parts = html.split('<script src="stocks.js?v=19"></script>')

bottom_panel = """
  <div class="bottom-panel">
    <!-- Trade Panel -->
    <div class="trade-panel">
      <div class="panel-header">
        <h2>Trade</h2>
        <div class="available-balance">Balance: <span id="tradeAvailableBalance">--</span></div>
      </div>
      <div class="trade-form">
        <div class="input-group">
          <label>Entry Price</label>
          <input type="number" id="tradeEntryPrice" step="0.05">
        </div>
        <div class="input-group">
          <label>Quantity</label>
          <input type="number" id="tradeQuantity" value="1">
        </div>
        <div class="input-group">
          <label>Take Profit (Opt)</label>
          <input type="number" id="tradeTakeProfit" step="0.05">
        </div>
        <div class="input-group">
          <label>Stop Loss (Opt)</label>
          <input type="number" id="tradeStopLoss" step="0.05">
        </div>
        <div class="trade-summary">
          <span>Total: <span id="tradeTotalInvestment">--</span></span>
        </div>
        <div class="trade-actions">
          <button class="btn-buy" id="btnLong" onclick="window.submitTrade('LONG')">Buy (Long)</button>
          <button class="btn-sell" id="btnShort" onclick="window.submitTrade('SHORT')">Sell (Short)</button>
        </div>
        <div id="tradeMessage" class="trade-message"></div>
      </div>
    </div>

    <!-- Positions Panel -->
    <div class="positions-panel">
      <div class="panel-header">
        <h2>Open Positions</h2>
      </div>
      <div class="positions-table-wrapper">
        <table class="positions-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Qty</th>
              <th>Entry</th>
              <th>CMP</th>
              <th>P&L</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="openPositionsContainer">
            <tr><td colspan="6" style="text-align:center;">No open positions for this stock</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Modals -->
  <div id="tradeModal" class="modal">
    <div class="modal-content">
      <h2>Place Order: <span id="tradeModalTicker"></span></h2>
      <button onclick="document.getElementById('tradeModal').classList.remove('visible')">Close</button>
    </div>
  </div>

  <div id="editPositionModal" class="modal">
    <div class="modal-content">
      <h2>Edit Limits: <span id="editPositionTicker"></span></h2>
      <p>Type: <span id="editPositionType"></span> | Qty: <span id="editPositionCurrentQty"></span></p>
      <div class="input-group">
         <label>New Take Profit</label>
         <input type="number" id="editTakeProfit" step="0.05">
      </div>
      <div class="input-group">
         <label>New Stop Loss</label>
         <input type="number" id="editStopLoss" step="0.05">
      </div>
      <button id="editSubmitBtn">Update</button>
      <button onclick="document.getElementById('editPositionModal').classList.remove('visible')">Cancel</button>
      <div id="editMessage"></div>
    </div>
  </div>

  <div id="deleteModal" class="modal">
    <div class="modal-content">
      <h2>Close Position</h2>
      <p id="deleteMessage"></p>
      <button id="deleteConfirmBtn" class="btn-sell">Confirm Close</button>
      <button onclick="document.getElementById('deleteModal').classList.remove('visible')">Cancel</button>
    </div>
  </div>
  
  <div id="logoutModal" class="modal">
     <div class="modal-content">
       <h2>Logout</h2>
       <p>Are you sure you want to logout?</p>
       <button class="btn-sell" onclick="window.handleLogout()">Logout</button>
       <button onclick="document.getElementById('logoutModal').classList.remove('visible')">Cancel</button>
     </div>
  </div>
"""

# Now strip out the inline scripts from the second part (we will rely on our external JS files)
second_part = parts[1] if len(parts) > 1 else ""
# Remove everything after the first <script> in the second part if any, or just append the correct script tags.

final_html = parts[0] + bottom_panel + "\n" + '<script src="stocks.js?v=20"></script>\n<script src="loader.js?v=2"></script>\n<script src="nav.js?v=4"></script>\n<script src="news.js"></script>\n<script src="cache.js?v=1"></script>\n<script src="dashboard.js?v=1001"></script>\n<script src="drawings.js?v=8"></script>\n<script src="stock-logic.js?v=1"></script>\n<script src="stock-ui.js?v=1"></script>\n' + "</body></html>"

with open('frontend/stock.html', 'w', encoding='utf-8') as f:
    f.write(final_html)

print("Rebuilt stock.html successfully!")

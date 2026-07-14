import re
import os

# 1. Get Top Part
with open('dashboard.js', 'r', encoding='utf-8') as f:
    current = f.read()

top_part = current[:current.find('var bigChart = null;')]

# 2. Get fetchMarketSentiment
match = re.search(r'async function fetchMarketSentiment\(\) \{.*\}', current, re.DOTALL)
if match:
    sentiment_part = match.group(0)
else:
    # Manual extraction since it might be split or mangled
    sentiment_part = '''async function fetchMarketSentiment() {
  try {
    var res = await fetch("/api/scanx/news/market-sentiment");
    if (res.ok) {
      var data = await res.json();
      var score = data.score;
      var label = data.label || "neutral";
      var summary = data.summary || "Market data is being processed.";

      var sentimentEl = document.getElementById("market-sentiment-container");
      if (sentimentEl) {
        sentimentEl.innerHTML = 
          '<div class="sentiment-box" style="margin-top: 15px; padding: 15px; background: #1a1e29; border-radius: 8px; border: 1px solid #2a2e39;">' +
            '<div class="sentiment-header" style="font-size: 14px; font-weight: 600; color: #d1d4dc; margin-bottom: 10px;">MARKET SENTIMENT</div>' +
            '<div class="sentiment-body" style="display: flex; align-items: center;">' +
              '<svg class="sentiment-gauge" viewBox="0 0 100 50" style="width: 120px; height: 60px; overflow: visible;">' +
                '<path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="#ef5350" stroke-width="8" stroke-dasharray="125" stroke-dashoffset="0"/>' +
                '<path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="#26a69a" stroke-width="8" stroke-dasharray="125" stroke-dashoffset="62.5"/>' +
                '<polygon id="gaugeNeedle" points="47,50 53,50 50,15" fill="#d1d4dc" style="transform-origin: 50px 50px; transition: transform 1s ease-out; transform: rotate(-90deg);"/>' +
                '<circle cx="50" cy="50" r="5" fill="#d1d4dc"/>' +
              '</svg>' +
              '<div class="sentiment-text" style="margin-left: 20px;">' +
                '<div class="sentiment-score-text" id="sentimentScore" style="font-size: 24px; font-weight: bold; color: #fff;">--</div>' +
                '<div class="sentiment-label-text" id="sentimentLabel" style="font-size: 16px; text-transform: uppercase;">Loading...</div>' +
                '<div class="sentiment-summary" id="sentimentSummary" style="font-size: 12px; color: #8a8a8a; margin-top: 5px;">Analyzing latest market news...</div>' +
              '</div>' +
            '</div>' +
          '</div>';
        
        var sLabel = document.getElementById("sentimentLabel");
        var sScore = document.getElementById("sentimentScore");
        var sSum = document.getElementById("sentimentSummary");
        var needle = document.getElementById("gaugeNeedle");
        if (sLabel) {
          sLabel.innerText = label;
          sLabel.style.color = (label.toLowerCase() === 'bullish') ? '#26a69a' : (label.toLowerCase() === 'bearish' ? '#ef5350' : '#d1d4dc');
        }
        if (sScore) sScore.innerText = score != null ? score : 50;
        if (sSum) sSum.innerText = summary;
        if (needle) {
          var s = score != null ? score : 50;
          needle.style.transform = "rotate(" + (-90 + (s / 100) * 180) + "deg)";
        }
      }
    }
  } catch(e) {
    console.error("Failed to fetch sentiment", e);
  }
}
'''

# 3. Get Middle Part from recovered_lines.txt
recovered_dict = {}
with open(r'c:\Users\sohan\Desktop\StockMarket\recovered_lines.txt', 'r', encoding='utf-8') as f:
    for line in f:
        m = re.match(r'^(\d+):\s(.*)', line)
        if m:
            recovered_dict[int(m.group(1))] = m.group(2)

crosshair_logic = '''    color: '#26a69a',
    priceFormat: { type: 'volume' },
    priceScaleId: '',
    scaleMargins: { top: 0.8, bottom: 0 }
  });
  
  var toolTip = document.createElement('div');
  toolTip.className = 'floating-tooltip';
  toolTip.style.position = 'absolute';
  toolTip.style.display = 'none';
  toolTip.style.padding = '8px';
  toolTip.style.boxSizing = 'border-box';
  toolTip.style.fontSize = '12px';
  toolTip.style.color = '#fff';
  toolTip.style.backgroundColor = 'rgba(19, 23, 34, 0.9)';
  toolTip.style.border = '1px solid #2a2e39';
  toolTip.style.borderRadius = '4px';
  toolTip.style.zIndex = '1000';
  toolTip.style.pointerEvents = 'none';
  
  document.getElementById('chart-container').appendChild(toolTip);

  bigChart.subscribeCrosshairMove(function(param) {
    if (!param.time || param.point.x < 0 || param.point.y < 0) {
      toolTip.style.display = 'none';
      return;
    }
    var price = param.seriesData.get(bigCandleSeries);
    if (!price) {
      toolTip.style.display = 'none';
      return;
    }
    var vol = typeof bigVolumeSeries !== 'undefined' ? param.seriesData.get(bigVolumeSeries) : null;
    var dateStr;
    if (typeof param.time === 'string') dateStr = param.time;
    else {
      var d = new Date(param.time * 1000);
      dateStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    }
    
    toolTip.style.display = 'block';
    toolTip.style.left = param.point.x + 'px';
    toolTip.style.top = param.point.y + 'px';
    toolTip.innerHTML = '<div style=\"color: #2962FF\">O: ' + price.open.toFixed(2) + '</div>' +
                        '<div style=\"color: #26a69a\">H: ' + price.high.toFixed(2) + '</div>' +
                        '<div style=\"color: #ef5350\">L: ' + price.low.toFixed(2) + '</div>' +
                        '<div style=\"color: #2962FF\">C: ' + price.close.toFixed(2) + '</div>' +
                        (vol ? '<div style=\"color: #d1d4dc\">V: ' + vol.value + '</div>' : '') +
                        '<div style=\"color: #8a8a8a; font-size:10px;\">' + dateStr + '</div>';
  });
'''

# We build the middle part manually by stitching lines together.
# But instead of using the broken recovered lines (which has duplicates), let's use the cleanly extracted logic.
# Wait, I just need `initBigChart` up to `hideLoadingEl();`.
middle_lines = []
middle_lines.append('var bigChart = null;\n')
middle_lines.append('var activeRange = \'ALL\';\n')

# From recovered_lines.txt, we take exactly lines 1034 to 1437. This is BEFORE it got corrupted!
for i in range(1034, 1438):
    if i in recovered_dict:
        val = recovered_dict[i]
        if val == '[MISSING]':
            if i == 1101:
                middle_lines.append(crosshair_logic + '\n')
        else:
            middle_lines.append(val + '\n')

# 4. Get insert.js
with open('insert.js', 'r', encoding='utf-8') as f:
    insert_part = f.read()

# 5. Take lines 1630 to 1711 from recovered_lines.txt (the end of processBigChartPrice and calculateSMA etc)
end_middle_lines = []
for i in range(1629, 1712):
    if i in recovered_dict:
        val = recovered_dict[i]
        if val != '[MISSING]':
            end_middle_lines.append(val + '\n')

with open('dashboard.js', 'w', encoding='utf-8') as f:
    f.write(top_part)
    f.writelines(middle_lines)
    f.write(insert_part + '\n')
    f.writelines(end_middle_lines)
    f.write(sentiment_part + '\n')

print('Pristine rebuild complete!')

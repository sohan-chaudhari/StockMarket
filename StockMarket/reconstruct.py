import re

# Read current dashboard.js
with open('dashboard.js', 'r', encoding='utf-8') as f:
    dash_lines = f.readlines()

top_half = dash_lines[:949] # up to right before fetchMarketSentiment ends
bottom_half = dash_lines[949:] # fetchMarketSentiment and EOF

# Read recovered lines
recovered_dict = {}
with open(r'c:\Users\sohan\Desktop\StockMarket\recovered_lines.txt', 'r', encoding='utf-8') as f:
    for line in f:
        match = re.match(r'^(\d+):\s(.*)', line)
        if match:
            num = int(match.group(1))
            val = match.group(2)
            recovered_dict[num] = val

middle_lines = []

# Missing globals
middle_lines.append('var bigChart = null;\n')
middle_lines.append('var activeRange = \'ALL\';\n')

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
    toolTip.innerHTML = '<div style="color: #2962FF">O: ' + price.open.toFixed(2) + '</div>' +
                        '<div style="color: #26a69a">H: ' + price.high.toFixed(2) + '</div>' +
                        '<div style="color: #ef5350">L: ' + price.low.toFixed(2) + '</div>' +
                        '<div style="color: #2962FF">C: ' + price.close.toFixed(2) + '</div>' +
                        (vol ? '<div style="color: #d1d4dc">V: ' + vol.value + '</div>' : '') +
                        '<div style="color: #8a8a8a; font-size:10px;">' + dateStr + '</div>';
  });
'''

# We process lines 1034 to 1711
for i in range(1034, 1712):
    if i in recovered_dict:
        val = recovered_dict[i]
        if val == '[MISSING]':
            if i == 1101:
                middle_lines.append(crosshair_logic + '\n')
            # ignore other [MISSING]
        else:
            middle_lines.append(val + '\n')

with open('dashboard.js', 'w', encoding='utf-8') as f:
    f.writelines(top_half)
    f.writelines(middle_lines)
    f.writelines(bottom_half)

print('Reconstruction complete!')

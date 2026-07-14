"""
Script to move sentiment badge from footer to header in news cards
"""

with open('frontend(HS)/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the section where sentiment is currently in footer and remove it
# Then add it to the header section

# Step 1: Remove sentiment from footer section
footer_sentiment = '''            
            <!-- Footer -->
            <div class="news-footer">
                ${hasSentiment ? `
                <div class="sentiment-info">
                    ${createSentimentBadge(sentimentLabel, sentimentScore, confidence)}
                </div>
                ` : ''}
                
                ${hasRecency ? createRecencyScore(recencyScore) : ''}
            </div>'''

# Replace with empty footer
content = content.replace(footer_sentiment, '')

# Step 2: Add sentiment to header (after live badge line)
old_header_end = '''                    ${isRecent ? '<span class="live-badge">⚡ Live</span>' : ''}
                </div>'''

new_header_end = '''                    ${isRecent ? '<span class="live-badge">⚡ Live</span>' : ''}
                
                    <!-- Sentiment Badge on Right -->
                    ${hasSentiment ? `
                    <div class="sentiment-info">
                        ${createSentimentBadge(sentimentLabel, sentimentScore, confidence)}
                    </div>
                    ` : ''}
                </div>'''

content = content.replace(old_header_end, new_header_end)

with open('frontend(HS)/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print('✓ Moved sentiment badge to right side of header')
print('✓ Removed sentiment from footer')
print('Please refresh your browser with Ctrl+Shift+R')

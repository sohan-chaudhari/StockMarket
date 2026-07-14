with open('frontend(HS)/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the news card structure
# Remove sentiment from footer and add to header

old_structure = '''                </div>
                ${isRecent ? '<span class="live-badge">⚡ Live</span>' : ''}
            </div>
            
            <!-- Title -->
            <h3 class="news-title">${cleanTitle}</h3>
            
            <!-- Excerpt (only show if different from title) -->
            ${cleanExcerpt ? `<p class="news-excerpt">${cleanExcerpt}</p>` : ''}
            
            <!-- Footer -->
            <div class="news-footer">
                ${hasSentiment ? `
                <div class="sentiment-info">
                    ${createSentimentBadge(sentimentLabel, sentimentScore, confidence)}
                </div>
                ` : ''}
                
                ${hasRecency ? createRecencyScore(recencyScore) : ''}
            </div>'''

new_structure = '''                </div>
                ${isRecent ? '<span class="live-badge">⚡ Live</span>' : ''}
            
                <!-- Sentiment Badge on Right -->
                ${hasSentiment ? `
                <div class="sentiment-info">
                    ${createSentimentBadge(sentimentLabel, sentimentScore, confidence)}
                </div>
                ` : ''}
            </div>
            
            <!-- Title -->
            <h3 class="news-title">${cleanTitle}</h3>
            
            <!--Excerpt (only show if different from title) -->
            ${cleanExcerpt ? `<p class="news-excerpt">${cleanExcerpt}</p>` : ''}'''

content = content.replace(old_structure, new_structure)

with open('frontend(HS)/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print('Moved sentiment badge to right side of news card')

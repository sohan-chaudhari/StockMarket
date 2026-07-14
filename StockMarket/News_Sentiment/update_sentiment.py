import re

with open('backend/app/api/scanx_news.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find where to insert sentiment code (around line 147-163)
# Look for the specific pattern
in_target_section = False
new_lines = []
i = 0

while i < len(lines):
    line = lines[i]
    
    # Check if we're at the start of second endpoint's response building
    if 'Convert to response format' in line and i > 100:  # Make sure it's second occurrence
        # Replace this section with sentiment-enabled version
        new_lines.append("        # Convert to response format and add sentiment analysis\n")
        i += 1  # Skip original comment
        new_lines.append(lines[i])  # response = []
        i += 1
        new_lines.append(lines[i])  # for idx, article...
        i += 1
        
        # Add sentiment extraction code
        new_lines.append("            headline = article.get('headline', '')\n")
        new_lines.append("            ticker_symbol = article.get('ticker', ticker.upper())\n")
        new_lines.append("            source = article.get('source', 'Google News')\n")
        new_lines.append("            \n")
        new_lines.append("            # Get sentiment for headline\n")
        new_lines.append("            sentiment = get_sentiment(headline, ticker_symbol, source)\n")
        new_lines.append("            \n")
        
        # Continue with the rest but modify to use variables
        while i < len(lines) and 'response.append' not in lines[i]:
            if 'published_at' in lines[i]:
                new_lines.append(lines[i])
            i += 1
        
        # Add modified response.append
        new_lines.append("            \n")
        new_lines.append("            response.append({\n")
        new_lines.append(f"                'id': f\"full_{{ticker}}_{{idx}}\",\n")
        new_lines.append("                'ticker': ticker_symbol,\n")
        new_lines.append("                'title': headline,\n")
        new_lines.append("                'url': article.get('url', ''),\n")
        new_lines.append("                'source': source,\n")
        new_lines.append("                'published_at': published_at,\n")
        new_lines.append("                'excerpt': '',  # Not available from Playwright scraper\n")
        new_lines.append("                'company_name': company_name,\n")
        new_lines.append("                'logo_url': article.get('logo_url', ''),\n")
        new_lines.append("                'sentiment': sentiment  # Add sentiment data\n")
        new_lines.append("            })\n")
        
        # Skip original response.append block
        while i < len(lines) and '})' not in lines[i]:
            i += 1
        i += 1  # Skip the closing })
    else:
        new_lines.append(line)
        i += 1

with open('backend/app/api/scanx_news.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('Added sentiment analysis to ticker-specific endpoint')

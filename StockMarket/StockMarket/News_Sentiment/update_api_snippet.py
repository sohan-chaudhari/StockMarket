with open('backend/app/api/scanx_news.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    
    # After getting source, add snippet extraction
    if "source = article.get('source', 'scanx.trade')" in line:
        next_line = lines[i+1] if i+1 < len(lines) else ""
        if "snippet" not in next_line:
            new_lines.append("            snippet = article.get('snippet', '')  # Get snippet from scraper\n")
    
    # Update get_sentiment call to include snippet
    if "sentiment = get_sentiment(headline, ticker, source)" in line:
        new_lines[-1] = "            sentiment = get_sentiment(headline, ticker, source, snippet)\n"
    
    # Update excerpt field to use snippet
    if "'excerpt': ''," in line and "Not available" in lines[i]:
        new_lines[-1] = "                'excerpt': snippet,  # Include snippet in response\n"

with open('backend/app/api/scanx_news.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('Updated API endpoints to use snippet')

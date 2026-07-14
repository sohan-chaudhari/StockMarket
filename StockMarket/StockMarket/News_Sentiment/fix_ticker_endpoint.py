with open('backend/app/api/scanx_news.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and update the ticker-specific endpoint (around line 150-156)
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    new_lines.append(line)
    
    # After "source = article.get..." in ticker endpoint, add snippet
    if i >= 145 and "source = article.get('source', 'Google News')" in line:
        next_line_idx = i + 1
        if next_line_idx < len(lines) and "snippet" not in lines[next_line_idx]:
            new_lines.append("            snippet = article.get('snippet', '')  # Extract snippet\n")
            new_lines.append("            \n")
    
    # Update get_sentiment call in ticker endpoint
    if i >= 145 and "sentiment = get_sentiment(headline, ticker_symbol, source)" in line:
        new_lines[-1] = "            sentiment = get_sentiment(headline, ticker_symbol, source, snippet)\n"
    
    # Update excerpt in ticker endpoint  
    if i >= 160 and "'excerpt': ''," in line and i+1 < len(lines) and "company" in lines[i+1]:
        new_lines[-1] = "                'excerpt': snippet,  # Include snippet\n"
    
    i += 1

with open('backend/app/api/scanx_news.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('Updated ticker endpoint to use snippet')

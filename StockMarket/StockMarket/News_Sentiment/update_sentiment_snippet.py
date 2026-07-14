with open('backend/app/utils/sentiment_helper.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the get_sentiment function
old_function = '''def get_sentiment(headline: str, ticker: str = None, source: str = None) -> Dict:
    """
    Convenience function to get sentiment for a headline
    """
    return sentiment_helper.analyze_article(headline, ticker, source)'''

new_function = '''def get_sentiment(headline: str, ticker: str = None, source: str = None, snippet: str = "") -> Dict:
    """
    Convenience function to get sentiment for headline + snippet
    
    Args:
        headline: Article headline
        ticker: Stock ticker (optional)
        source: News source (optional) 
        snippet: Article overview/description (optional)
    
    Returns:
        dict with sentiment_score, label, confidence
    """
    # Combine headline and snippet for better context
    text_to_analyze = headline
    if snippet and snippet.strip():
        # Add snippet after headline (limit to 200 chars)
        text_to_analyze += ". " + snippet[:200]
    
    return sentiment_helper.analyze_article(text_to_analyze, ticker, source)'''

content = content.replace(old_function, new_function)

with open('backend/app/utils/sentiment_helper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Updated get_sentiment to use headline + snippet')

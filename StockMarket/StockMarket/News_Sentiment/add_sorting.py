with open('frontend(HS)/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the renderNews function
old_function = '''function renderNews(articles) {
    const html = articles.map(article => createNewsCard(article)).join('');
    newsFeed.innerHTML = html;
    newsFeed.style.display = 'flex';
    emptyState.style.display = 'none';
}'''

new_function = '''function renderNews(articles) {
    // Sort by published_at (newest first)
    const sortedArticles = [...articles].sort((a, b) => {
        return new Date(b.published_at) - new Date(a.published_at);
    });
    
    const html = sortedArticles.map(article => createNewsCard(article)).join('');
    newsFeed.innerHTML = html;
    newsFeed.style.display = 'flex';
    emptyState.style.display = 'none';
}'''

content = content.replace(old_function, new_function)

with open('frontend(HS)/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print('Added sorting to renderNews function')

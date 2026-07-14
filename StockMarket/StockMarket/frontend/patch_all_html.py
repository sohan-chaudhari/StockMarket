import os
import re

for filename in os.listdir('.'):
    if not filename.endswith('.html'):
        continue

    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Remove Cache-Control, Pragma, Expires meta tags
    content = re.sub(r'<meta http-equiv="Cache-Control"[^>]*>\n?', '', content)
    content = re.sub(r'<meta http-equiv="Pragma"[^>]*>\n?', '', content)
    content = re.sub(r'<meta http-equiv="Expires"[^>]*>\n?', '', content)

    # 2. Add page-cache.js before </head> or at top of body, if not present
    if 'page-cache.js' not in content:
        if '</head>' in content:
            content = content.replace('</head>', '    <script src="page-cache.js"></script>\n</head>')
        else:
            # Fallback for weird files
            content = '<script src="page-cache.js"></script>\n' + content
            
    # 3. Remove hardcoded leverage-loader div
    loader_pattern = re.compile(r'<div class="leverage-loader-overlay" id="leverage-loader">.*?</div></div></div>', re.DOTALL)
    content = loader_pattern.sub('', content)
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

print("Patch applied to all HTML files.")

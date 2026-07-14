import os
import glob
import re

frontend_dir = r"c:\Users\sohan\Desktop\StockMarket\StockMarket\StockMarket\frontend"
html_files = glob.glob(os.path.join(frontend_dir, "*.html"))

for file in html_files:
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if 'href="H0"' in content:
        # Some files might have 4, some have 3.
        # The correct order is usually: index.css, nav.css, loader.css, styles.css
        css_files = [
            'href="index.css?v=66"',
            'href="nav.css?v=4"',
            'href="loader.css?v=4"',
            'href="styles.css?v=2"'
        ]
        
        # We replace one by one
        for css in css_files:
            if 'href="H0"' in content:
                content = content.replace('href="H0"', css, 1)
        
        # If any H0 are left over, just replace with styles.css
        content = content.replace('href="H0"', 'href="styles.css?v=2"')
        
        with open(file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed {os.path.basename(file)}")

print("All done!")

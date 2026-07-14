with open('frontend(HS)/app.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the duplicate showLoading function (the second one, after line 50)
start_line = None
end_line = None

for i, line in enumerate(lines):
    if 'function showLoading()' in line and i > 50:
        start_line = i
    elif start_line is not None and 'function showEmptyState()' in line:
        end_line = i
        break

if start_line is not None and end_line is not None:
    # Remove lines from start_line to end_line (exclusive of end_line)
    new_lines = lines[:start_line] + lines[end_line:]
    
    with open('frontend(HS)/app.js', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print(f'✓ Removed old showLoading function (lines {start_line+1}-{end_line})')
else:
    print('✗ Could not find duplicate function')

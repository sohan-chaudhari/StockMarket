import re

with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix ValidationService lazy load in validation endpoint
def fix_validation(m):
    return m.group(1) + '\n    global _validation_service\n    if _validation_service is None:\n        init_recovery_service()\n' + m.group(2)

content = re.sub(r'(@app\.get\(\"/api/validation/run\"\)\ndef run_validation[^\:]+\:\n(?:\s+\"\"\"[^\"]+\"\"\"\n)?)(    from models)', fix_validation, content)

# Fix NameError live in endpoints
content = content.replace('if before is None:\n        live = candle_aggregator', 'live = None\n    if before is None:\n        live = candle_aggregator')

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed main.py endpoints')

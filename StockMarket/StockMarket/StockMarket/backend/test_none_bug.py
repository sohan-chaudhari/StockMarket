cache_entry = {'data': None, 'ts': 123}
idx_data = cache_entry.get('data', {})
print("idx_data:", type(idx_data))
try:
    idx_current = idx_data.get('current', 0)
except Exception as e:
    print("ERROR:", e)

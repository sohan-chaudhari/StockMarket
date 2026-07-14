from datetime import date

feb2 = date(2026, 2, 2)
print(f"Feb 2, 2026 is {feb2.strftime('%A')} (weekday={feb2.weekday()})")
print(f"0=Monday, 6=Sunday")
print(f"\nSo Feb 2, 2026 is a {'WEEKEND' if feb2.weekday() >= 5 else 'WEEKDAY'}")

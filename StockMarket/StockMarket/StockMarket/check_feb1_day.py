from datetime import date

feb1 = date(2026, 2, 1)
print(f"Feb 1, 2026 was a {feb1.strftime('%A')}")
print(f"Weekday number: {feb1.weekday()} (0=Monday, 6=Sunday)")

if feb1.weekday() >= 5:
    print("\n✗ Feb 1st was a WEEKEND - no trading!")
else:
    print("\n✓ Feb 1st was a WEEKDAY - should have trading")

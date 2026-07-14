const now = new Date();
console.log('Current Date:', now);
console.log('Date String:', now.toDateString());
console.log('Day of Week:', now.getDay(), '(0=Sun, 6=Sat)');
console.log('Hours:', now.getHours());
console.log('Minutes:', now.getMinutes());

// Test isMarketOpen logic
const day = now.getDay();
const h = now.getHours();
const m = now.getMinutes();
const isWeekday = day >= 1 && day <= 5;
const dateStr = now.toDateString();
const isSpecialDay = dateStr.includes('Feb 01 2026');
const isTradingDay = isWeekday || isSpecialDay;
const timeCheck = ((h > 9 || (h === 9 && m >= 15)) && (h < 15 || (h === 15 && m < 32)));
const result = isTradingDay && timeCheck;

console.log('\nMarket Check:');
console.log('  isWeekday:', isWeekday);
console.log('  isSpecialDay:', isSpecialDay);
console.log('  isTradingDay:', isTradingDay);
console.log('  timeCheck:', timeCheck);
console.log('  FINAL isMarketOpen:', result);

const now = new Date('2026-02-01T09:30:00');
console.log('Date String:', now.toDateString());
console.log('Includes Feb 01 2026:', now.toDateString().includes('Feb 01 2026'));
console.log('Includes Sat Feb 01 2026:', now.toDateString().includes('Sat Feb 01 2026'));

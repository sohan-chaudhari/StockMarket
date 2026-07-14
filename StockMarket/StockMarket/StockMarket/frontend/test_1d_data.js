// Native fetch in Node 18+

const API_BASE_URL = 'http://localhost:8003/api';
const ticker = 'INFY.NS';
const range = '1D';

async function testFetch() {
    try {
        console.log(`Fetching ${range} data for ${ticker}...`);
        const res = await fetch(`${API_BASE_URL}/stock-data/range?ticker=${encodeURIComponent(ticker)}&range=${range}`);

        if (!res.ok) {
            console.error(`Fetch failed: ${res.status} ${res.statusText}`);
            return;
        }

        const data = await res.json();
        console.log(`Data Points: ${data.length}`);

        if (data.length > 0) {
            console.log('First Point:', data[0]);
            console.log('Last Point:', data[data.length - 1]);
        } else {
            console.log("Returned empty array.");
        }

    } catch (e) {
        console.error("Error:", e);
    }
}

testFetch();

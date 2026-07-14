export interface StockData {
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    adj_close: number;
    volume: number;
}

const API_BASE_URL = 'http://localhost:8003/api';

export const fetchStockData = async (ticker: string, range: string = 'ALL'): Promise<StockData[]> => {
    try {
        const response = await fetch(`${API_BASE_URL}/stock-data/range?ticker=${ticker}&range=${range}`);
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching stock data:', error);
        return [];
    }
};

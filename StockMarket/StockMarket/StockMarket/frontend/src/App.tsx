import { useState, useEffect } from 'react'
import { CandlestickChart } from './components/CandlestickChart'
import type { TradeInfo } from './components/CandlestickChart'
import { fetchStockData } from './services/api'
import type { StockData } from './services/api'
import './index.css'

function App() {
  const [data, setData] = useState<StockData[]>([]);
  const [ticker, setTicker] = useState<string>('RELIANCE');
  const [range, setRange] = useState<string>('1D');
  const [interval, setInterval] = useState<string>('5m');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  // error is handled in UI but state is kept for future refinement
  const [, setError] = useState<string | null>(null);

  // Mock Trades for Verification
  const [trades] = useState<TradeInfo[]>([
    { price: 2450.50, type: 'ENTRY', time: '2024-05-15 10:00', side: 'BUY' },
  ]);

  // Indicators state
  const [showSMA, setShowSMA] = useState(true);
  const [showEMA, setShowEMA] = useState(false);
  const [showBB, setShowBB] = useState(false);

  useEffect(() => {
    const loadData = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const stockData = await fetchStockData(ticker, range);
        if (!stockData || stockData.length === 0) {
          setError("No data received from API.");
        } else {
          setData(stockData);
        }
      } catch (e: any) {
        setError(e.message || "Error fetching data");
      } finally {
        setIsLoading(false);
      }
    };
    loadData();
  }, [ticker, range, interval]);

  const ranges = ['1D', '1W', '1M', '1Y', 'ALL'];
  const timeframes = ['1m', '5m', '15m', '1h', '1D'];
  const stocks = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'NIFTY', 'BANKNIFTY'];

  return (
    <div className="app-container">
      {/* Top Header */}
      <header className="header">
        <div className="ticker-info">
          <span style={{ color: '#2962ff', fontWeight: 700, fontSize: '1.4rem' }}>LEVERAGE</span>
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}>
            {stocks.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <div className="price-display" style={{ marginLeft: '20px', display: 'flex', gap: '15px', alignItems: 'center' }}>
             <span style={{ fontSize: '1.1rem', fontWeight: 600 }}>₹{data.length > 0 ? data[data.length-1].close.toFixed(2) : '--'}</span>
             <span style={{ color: '#26a69a', fontSize: '0.9rem' }}>+1.2%</span>
          </div>
        </div>
        <div className="user-profile">
          <button className="toolbar-btn">Paper Trading: ₹100,000</button>
        </div>
      </header>

      {/* Toolbar */}
      <div className="toolbar">
        <div className="toolbar-group">
          {timeframes.map(tf => (
            <button 
              key={tf} 
              className={`toolbar-btn ${interval === tf ? 'active' : ''}`}
              onClick={() => setInterval(tf)}
            >
              {tf}
            </button>
          ))}
        </div>
        <div className="toolbar-group">
          <button 
            className={`toolbar-btn ${showSMA ? 'active' : ''}`}
            onClick={() => setShowSMA(!showSMA)}
          >
            SMA 20
          </button>
          <button 
            className={`toolbar-btn ${showEMA ? 'active' : ''}`}
            onClick={() => setShowEMA(!showEMA)}
          >
            EMA 20
          </button>
          <button 
            className={`toolbar-btn ${showBB ? 'active' : ''}`}
            onClick={() => setShowBB(!showBB)}
          >
            Boll Bands
          </button>
        </div>
        <div className="toolbar-group" style={{ marginLeft: 'auto' }}>
           {ranges.map(r => (
             <button 
              key={r} 
              className={`toolbar-btn ${range === r ? 'active' : ''}`}
              onClick={() => setRange(r)}
             >
               {r}
             </button>
           ))}
        </div>
      </div>

      {/* Main Content Area */}
      <div className="main-content">
        <div className="chart-area">
          {isLoading && <div style={{ position: 'absolute', top: '50%', left: '50%', color: 'white', zIndex: 10 }}>Loading...</div>}
          <CandlestickChart 
            data={data} 
            showSMA={showSMA}
            showEMA={showEMA}
            showBB={showBB}
            trades={trades}
          />
        </div>
        
        {/* Sidebar */}
        <aside className="sidebar">
           <div style={{ padding: '20px', borderBottom: '1px solid #2a2e39' }}>
             <h3 style={{ fontSize: '0.9rem', color: '#b2b5be', margin: '0 0 15px 0' }}>Watchlist</h3>
             <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {['NIFTY', 'BANKNIFTY', 'RELIANCE'].map(s => (
                  <div key={s} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem' }}>
                    <span>{s}</span>
                    <span style={{ color: '#26a69a' }}>24,350.50</span>
                  </div>
                ))}
             </div>
           </div>
           <div style={{ padding: '20px' }}>
             <h3 style={{ fontSize: '0.9rem', color: '#b2b5be', margin: '0 0 15px 0' }}>Order Panel</h3>
             <button style={{ width: '100%', background: '#26a69a', border: 'none', color: 'white', padding: '10px', borderRadius: '4px', cursor: 'pointer', fontWeight: 600 }}>BUY</button>
             <button style={{ width: '100%', background: '#ef5350', border: 'none', color: 'white', padding: '10px', borderRadius: '4px', cursor: 'pointer', fontWeight: 600, marginTop: '10px' }}>SELL</button>
           </div>
        </aside>
      </div>
    </div>
  )
}

export default App

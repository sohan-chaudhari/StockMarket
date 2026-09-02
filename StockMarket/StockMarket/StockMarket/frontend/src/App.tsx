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
          <button className="top-nav-btn" style={{ color: '#2962ff' }}>≡</button>
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}>
            {stocks.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <button className="top-nav-btn">⊕ Compare</button>
          <button className="top-nav-btn">ƒx Indicators</button>
        </div>
        <div className="user-profile">
          <button className="toolbar-btn">Paper Trading: ₹100,000</button>
        </div>
      </header>

      {/* Main Content Area */}
      <div className="main-content">
        {/* Left Drawing Toolbar */}
        <div className="left-toolbar">
          <button className="drawing-tool-btn">✛</button>
          <button className="drawing-tool-btn">╱</button>
          <button className="drawing-tool-btn">≡</button>
          <button className="drawing-tool-btn">T</button>
          <button className="drawing-tool-btn">📏</button>
          <button className="drawing-tool-btn">🗑</button>
        </div>

        <div className="chart-area">
          {/* Chart Top Toolbar */}
          <div className="chart-top-toolbar">
            <div style={{ display: 'flex', gap: '5px' }}>
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
            <div style={{ width: '1px', height: '20px', background: 'var(--border-color)' }}></div>
            <div style={{ display: 'flex', gap: '5px' }}>
              <button className={`toolbar-btn ${showSMA ? 'active' : ''}`} onClick={() => setShowSMA(!showSMA)}>SMA 20</button>
              <button className={`toolbar-btn ${showEMA ? 'active' : ''}`} onClick={() => setShowEMA(!showEMA)}>EMA 20</button>
              <button className={`toolbar-btn ${showBB ? 'active' : ''}`} onClick={() => setShowBB(!showBB)}>Boll Bands</button>
            </div>
            <div style={{ width: '1px', height: '20px', background: 'var(--border-color)' }}></div>
            <div style={{ display: 'flex', gap: '5px' }}>
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

          <div style={{ flex: 1, position: 'relative' }}>
            {isLoading && <div style={{ position: 'absolute', top: '50%', left: '50%', color: 'white', zIndex: 10 }}>Loading...</div>}
            <CandlestickChart 
              data={data} 
              showSMA={showSMA}
              showEMA={showEMA}
              showBB={showBB}
              trades={trades}
            />
          </div>
        </div>
        
        {/* Right Sidebar */}
        <aside className="sidebar">
           <div style={{ padding: '15px', borderBottom: '1px solid var(--border-color)' }}>
             <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '0 0 10px 0', textTransform: 'uppercase' }}>Watchlist</h3>
             <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'HDFCBANK'].map(s => (
                  <div key={s} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', padding: '4px 0', cursor: 'pointer', borderBottom: '1px solid rgba(255,255,255,0.02)' }} onClick={() => setTicker(s)}>
                    <span style={{ color: ticker === s ? 'var(--accent-blue)' : 'var(--text-primary)' }}>{s}</span>
                    <span style={{ color: 'var(--accent-green)' }}>--</span>
                  </div>
                ))}
             </div>
           </div>
           <div style={{ padding: '15px' }}>
             <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '0 0 10px 0', textTransform: 'uppercase' }}>Order Panel</h3>
             <div style={{ display: 'flex', gap: '10px' }}>
                <button style={{ flex: 1, background: 'var(--accent-green)', border: 'none', color: 'white', padding: '8px', borderRadius: '4px', cursor: 'pointer', fontWeight: 600 }}>BUY</button>
                <button style={{ flex: 1, background: 'var(--accent-red)', border: 'none', color: 'white', padding: '8px', borderRadius: '4px', cursor: 'pointer', fontWeight: 600 }}>SELL</button>
             </div>
           </div>
        </aside>
      </div>
    </div>
  )
}

export default App

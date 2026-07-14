import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, IChartApi } from 'lightweight-charts';
import { StockData } from '../services/api';

export interface TradeInfo {
    price: number;
    type: 'ENTRY' | 'TP' | 'SL';
    time: string | number;
    side: 'BUY' | 'SELL';
}

interface CandlestickChartProps {
    data: StockData[];
    trades?: TradeInfo[];
    showSMA?: boolean;
    showEMA?: boolean;
    showBB?: boolean;
}

export const CandlestickChart: React.FC<CandlestickChartProps> = ({ 
    data, 
    trades,
    showSMA = true,
    showEMA = false,
    showBB = false
}) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        if (!chartContainerRef.current) return;

        // Ensure container has dimensions
        const { clientWidth, clientHeight } = chartContainerRef.current;
        if (clientWidth === 0 || clientHeight === 0) return;

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: '#131722' },
                textColor: '#d1d4dc',
            },
            grid: {
                vertLines: { color: '#2a2e39' },
                horzLines: { color: '#2a2e39' },
            },
            width: clientWidth,
            height: 600,
            timeScale: {
                timeVisible: true,
                secondsVisible: false,
                borderColor: '#2a2e39',
            },
            rightPriceScale: {
                borderColor: '#2a2e39',
            },
        });

        chartRef.current = chart;

        // 1. Candlestick Series
        const candlestickSeries = chart.addCandlestickSeries({
            upColor: '#089981',
            downColor: '#f23645',
            borderVisible: false,
            wickUpColor: '#089981',
            wickDownColor: '#f23645',
        });

        // 2. Volume Series
        const volumeSeries = chart.addHistogramSeries({
            color: '#089981',
            priceFormat: { type: 'volume' },
            priceScaleId: '',
        });

        volumeSeries.priceScale().applyOptions({
            scaleMargins: { top: 0.8, bottom: 0 },
        });

        // 3. Indicator Series (Line Series)
        const smaSeries = chart.addLineSeries({ color: '#2962FF', lineWidth: 1, title: 'SMA 20' });
        const emaSeries = chart.addLineSeries({ color: '#F2994A', lineWidth: 1, title: 'EMA 20' });
        const bbUpper = chart.addLineSeries({ color: 'rgba(38, 166, 154, 0.4)', lineWidth: 1, title: 'BB Upper' });
        const bbLower = chart.addLineSeries({ color: 'rgba(38, 166, 154, 0.4)', lineWidth: 1, title: 'BB Lower' });

        // Use Set to ensure unique times
        const uniqueData = new Map();
        data.forEach(item => {
            uniqueData.set(item.date, item);
        });
        const sortedData = Array.from(uniqueData.values()).sort((a, b) =>
            new Date(a.date).getTime() - new Date(b.date).getTime()
        );

        const candleData = sortedData.map(item => ({
            time: item.date,
            open: item.open,
            high: item.high,
            low: item.low,
            close: item.close,
        }));

        const volumeData = sortedData.map(item => ({
            time: item.date,
            value: item.volume,
            color: item.close >= item.open ? '#089981' : '#f23645',
        }));

        candlestickSeries.setData(candleData);
        volumeSeries.setData(volumeData);

        // 4. Set Indicator Data
        const smaData = sortedData
            .filter(item => (item as any).sma_20)
            .map(item => ({ time: item.date, value: (item as any).sma_20 }));
        const emaData = sortedData
            .filter(item => (item as any).ema_20)
            .map(item => ({ time: item.date, value: (item as any).ema_20 }));
        const bbUpData = sortedData
            .filter(item => (item as any).bb_upper)
            .map(item => ({ time: item.date, value: (item as any).bb_upper }));
        const bbDownData = sortedData
            .filter(item => (item as any).bb_lower)
            .map(item => ({ time: item.date, value: (item as any).bb_lower }));

        smaSeries.setData(showSMA ? smaData : []);
        emaSeries.setData(showEMA ? emaData : []);
        bbUpper.setData(showBB ? bbUpData : []);
        bbLower.setData(showBB ? bbDownData : []);

        // 5. Trade Markers
        if (trades && trades.length > 0) {
            const markers = trades.map(trade => ({
                time: trade.time,
                position: trade.type === 'ENTRY' ? (trade.side === 'BUY' ? 'belowBar' : 'aboveBar') : 'inBar',
                color: trade.type === 'SL' ? '#ef5350' : (trade.type === 'TP' ? '#26a69a' : '#2962FF'),
                shape: trade.type === 'ENTRY' ? (trade.side === 'BUY' ? 'arrowUp' : 'arrowDown') : 'circle',
                text: `${trade.type} @ ${trade.price}`,
            })) as any;
            candlestickSeries.setMarkers(markers);
        }

        chart.timeScale().fitContent();

        const handleResize = () => {
            if (chartContainerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ 
                    width: chartContainerRef.current.clientWidth,
                    height: chartContainerRef.current.clientHeight 
                });
            }
        };

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
        };
    }, [data, showSMA, showEMA, showBB, trades]);

    return (
        <div ref={chartContainerRef} style={{ width: '100%', height: '100%', position: 'relative' }} />
    );
};

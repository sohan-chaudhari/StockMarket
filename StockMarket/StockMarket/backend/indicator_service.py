"""
Indicator Service
=================
Calculates technical indicators like SMA, EMA, RSI, MACD, and Bollinger Bands.
Uses pandas for efficient vector calculations.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional

class IndicatorService:
    """
    Service for technical analysis indicator calculations.
    Expects a list of candles with OHLCV data.
    """
    
    @staticmethod
    def to_df(candles: List[Dict]) -> pd.DataFrame:
        """Convert candle list to pandas DataFrame"""
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles)
        
        # Ensure 'close' and other prices are numeric
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Ensure 'timestamp' is datetime
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            
        return df

    @staticmethod
    def calculate_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """Simple Moving Average"""
        return df['close'].rolling(window=period).mean()

    @staticmethod
    def calculate_ema(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """Exponential Moving Average"""
        return df['close'].ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Relative Strength Index"""
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
        """Moving Average Convergence Divergence"""
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        
        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram
        }

    @staticmethod
    def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: int = 2) -> Dict[str, pd.Series]:
        """Bollinger Bands"""
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        return {
            "middle": sma,
            "upper": upper,
            "lower": lower
        }

    @classmethod
    def apply_all(cls, candles: List[Dict]) -> List[Dict]:
        """
        Applies a standard set of indicators to the candle list.
        Useful for bulk data preparation.
        """
        if not candles or len(candles) < 30: # Need enough data for MACD slow
            return candles

        df = cls.to_df(candles)
        
        df['sma_20'] = cls.calculate_sma(df, 20)
        df['ema_20'] = cls.calculate_ema(df, 20)
        df['rsi_14'] = cls.calculate_rsi(df, 14)
        
        macd_data = cls.calculate_macd(df)
        df['macd'] = macd_data['macd']
        df['macd_signal'] = macd_data['signal']
        df['macd_hist'] = macd_data['histogram']
        
        bb_data = cls.calculate_bollinger_bands(df)
        df['bb_upper'] = bb_data['upper']
        df['bb_lower'] = bb_data['lower']
        df['bb_middle'] = bb_data['middle']
        
        # Replace NaN and infinity with None for JSON compatibility
        # This is critical to avoid 500 errors in FastAPI response serialization
        df = df.replace([np.nan, np.inf, -np.inf], None)
        
        # Ensure 'timestamp' remains a datetime object if it was converted to Timestamp/None
        # (df.where can sometimes be tricky with types)
        result = df.to_dict('records')
        
        # Final pass to ensure NO NaN/Inf values remain in any record
        ohlcv_cols = {'open', 'high', 'low', 'close', 'adj_close', 'volume'}
        clean_result = []
        for row in result:
            clean_row = {}
            for k, v in row.items():
                if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                    clean_row[k] = 0.0 if k in ohlcv_cols else None
                else:
                    clean_row[k] = v
            clean_result.append(clean_row)
            
        return clean_result

# Global singleton
indicator_service = IndicatorService()

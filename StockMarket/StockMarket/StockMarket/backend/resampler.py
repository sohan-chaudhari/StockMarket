"""
Candle Resampler - Convert 5m candles to higher timeframes
===========================================================
Resample 5-minute OHLCV data to 15m, 1h, and 1d

Uses pandas for efficient time-series resampling
"""

import pandas as pd
from datetime import datetime
from typing import List, Dict


class CandleResampler:
    """
    Resample 5-minute candles to higher timeframes
    """
    
    @staticmethod
    def candles_to_dataframe(candles: List[Dict]) -> pd.DataFrame:
        """
        Convert candle list to pandas DataFrame
        
        Args:
            candles: List of candle dictionaries
        
        Returns:
            DataFrame with timestamp index
        """
        if not candles:
            return pd.DataFrame()
        
        df = pd.DataFrame(candles)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        return df
    
    @staticmethod
    def dataframe_to_candles(df: pd.DataFrame) -> List[Dict]:
        """
        Convert DataFrame back to candle list
        
        Returns:
            List of candle dictionaries
        """
        if df.empty:
            return []
        
        df_reset = df.reset_index()
        return df_reset.to_dict('records')
    
    @staticmethod
    def resample(
        candles: List[Dict], 
        timeframe: str
    ) -> List[Dict]:
        """
        Generic resample function
        
        Args:
            candles: List of 5m candles
            timeframe: '15T', '1H', '1D'
        
        Returns:
            Resampled candles
        """
        if not candles:
            return []
        
        df = CandleResampler.candles_to_dataframe(candles)
        
        resampled = df.resample(timeframe).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        return CandleResampler.dataframe_to_candles(resampled)
    
    @classmethod
    def resample_to_15m(cls, candles_5m: List[Dict]) -> List[Dict]:
        """
        Resample 5m → 15m
        
        Example:
            Input:  09:15, 09:20, 09:25 (3× 5m candles)
            Output: 09:15 (1× 15m candle)
        """
        return cls.resample(candles_5m, '15min')
    
    @classmethod
    def resample_to_1h(cls, candles_5m: List[Dict]) -> List[Dict]:
        """
        Resample 5m → 1h
        
        Example:
            Input:  09:15, 09:20, ..., 10:10 (12× 5m candles)
            Output: 09:00 (1× 1h candle)
        """
        return cls.resample(candles_5m, '1h')
    
    @classmethod
    def resample_to_1d(cls, candles_5m: List[Dict]) -> List[Dict]:
        """
        Resample 5m → 1d
        
        Example:
            Input:  All 5m candles for a day (75 candles)
            Output: Single daily candle
        """
        return cls.resample(candles_5m, '1d')
    
    @staticmethod
    def validate_ohlc(candle: Dict) -> bool:
        """
        Validate OHLC relationships
        
        Rules:
            - high >= open
            - high >= close
            - low <= open
            - low <= close
            - high >= low
        """
        try:
            o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
            
            if h < o or h < c:
                return False
            if l > o or l > c:
                return False
            if h < l:
                return False
            
            return True
        except KeyError:
            return False


# ============================================================================
# Date-Based Resampling Strategy
# ============================================================================

class TieredResampler:
    """
    Apply different resampling based on date ranges
    
    Strategy:
        - Latest → Jan 2025: Keep as 5m
        - Jan 2025 → 2023: Resample to 15m
        - 2023 → 2020: Resample to 1h
        - 2020 → 2015: Resample to 1d
    """
    
    TIER_BOUNDARIES = {
        '5m': datetime(2025, 1, 1),
        '15m': datetime(2023, 1, 1),
        '1h': datetime(2020, 1, 1),
        '1d': datetime(2015, 1, 1)
    }
    
    @classmethod
    def split_by_tiers(cls, candles: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Split candles into date-based tiers
        
        Returns:
            {
                '5m': [...],   # Recent data
                '15m': [...],  # 2025-2023
                '1h': [...],   # 2023-2020
                '1d': [...]    # 2020-2015
            }
        """
        tiers = {
            '5m': [],
            '15m': [],
            '1h': [],
            '1d': []
        }
        
        for candle in candles:
            timestamp = candle['timestamp']
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            
            # Make timestamp naive for comparison with TIER_BOUNDARIES
            if timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            
            if timestamp >= cls.TIER_BOUNDARIES['5m']:
                tiers['5m'].append(candle)
            elif timestamp >= cls.TIER_BOUNDARIES['15m']:
                tiers['15m'].append(candle)
            elif timestamp >= cls.TIER_BOUNDARIES['1h']:
                tiers['1h'].append(candle)
            else:
                tiers['1d'].append(candle)
        
        return tiers
    
    @classmethod
    def apply_tiered_resampling(cls, candles: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Split and resample candles based on date tiers
        
        Returns:
            {
                '5m': List[candles],   # Keep as-is
                '15m': List[candles],  # Resampled to 15m
                '1h': List[candles],   # Resampled to 1h
                '1d': List[candles]    # Resampled to 1d
            }
        """
        # Split by date ranges
        tiers = cls.split_by_tiers(candles)
        
        # Apply resampling to each tier
        resampled = {
            '5m': tiers['5m'],  # Keep original
            '15m': CandleResampler.resample_to_15m(tiers['15m']),
            '1h': CandleResampler.resample_to_1h(tiers['1h']),
            '1d': CandleResampler.resample_to_1d(tiers['1d'])
        }
        
        return resampled
    
    @classmethod
    def print_tier_summary(cls, tiers: Dict[str, List[Dict]]):
        """Print summary of resampled tiers"""
        print("\n" + "=" * 70)
        print("TIERED RESAMPLING SUMMARY")
        print("=" * 70)
        
        total_original = sum(len(candles) for candles in tiers.values())
        
        for timeframe, candles in tiers.items():
            if candles:
                first_date = candles[0]['timestamp']
                last_date = candles[-1]['timestamp']
                
                if isinstance(first_date, str):
                    first_date = datetime.fromisoformat(first_date.replace('Z', '+00:00'))
                if isinstance(last_date, str):
                    last_date = datetime.fromisoformat(last_date.replace('Z', '+00:00'))
                
                print(f"\n{timeframe} candles: {len(candles):,}")
                print(f"  Date range: {first_date.date()} → {last_date.date()}")
        
        print(f"\nTotal candles (all tiers): {total_original:,}")
        print("=" * 70)


# ============================================================================
# Usage Example
# ============================================================================

if __name__ == "__main__":
    # Example: Resample sample data
    
    sample_5m_candles = [
        {
            'timestamp': datetime(2026, 2, 14, 9, 15),
            'open': 2850.0,
            'high': 2855.0,
            'low': 2848.0,
            'close': 2852.0,
            'volume': 10000
        },
        {
            'timestamp': datetime(2026, 2, 14, 9, 20),
            'open': 2852.0,
            'high': 2860.0,
            'low': 2851.0,
            'close': 2858.0,
            'volume': 12000
        },
        {
            'timestamp': datetime(2026, 2, 14, 9, 25),
            'open': 2858.0,
            'high': 2862.0,
            'low': 2856.0,
            'close': 2860.0,
            'volume': 11000
        }
    ]
    
    print("Original 5m candles:")
    for candle in sample_5m_candles:
        print(f"  {candle['timestamp']} | O:{candle['open']} H:{candle['high']} "
              f"L:{candle['low']} C:{candle['close']} V:{candle['volume']}")
    
    # Resample to 15m
    candles_15m = CandleResampler.resample_to_15m(sample_5m_candles)
    
    print("\nResampled 15m candles:")
    for candle in candles_15m:
        print(f"  {candle['timestamp']} | O:{candle['open']} H:{candle['high']} "
              f"L:{candle['low']} C:{candle['close']} V:{candle['volume']}")
    
    # Validate
    for candle in candles_15m:
        is_valid = CandleResampler.validate_ohlc(candle)
        print(f"\nOHLC valid: {is_valid}")

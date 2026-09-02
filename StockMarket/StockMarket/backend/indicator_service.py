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

    @staticmethod
    def calculate_vwap(df: pd.DataFrame, source: str = 'hlc3', anchor: str = 'Session', band_mult: float = 1.0, bands: bool = False) -> Dict[str, pd.Series]:
        """Volume Weighted Average Price (Session-anchored)"""
        if df.empty or 'volume' not in df.columns:
            return {'vwap': pd.Series(dtype=float)}

        if source == 'close':
            tp = df['close']
        elif source == 'open':
            tp = df['open'] if 'open' in df.columns else df['close']
        elif source == 'high':
            tp = df['high'] if 'high' in df.columns else df['close']
        elif source == 'low':
            tp = df['low'] if 'low' in df.columns else df['close']
        elif source == 'hl2':
            h = df['high'] if 'high' in df.columns else df['close']
            l = df['low'] if 'low' in df.columns else df['close']
            tp = (h + l) / 2.0
        elif source == 'ohlc4':
            o = df['open'] if 'open' in df.columns else df['close']
            h = df['high'] if 'high' in df.columns else df['close']
            l = df['low'] if 'low' in df.columns else df['close']
            c = df['close']
            tp = (o + h + l + c) / 4.0
        else: # default hlc3
            h = df['high'] if 'high' in df.columns else df['close']
            l = df['low'] if 'low' in df.columns else df['close']
            c = df['close']
            tp = (h + l + c) / 3.0

        vol = df['volume'].fillna(0).clip(lower=0)
        pv = tp * vol
        p2v = tp * tp * vol

        if anchor == 'Session' and 'timestamp' in df.columns:
            ts = pd.to_datetime(df['timestamp'])
            dates = (ts + pd.Timedelta(hours=5, minutes=30)).dt.date
            cum_vol = vol.groupby(dates).cumsum()
            cum_pv = pv.groupby(dates).cumsum()
            cum_p2v = p2v.groupby(dates).cumsum()
        else:
            cum_vol = vol.cumsum()
            cum_pv = pv.cumsum()
            cum_p2v = p2v.cumsum()

        vwap = np.where(cum_vol > 0, cum_pv / cum_vol, tp)
        res = {'vwap': pd.Series(vwap, index=df.index)}

        if bands:
            variance = np.where(cum_vol > 0, np.maximum(0, (cum_p2v / cum_vol) - (vwap ** 2)), 0)
            stdev = np.sqrt(variance)
            res['upper'] = pd.Series(vwap + band_mult * stdev, index=df.index)
            res['lower'] = pd.Series(vwap - band_mult * stdev, index=df.index)
            res['std_dev'] = pd.Series(stdev, index=df.index)

        return res

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Average True Range using Wilder's smoothing methodology."""
        if df.empty or len(df) == 0:
            return pd.Series(dtype=float)
        
        h = df['high'] if 'high' in df.columns else df['close']
        l = df['low'] if 'low' in df.columns else df['close']
        c = df['close']
        prev_c = c.shift(1)
        
        hl = (h - l).abs()
        hpc = (h - prev_c).abs()
        lpc = (l - prev_c).abs()
        
        tr = np.maximum(hl, np.maximum(hpc.fillna(hl), lpc.fillna(hl)))
        tr_series = pd.Series(tr, index=df.index)
        
        if period <= 1:
            return tr_series
        
        if len(df) < period:
            return pd.Series(np.nan, index=df.index)
        
        atr_vals = np.full(len(df), np.nan, dtype=float)
        initial_atr = tr_series.iloc[:period].mean()
        atr_vals[period - 1] = initial_atr
        
        cur_atr = initial_atr
        for i in range(period, len(df)):
            cur_atr = (cur_atr * (period - 1) + tr_series.iloc[i]) / period
            atr_vals[i] = cur_atr
            
        return pd.Series(atr_vals, index=df.index)

    @classmethod
    def calculate_supertrend(cls, df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
        """
        Supertrend Trend-Following Overlay Indicator.
        Reuses calculate_atr(df, period).
        """
        if df.empty or len(df) == 0:
            return pd.DataFrame(index=df.index, columns=['supertrend', 'trend', 'upper', 'lower'])

        atr = cls.calculate_atr(df, period=period)
        h = df['high'] if 'high' in df.columns else df['close']
        l = df['low'] if 'low' in df.columns else df['close']
        c = df['close']

        hl2 = (h + l) / 2.0
        basic_upper = hl2 + (multiplier * atr)
        basic_lower = hl2 - (multiplier * atr)

        n = len(df)
        supertrend = np.full(n, np.nan, dtype=float)
        trend = np.full(n, np.nan, dtype=float)
        final_upper = np.full(n, np.nan, dtype=float)
        final_lower = np.full(n, np.nan, dtype=float)

        if n < period or period < 1 or multiplier <= 0:
            return pd.DataFrame({
                'supertrend': pd.Series(supertrend, index=df.index),
                'trend': pd.Series(trend, index=df.index),
                'upper': pd.Series(final_upper, index=df.index),
                'lower': pd.Series(final_lower, index=df.index)
            })

        seed_idx = period - 1
        for i in range(seed_idx, n):
            cur_atr = atr.iloc[i]
            if pd.isna(cur_atr):
                continue
            
            bu = basic_upper.iloc[i]
            bl = basic_lower.iloc[i]
            cl = c.iloc[i]

            if i == seed_idx or np.isnan(final_upper[i - 1]):
                final_upper[i] = bu
                final_lower[i] = bl
                init_trend = 1 if cl > bu else (-1 if cl < bl else (1 if cl >= hl2.iloc[i] else -1))
                trend[i] = init_trend
                supertrend[i] = bl if init_trend == 1 else bu
            else:
                prev_fu = final_upper[i - 1]
                prev_fl = final_lower[i - 1]
                prev_c = c.iloc[i - 1]
                prev_trend = trend[i - 1]

                # Final Upper Band
                if bu < prev_fu or prev_c > prev_fu:
                    final_upper[i] = bu
                else:
                    final_upper[i] = prev_fu

                # Final Lower Band
                if bl > prev_fl or prev_c < prev_fl:
                    final_lower[i] = bl
                else:
                    final_lower[i] = prev_fl

                # Trend Determination
                if prev_trend == -1:
                    if cl > final_upper[i]:
                        trend[i] = 1
                        supertrend[i] = final_lower[i]
                    else:
                        trend[i] = -1
                        supertrend[i] = final_upper[i]
                else:
                    if cl < final_lower[i]:
                        trend[i] = -1
                        supertrend[i] = final_upper[i]
                    else:
                        trend[i] = 1
                        supertrend[i] = final_lower[i]

        return pd.DataFrame({
            'supertrend': pd.Series(supertrend, index=df.index),
            'trend': pd.Series(trend, index=df.index),
            'upper': pd.Series(final_upper, index=df.index),
            'lower': pd.Series(final_lower, index=df.index)
        })

    @classmethod
    def calculate_stochastic(cls, df: pd.DataFrame, k_period: int = 14, k_smooth: int = 3, d_period: int = 3) -> pd.DataFrame:
        """
        Stochastic Oscillator (%K, %D).
        Formula:
          HH = rolling highest high over k_period
          LL = rolling lowest low over k_period
          Raw %K = 100 * (Close - LL) / (HH - LL)
          Smoothed %K = SMA(Raw %K, k_smooth)
          %D = SMA(Smoothed %K, d_period)
        """
        if df.empty or len(df) == 0:
            return pd.DataFrame(index=df.index, columns=['k', 'd', 'raw_k'])

        n = len(df)
        raw_k = np.full(n, np.nan, dtype=float)
        k = np.full(n, np.nan, dtype=float)
        d = np.full(n, np.nan, dtype=float)

        if n < k_period or k_period < 1 or k_smooth < 1 or d_period < 1:
            return pd.DataFrame({
                'k': pd.Series(k, index=df.index),
                'd': pd.Series(d, index=df.index),
                'raw_k': pd.Series(raw_k, index=df.index)
            })

        h = df['high'] if 'high' in df.columns else df['close']
        l = df['low'] if 'low' in df.columns else df['close']
        c = df['close']

        # 1. Raw %K
        for i in range(k_period - 1, n):
            hh = h.iloc[i - k_period + 1 : i + 1].max()
            ll = l.iloc[i - k_period + 1 : i + 1].min()
            rng = hh - ll
            if rng == 0 or np.isnan(rng):
                val = 50.0
            else:
                val = 100.0 * (c.iloc[i] - ll) / rng
            raw_k[i] = max(0.0, min(100.0, val))

        # 2. Smoothed %K = SMA(Raw %K, k_smooth)
        k_min_idx = k_period - 1 + k_smooth - 1
        if n > k_min_idx:
            if k_smooth == 1:
                for i in range(k_period - 1, n):
                    k[i] = raw_k[i]
            else:
                sum_k = sum(raw_k[k_period - 1 : k_min_idx + 1])
                k[k_min_idx] = max(0.0, min(100.0, sum_k / k_smooth))
                for i in range(k_min_idx + 1, n):
                    sum_k += raw_k[i] - raw_k[i - k_smooth]
                    k[i] = max(0.0, min(100.0, sum_k / k_smooth))

        # 3. %D = SMA(Smoothed %K, d_period)
        d_min_idx = k_min_idx + d_period - 1
        if n > d_min_idx:
            if d_period == 1:
                for i in range(k_min_idx, n):
                    d[i] = k[i]
            else:
                sum_d = sum(k[k_min_idx : d_min_idx + 1])
                d[d_min_idx] = max(0.0, min(100.0, sum_d / d_period))
                for i in range(d_min_idx + 1, n):
                    sum_d += k[i] - k[i - d_period]
                    d[i] = max(0.0, min(100.0, sum_d / d_period))

        return pd.DataFrame({
            'k': pd.Series(k, index=df.index),
            'd': pd.Series(d, index=df.index),
            'raw_k': pd.Series(raw_k, index=df.index)
        })

    @staticmethod
    def calculate_adx(df: pd.DataFrame, di_length: int = 14, adx_smoothing: int = 14, period: Optional[int] = None) -> pd.DataFrame:
        """
        Average Directional Index (ADX) & Directional Movement Index (DMI).
        Wilder's methodology.
        Returns DataFrame with 'adx', 'plus_di', 'minus_di', 'dx'.
        """
        if period is not None:
            di_length = period
            adx_smoothing = period

        n = len(df)
        empty_res = pd.DataFrame({
            'adx': pd.Series([np.nan] * n, index=df.index),
            'plus_di': pd.Series([np.nan] * n, index=df.index),
            'minus_di': pd.Series([np.nan] * n, index=df.index),
            'dx': pd.Series([np.nan] * n, index=df.index)
        })
        if df.empty or n == 0 or di_length < 1 or adx_smoothing < 1:
            return empty_res

        h = df['high'].astype(float).fillna(df['close'])
        l = df['low'].astype(float).fillna(df['close'])
        c = df['close'].astype(float)

        # 1. True Range & Directional Movement (+DM, -DM)
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)

        tr[0] = abs(h.iloc[0] - l.iloc[0])
        for i in range(1, n):
            h_cur = h.iloc[i]
            l_cur = l.iloc[i]
            c_prev = c.iloc[i - 1]
            h_prev = h.iloc[i - 1]
            l_prev = l.iloc[i - 1]

            hl = abs(h_cur - l_cur)
            hpc = abs(h_cur - c_prev)
            lpc = abs(l_cur - c_prev)
            tr[i] = max(hl, hpc, lpc)

            up_move = h_cur - h_prev
            down_move = l_prev - l_cur

            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            else:
                plus_dm[i] = 0.0

            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
            else:
                minus_dm[i] = 0.0

        plus_di = [np.nan] * n
        minus_di = [np.nan] * n
        dx = [np.nan] * n
        adx = [np.nan] * n

        if n < di_length:
            return pd.DataFrame({
                'adx': pd.Series(adx, index=df.index),
                'plus_di': pd.Series(plus_di, index=df.index),
                'minus_di': pd.Series(minus_di, index=df.index),
                'dx': pd.Series(dx, index=df.index)
            })

        # 2. Initial Wilder sum for TR, +DM, -DM
        smoothed_tr = np.zeros(n)
        smoothed_plus_dm = np.zeros(n)
        smoothed_minus_dm = np.zeros(n)

        smoothed_tr[di_length - 1] = sum(tr[:di_length])
        smoothed_plus_dm[di_length - 1] = sum(plus_dm[:di_length])
        smoothed_minus_dm[di_length - 1] = sum(minus_dm[:di_length])

        if smoothed_tr[di_length - 1] == 0:
            plus_di[di_length - 1] = 0.0
            minus_di[di_length - 1] = 0.0
        else:
            plus_di[di_length - 1] = max(0.0, min(100.0, 100.0 * smoothed_plus_dm[di_length - 1] / smoothed_tr[di_length - 1]))
            minus_di[di_length - 1] = max(0.0, min(100.0, 100.0 * smoothed_minus_dm[di_length - 1] / smoothed_tr[di_length - 1]))

        sum_di = plus_di[di_length - 1] + minus_di[di_length - 1]
        diff_di = abs(plus_di[di_length - 1] - minus_di[di_length - 1])
        dx[di_length - 1] = 0.0 if sum_di == 0 else max(0.0, min(100.0, 100.0 * diff_di / sum_di))

        # 3. Subsequent Wilder smoothing for +DI, -DI, DX
        for i in range(di_length, n):
            smoothed_tr[i] = smoothed_tr[i - 1] - (smoothed_tr[i - 1] / di_length) + tr[i]
            smoothed_plus_dm[i] = smoothed_plus_dm[i - 1] - (smoothed_plus_dm[i - 1] / di_length) + plus_dm[i]
            smoothed_minus_dm[i] = smoothed_minus_dm[i - 1] - (smoothed_minus_dm[i - 1] / di_length) + minus_dm[i]

            if smoothed_tr[i] == 0:
                plus_di[i] = 0.0
                minus_di[i] = 0.0
            else:
                plus_di[i] = max(0.0, min(100.0, 100.0 * smoothed_plus_dm[i] / smoothed_tr[i]))
                minus_di[i] = max(0.0, min(100.0, 100.0 * smoothed_minus_dm[i] / smoothed_tr[i]))

            s_di = plus_di[i] + minus_di[i]
            d_di = abs(plus_di[i] - minus_di[i])
            dx[i] = 0.0 if s_di == 0 else max(0.0, min(100.0, 100.0 * d_di / s_di))

        # 4. ADX = Wilder-smoothed DX (warm-up requires adx_smoothing DX values => (di_length - 1) + (adx_smoothing - 1) index)
        adx_seed_idx = (di_length - 1) + (adx_smoothing - 1)
        if n > adx_seed_idx:
            adx[adx_seed_idx] = sum(dx[di_length - 1 : adx_seed_idx + 1]) / adx_smoothing
            for i in range(adx_seed_idx + 1, n):
                adx[i] = (adx[i - 1] * (adx_smoothing - 1) + dx[i]) / adx_smoothing

        return pd.DataFrame({
            'adx': pd.Series(adx, index=df.index),
            'plus_di': pd.Series(plus_di, index=df.index),
            'minus_di': pd.Series(minus_di, index=df.index),
            'dx': pd.Series(dx, index=df.index)
        })

    calculate_dmi = calculate_adx

    @staticmethod
    def calculate_obv(df: pd.DataFrame) -> pd.DataFrame:
        """
        On-Balance Volume (OBV).
        Deterministic cumulative volume based on price direction relative to previous close.
        OBV[0] = 0.
        Returns DataFrame with 'obv'.
        """
        n = len(df)
        empty_res = pd.DataFrame({'obv': pd.Series([np.nan] * n, index=df.index)})
        if df.empty or n == 0:
            return empty_res

        c = df['close'].astype(float)
        v = df['volume'].fillna(0).astype(float)

        obv = np.zeros(n)
        cur_obv = 0.0
        obv[0] = 0.0

        for i in range(1, n):
            c_prev = c.iloc[i - 1]
            c_cur = c.iloc[i]
            vol = v.iloc[i]
            if not np.isfinite(vol) or vol < 0:
                vol = 0.0

            if np.isnan(c_cur) or np.isnan(c_prev):
                pass
            elif c_cur > c_prev:
                cur_obv += vol
            elif c_cur < c_prev:
                cur_obv -= vol
            obv[i] = cur_obv

        return pd.DataFrame({'obv': pd.Series(obv, index=df.index)})

    @staticmethod
    def calculate_cci(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """
        Commodity Channel Index (CCI).
        TP = (High + Low + Close) / 3
        SMA_TP = SMA(TP, period)
        MeanDeviation = mean(|TP - SMA_TP| for window)
        CCI = (TP - SMA_TP) / (0.015 * MeanDeviation)
        Returns DataFrame with 'cci'.
        """
        n = len(df)
        empty_res = pd.DataFrame({'cci': pd.Series([np.nan] * n, index=df.index)})
        if df.empty or n == 0 or period < 1 or n < period:
            return empty_res

        h = df['high'].astype(float).fillna(df['close'])
        l = df['low'].astype(float).fillna(df['close'])
        c = df['close'].astype(float)

        tp = (h + l + c) / 3.0
        cci = [np.nan] * n

        for i in range(period - 1, n):
            window_tp = tp.iloc[i - period + 1 : i + 1]
            sma_tp = window_tp.mean()
            mean_dev = (window_tp - sma_tp).abs().mean()

            if mean_dev == 0 or np.isnan(mean_dev):
                cci[i] = np.nan
            else:
                cci[i] = (tp.iloc[i] - sma_tp) / (0.015 * mean_dev)

        return pd.DataFrame({'cci': pd.Series(cci, index=df.index)})

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
        df['atr_14'] = cls.calculate_atr(df, 14)
        
        st_data = cls.calculate_supertrend(df, 10, 3.0)
        df['supertrend_10_3'] = st_data['supertrend']

        stoch_data = cls.calculate_stochastic(df, 14, 3, 3)
        df['stoch_k'] = stoch_data['k']
        df['stoch_d'] = stoch_data['d']

        adx_data = cls.calculate_adx(df, 14)
        df['adx_14'] = adx_data['adx']
        df['plus_di_14'] = adx_data['plus_di']
        df['minus_di_14'] = adx_data['minus_di']
        
        df['obv'] = cls.calculate_obv(df)['obv']
        df['cci_20'] = cls.calculate_cci(df, 20)['cci']

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

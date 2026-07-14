import pandas as pd
from typing import List, Dict

class ResampleService:
    """
    Unified resampling engine. Engine-agnostic (using Pandas currently).
    Public API: resample(candles, source_tf, target_tf)
    """

    RESAMPLE_RULES = {
        "15m": "15min",
        "30m": "30min",
        "1H": "1h",
        "4H": "4h",
        "1D": "1D",
        "1W": "W-MON",
        "1M": "MS"
    }

    @staticmethod
    def resample(candles: List[Dict], source_tf: str, target_tf: str) -> List[Dict]:
        """
        Resample candles from source_tf to target_tf.
        Supported conversions:
        5m->15m, 15m->30m, 30m->1H, 1H->4H, 4H->1D, 1D->1W, 1W->1M
        """
        if not candles:
            return []

        # Standard Pandas mapping
        rule = ResampleService.RESAMPLE_RULES.get(target_tf)
        if not rule:
            # Check lowercase fallbacks
            rule = ResampleService.RESAMPLE_RULES.get(target_tf.upper())
            if not rule:
                raise ValueError(f"Unsupported target timeframe: {target_tf}")

        df = pd.DataFrame(candles)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)

        # Indian Stock Market opens at 09:15. For intraday timeframes (15m, 30m, 1h, 4h),
        # we need to offset the bins by 15 minutes so they align correctly.
        offset = '15min' if target_tf.lower() in ['15m', '30m', '1h', '4h'] else None

        resampled = df.resample(rule, closed='left', label='left', offset=offset).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        if resampled.empty:
            return []

        df_reset = resampled.reset_index()
        return df_reset.to_dict('records')

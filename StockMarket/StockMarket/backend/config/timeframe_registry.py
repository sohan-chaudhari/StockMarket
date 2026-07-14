TIMEFRAME_REGISTRY = {
    "5m":  {"type": "base",    "minutes": 5,   "resample_rule": "5min"},
    "15m": {"type": "intraday", "minutes": 15,  "resample_rule": "15min"},
    "30m": {"type": "intraday", "minutes": 30,  "resample_rule": "30min"},
    "1h":  {"type": "intraday", "minutes": 60,  "resample_rule": "1h"},
    "4h":  {"type": "intraday", "minutes": 240, "resample_rule": "4h"},
    "1D":  {"type": "session", "boundary": "market_close"},
    "1W":  {"type": "week",    "boundary": "trading_week"},
    "1M":  {"type": "month",   "boundary": "trading_month"},
}

INTRADAY_TFS = [tf for tf, cfg in TIMEFRAME_REGISTRY.items() if cfg["type"] == "intraday"]
SESSION_TFS = [tf for tf, cfg in TIMEFRAME_REGISTRY.items() if cfg["type"] != "intraday"]
ALL_TFS = list(TIMEFRAME_REGISTRY.keys())

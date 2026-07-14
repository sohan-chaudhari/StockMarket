RETENTION_POLICY = {
    "version": 2,
    "retention_policy": [
        {"source_tf": "5m",  "target_tf": "15m", "after_days": 120,  "after_trading_sessions": 44, "batch_size": 50},
        {"source_tf": "15m", "target_tf": "30m", "after_days": 365,  "after_trading_sessions": 44, "batch_size": 100},
        {"source_tf": "30m", "target_tf": "1h",  "after_days": 730,  "after_trading_sessions": 44, "batch_size": 200},
        {"source_tf": "1h",  "target_tf": "4h",  "after_days": 1095, "after_trading_sessions": 44, "batch_size": 500},
        {"source_tf": "4h",  "target_tf": "1D",  "after_days": 1460, "after_trading_sessions": 44, "batch_size": 1000},
        {"source_tf": "1D",  "target_tf": "1W",  "after_days": 1825, "batch_size": 2000},
        {"source_tf": "1W",  "target_tf": "1M",  "after_days": 3650, "batch_size": 5000},
    ],
}

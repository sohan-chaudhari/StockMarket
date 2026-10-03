# LEVERAGE Platform — Technical Documentation Package

This directory contains the production-grade, publication-quality LaTeX technical specification and architectural manual for the **LEVERAGE** Indian Equity Analytics and Paper Trading Platform.

## Deliverables & File Structure

```text
docs/technical/
├── main.tex                                    # Master LaTeX root document
├── LEVERAGE_Core_Platform_Technical_Documentation.pdf  # Compiled 52-page engineering manual
├── chapters/
│   ├── ch01_executive_overview.tex             # Chapter 1: Executive Overview & System Coordinates
│   ├── ch02_platform_architecture.tex          # Chapter 2: System Architecture & Data Flow (TikZ)
│   ├── ch03_market_data_architecture.tex       # Chapter 3: Market Data Pipeline & Multi-Tier Ingestion
│   ├── ch04_candle_timeseries_architecture.tex # Chapter 4: Historical Candle & Time-Series Architecture
│   ├── ch05_chart_visualization_architecture.tex # Chapter 5: Chart & Visualization Architecture
│   ├── ch06_technical_indicators.tex           # Chapter 6: Technical Indicators & Quantitative Math
│   ├── ch07_smc_price_action.tex               # Chapter 7: Smart Money Concepts & Price Action
│   ├── ch08_market_intelligence.tex            # Chapter 8: Market Intelligence & Screener
│   ├── ch09_stock_intelligence_recommendations.tex # Chapter 9: Stock Scoring & Recommendation
│   ├── ch10_portfolio_intelligence.tex         # Chapter 10: Portfolio & Paper Trading Engine
│   ├── ch11_api_architecture.tex               # Chapter 11: REST API Routing & Core Endpoints
│   ├── ch12_websocket_realtime.tex             # Chapter 12: Real-Time WebSockets (/ws/dashboard, /ws/user)
│   ├── ch13_auth_security.tex                  # Chapter 13: Authentication, Bcrypt & Security Controls
│   ├── ch14_database_architecture.tex          # Chapter 14: Database Models & SQLAlchemy Pool
│   ├── ch15_caching_performance.tex            # Chapter 15: In-Memory Caching (L1/L2/Byte-Stream)
│   ├── ch16_memory_resource_engineering.tex    # Chapter 16: Memory Budgeting & Glibc Arenas (1 GB Target)
│   ├── ch17_deployment_architecture.tex        # Chapter 17: Containerization & Reverse Proxy (Docker/Nginx)
│   ├── ch18_aws_readiness_validation.tex       # Chapter 18: AWS Readiness & Audit Reconciliation
│   ├── ch19_testing_architecture.tex           # Chapter 19: Test Suite Verification Baseline (1,596 Tests)
│   ├── ch20_reliability_failure_recovery.tex   # Chapter 20: Fault Recovery & Post-Mortem Incidents
│   ├── ch21_known_limitations_tech_debt.tex    # Chapter 21: Confirmed Limitations & Technical Debt
│   ├── ch22_future_architecture.tex            # Chapter 22: Roadmap Vectors (Redis, Multi-Broker)
│   └── ch23_glossary.tex                       # Chapter 23: Technical & Financial Glossary
├── appendices/
│   ├── app_a_file_reference.tex                # Appendix A: Codebase File & Module Directory
│   ├── app_b_api_reference.tex                 # Appendix B: Complete 70-Route API Inventory
│   ├── app_c_config_reference.tex              # Appendix C: Environment Variables Master Table
│   └── app_d_system_constants.tex              # Appendix D: System Constants & Operational Limits
└── README.md                                   # Documentation package overview
```

## Compilation Instructions

The document is built using modern XeTeX-powered engines (such as Tectonic or XeLaTeX):

```bash
# Using Tectonic (zero-configuration standalone compiler):
tectonic docs/technical/main.tex --outdir docs/technical/

# Alternatively, using standard XeLaTeX:
xelatex -interaction=nonstopmode -output-directory=docs/technical docs/technical/main.tex
xelatex -interaction=nonstopmode -output-directory=docs/technical docs/technical/main.tex
```

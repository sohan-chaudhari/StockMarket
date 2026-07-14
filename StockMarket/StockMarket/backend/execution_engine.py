"""
Background Execution Engine for TP/SL Orders
Monitors pending orders and executes them when price conditions are met.
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

import models, database
from trade_service import TradingService
from websocket_manager import user_ws_manager


class PriceMonitorService:
    """Background service that monitors prices and executes TP/SL orders"""
    
    def __init__(self):
        self.is_running = False
        self.check_interval = 2  # seconds
        self.price_cache: Dict[str, float] = {}
        self.cache_ttl = 3  # seconds
        self.last_cache_update = None

    
    async def start(self):
        """Start the background monitoring service"""
        if self.is_running:
            print("[Engine] Already running")
            return
        
        await self.recover_stuck_orders()
        self.is_running = True
        print("[Engine] TP/SL Execution Engine started")
        
        while self.is_running:
            try:
                await self.check_pending_orders()
            except Exception as e:
                print(f"[Engine] Error in check cycle: {e}")
            
            await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Stop the background service"""
        self.is_running = False
        print("[Engine] TP/SL Execution Engine stopped")
    
    async def check_pending_orders(self):
        """Check all pending orders and execute if conditions are met"""
        db = database.SessionLocal()
        
        try:
            # 1. Get all pending orders
            pending_orders = db.query(models.Order).filter(
                models.Order.status == "PENDING"
            ).all()
            
            if not pending_orders:
                return
            
            # 2. Group by ticker to minimize price fetches
            orders_by_ticker: Dict[str, List[models.Order]] = {}
            for order in pending_orders:
                if order.ticker not in orders_by_ticker:
                    orders_by_ticker[order.ticker] = []
                orders_by_ticker[order.ticker].append(order)
            
            # 3. Fetch current prices for each ticker
            prices = await self.get_current_prices(db, list(orders_by_ticker.keys()))
            
            # 4. Check each order against current price
            for ticker, orders in orders_by_ticker.items():
                current_price = prices.get(ticker)
                if current_price is None:
                    continue
                
                for order in orders:
                    await self.execute_order(db, order, current_price)
        
        except Exception as e:
            print(f"[Engine] Error checking orders: {e}")
            db.rollback()
        finally:
            db.close()
    
    async def get_current_prices(self, db: Session, tickers: List[str]) -> Dict[str, float]:
        """Get current prices for tickers from the aggregator's Candle table.
        Uses the latest completed 1m candle (most recent price snap)."""
        prices = {}
        if not tickers:
            return prices

        from datetime import date
        today = date.today()
        from sqlalchemy import func

        # Primary source: latest completed 1m candle from Candle table (aggregator writes here)
        subq = db.query(
            models.Candle.ticker,
            func.max(models.Candle.timestamp).label('max_ts')
        ).filter(
            models.Candle.ticker.in_(tickers),
            models.Candle.timeframe == "1m",
            models.Candle.is_completed == True,
            func.date(models.Candle.timestamp) == today
        ).group_by(models.Candle.ticker).subquery()

        rows = db.query(models.Candle).join(
            subq,
            (models.Candle.ticker == subq.c.ticker) &
            (models.Candle.timestamp == subq.c.max_ts)
        ).all()
        for r in rows:
            prices[r.ticker] = r.close

        # Fallback: StockData latest close for tickers not found in Candle table
        missing = [t for t in tickers if t not in prices]
        if missing:
            subq2 = db.query(
                models.StockData.ticker,
                func.max(models.StockData.date).label('max_date')
            ).filter(
                models.StockData.ticker.in_(missing)
            ).group_by(models.StockData.ticker).subquery()

            hist_rows = db.query(models.StockData.ticker, models.StockData.close).join(
                subq2,
                (models.StockData.ticker == subq2.c.ticker) &
                (models.StockData.date == subq2.c.max_date)
            ).all()
            for row in hist_rows:
                if row[0] not in prices:
                    prices[row[0]] = row[1]

        return prices
    
    async def execute_order(self, db: Session, order: models.Order, execution_price: float):
        """Check if order should execute, then execute. Single locked transaction."""
        try:
            # Lock position row — prevents race with another execution
            position = db.query(models.Position).filter(
                models.Position.id == order.position_id,
                models.Position.status == "OPEN"
            ).with_for_update().first()

            if not position:
                print(f"[Engine] Position {order.position_id} not found or already closed")
                return

            # Check trigger condition within the locked transaction
            if position.position_type == "LONG":
                triggered = (
                    (order.order_type == "TP" and execution_price >= order.trigger_price) or
                    (order.order_type == "SL" and execution_price <= order.trigger_price)
                )
            else:  # SHORT
                triggered = (
                    (order.order_type == "TP" and execution_price <= order.trigger_price) or
                    (order.order_type == "SL" and execution_price >= order.trigger_price)
                )
            if not triggered:
                return

            # Determine close type
            close_type = "TP_EXECUTED" if order.order_type == "TP" else "SL_EXECUTED"

            # Mark order as executing first (prevents double-execution on crash+restart)
            old_status = order.status
            order.status = "EXECUTING"
            db.flush()

            # Use TradingService to close position (will commit)
            result_position, message = TradingService.close_position(
                db=db,
                user_id=position.user_id,
                position_id=position.id,
                closing_price=execution_price,
                close_type=close_type
            )

            if result_position:
                # Update the order that triggered this
                order.status = "EXECUTED"
                order.execution_price = execution_price
                order.executed_at = database.get_ist_now()
                db.commit()

                # Fetch updated balance
                user = db.query(models.User).filter(models.User.user_id == position.user_id).first()
                new_balance = user.virtual_balance

                print(f"[Engine] {close_type}: Position {position.id} ({position.ticker}) "
                      f"closed at ₹{execution_price:.2f}. P&L: ₹{result_position.realized_pnl:.2f}")

                # Broadcast event
                await user_ws_manager.send_personal_message({
                    "type": "order_filled",
                    "data": {
                        "ticker": position.ticker,
                        "order_type": order.order_type,
                        "execution_price": execution_price,
                        "pnl": result_position.realized_pnl,
                        "new_balance": new_balance
                    }
                }, user_id=position.user_id)
            else:
                print(f"[Engine] Failed to execute order {order.id}: {message}")
                order.status = old_status
                db.commit()

        except Exception as e:
            print(f"[Engine] Error executing order {order.id}: {e}")
            db.rollback()

    async def recover_stuck_orders(self):
        """Recover TP/SL orders stuck in EXECUTING status after a crash."""
        db = database.SessionLocal()
        try:
            stuck_orders = db.query(models.Order).filter(
                models.Order.status == "EXECUTING"
            ).all()
            if not stuck_orders:
                db.close()
                return
            for order in stuck_orders:
                position = db.query(models.Position).filter(
                    models.Position.id == order.position_id
                ).first()
                if not position:
                    order.status = "PENDING"
                    db.commit()
                    continue
                prices = await self.get_current_prices(db, [order.ticker])
                current_price = prices.get(order.ticker)
                if current_price is None:
                    order.status = "PENDING"
                    db.commit()
                    continue
                trigger_price = order.trigger_price
                if trigger_price and abs(current_price - trigger_price) / trigger_price <= 0.01:
                    await self.execute_order(db, order, current_price)
                else:
                    order.status = "PENDING"
                    db.commit()
        except Exception as e:
            print(f"[Engine] Error recovering stuck orders: {e}")
            db.rollback()
        finally:
            db.close()


# Global instance
price_monitor = PriceMonitorService()


async def start_execution_engine():
    """Start the background execution engine"""
    await price_monitor.start()

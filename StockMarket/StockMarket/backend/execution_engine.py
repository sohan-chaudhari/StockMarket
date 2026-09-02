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
from price_provider import PriceProvider


class PriceMonitorService:
    """Background service that monitors prices and executes TP/SL orders"""

    def __init__(self):
        self.is_running = False
        self.check_interval = 2  # seconds
        self._provider = None

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

    def _is_market_open(self) -> bool:
        """Return True only during NSE trading hours (09:15–15:30 IST, Mon–Fri,
        excluding NSE holidays). Orders must never fire on stale off-hours prices."""
        try:
            from exchange_calendar import nse_calendar
            return nse_calendar.is_market_open(database.get_ist_now())
        except Exception:
            # Fail-safe: if calendar is unavailable, do not execute
            return False

    def _reconcile_execution_subscriptions(self, db: Session):
        """Reconcile execution reference counts from DB-authoritative truth.
        Counts orders WHERE status IN ('PENDING', 'EXECUTING') grouped by ticker.
        Updates viewed_ticker_mgr with exact counts so tickers remain subscribed
        regardless of viewer state (ADR-011)."""
        try:
            active_orders = db.query(models.Order.ticker, models.Order.id).filter(
                models.Order.status.in_(["PENDING", "EXECUTING"])
            ).all()

            counts: Dict[str, int] = {}
            for row in active_orders:
                tkr = row.ticker
                counts[tkr] = counts.get(tkr, 0) + 1

            import main
            if hasattr(main, "viewed_ticker_mgr") and main.viewed_ticker_mgr:
                main.viewed_ticker_mgr.set_execution_ref_counts(counts)
        except Exception as e:
            print(f"[Engine] Error reconciling execution subscriptions: {e}")

    async def check_pending_orders(self):
        """Check all pending orders and execute if conditions are met.

        Guard: exits immediately when the market is closed so that no order
        is ever evaluated against a stale price from a previous session.
        """
        db = database.SessionLocal()

        try:
            # Reconcile DB-authoritative execution subscription counts
            self._reconcile_execution_subscriptions(db)

            # ── Market-hours guard ─────────────────────────────────────────────
            if not self._is_market_open():
                return  # Never fire TP/SL outside 09:15–15:30 IST

            # 1. Get all pending TP/SL orders
            pending_orders = db.query(models.Order).filter(
                models.Order.status == "PENDING"
            ).all()

            if not pending_orders:
                return

            # 2. Group by ticker to minimise price fetches
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

    def _get_provider(self):
        """Return a PriceProvider bound to the live runtime singletons. The bind
        is performed lazily and cached so the provider holds no stale references
        and is created only once the engine is actually wired into startup."""
        if self._provider is None:
            from angelone_service import angelone_service
            from aggregator import candle_aggregator

            self._provider = PriceProvider(
                angelone_service=angelone_service,
                candle_aggregator=candle_aggregator,
                session_factory=database.SessionLocal,
                now_fn=database.get_ist_now,
            )
        return self._provider

    async def get_current_prices(self, db: Session, tickers: List[str]) -> Dict[str, float]:
        """Get current prices for tickers. Delegates to the PriceProvider's
        four-tier resolution pipeline (live tick -> forming 5m -> completed 5m
        -> daily close when market closed). Public signature and return type are
        preserved: Dict[str, float]."""
        if not tickers:
            return {}
        provider = self._get_provider()
        snapshots = provider.get_prices(tickers)
        return {tkr: snap.price for tkr, snap in snapshots.items()}

    def get_price(self, ticker: str) -> Optional[float]:
        """Synchronous single-ticker price resolution for non-async call sites.
        Delegates to the same PriceProvider pipeline as get_current_prices so the
        provider remains the single source of truth for execution prices."""
        if not ticker:
            return None
        provider = self._get_provider()
        snapshot = provider.get_price(ticker)
        return snapshot.price if snapshot else None

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
        """Recover TP/SL orders stuck in EXECUTING status after a crash.

        A stuck order means the server crashed between marking the order
        EXECUTING and writing the final EXECUTED state.  We inspect the
        position:
          - Position CLOSED  → order execution completed before crash;
                               reconcile order to EXECUTED.
          - Position OPEN    → price still near trigger → retry execution;
                               otherwise reset to PENDING so the normal
                               check loop can re-evaluate it next cycle.
          - Position missing → orphaned order; reset to PENDING.
        """
        db = database.SessionLocal()
        try:
            self._reconcile_execution_subscriptions(db)
            stuck_orders = db.query(models.Order).filter(
                models.Order.status == "EXECUTING"
            ).all()
            if not stuck_orders:
                return

            print(f"[Engine] Recovering {len(stuck_orders)} stuck EXECUTING order(s)...")

            for order in stuck_orders:
                position = db.query(models.Position).filter(
                    models.Position.id == order.position_id
                ).first()

                if not position:
                    # Orphaned order — position row was deleted; mark as PENDING
                    # so the next check cycle can handle it cleanly.
                    print(f"[Engine] Stuck order {order.id}: position missing, resetting to PENDING")
                    order.status = "PENDING"
                    db.commit()
                    continue

                if position.status == "CLOSED":
                    # The position was successfully closed before the crash;
                    # just finalise the order status so the DB is consistent.
                    print(f"[Engine] Stuck order {order.id}: position already CLOSED, "
                          f"reconciling order to EXECUTED")
                    order.status = "EXECUTED"
                    if order.executed_at is None:
                        order.executed_at = database.get_ist_now()
                    db.commit()
                    continue

                # Position is still OPEN — the execution did NOT complete.
                # Try to re-execute if price is still near trigger, else reset.
                prices = await self.get_current_prices(db, [order.ticker])
                current_price = prices.get(order.ticker)
                if current_price is None:
                    print(f"[Engine] Stuck order {order.id}: no price available, resetting to PENDING")
                    order.status = "PENDING"
                    db.commit()
                    continue

                trigger_price = order.trigger_price
                if trigger_price and abs(current_price - trigger_price) / trigger_price <= 0.01:
                    print(f"[Engine] Stuck order {order.id}: retrying execution at ₹{current_price:.2f}")
                    await self.execute_order(db, order, current_price)
                else:
                    print(f"[Engine] Stuck order {order.id}: price ₹{current_price:.2f} moved away "
                          f"from trigger ₹{trigger_price:.2f}, resetting to PENDING")
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

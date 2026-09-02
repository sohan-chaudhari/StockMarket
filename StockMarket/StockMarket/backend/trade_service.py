"""
Trading Service - Core business logic for paper trading
Handles: Opening positions, closing positions, setting TP/SL, P&L calculations
"""
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional, Tuple, Dict, List
import models
from database import get_ist_now


class TradingService:
    """Encapsulates all trading business logic"""
    
    MAX_TP_SL_EDITS = 3
    
    @staticmethod
    def validate_balance(db: Session, user_id: int, required_amount: float) -> Tuple[bool, str]:
        """Check if user has sufficient balance"""
        user = db.query(models.User).filter(models.User.user_id == user_id).first()
        if not user:
            return False, "User not found"
        if user.virtual_balance < required_amount:
            return False, f"Insufficient balance. Required: ₹{required_amount:.2f}, Available: ₹{user.virtual_balance:.2f}"
        return True, "OK"
    
    @staticmethod
    def validate_tp_sl(position_type: str, entry_price: float, tp: Optional[float], sl: Optional[float]) -> Tuple[bool, str]:
        """Validate TP/SL bounds based on position type"""
        if position_type == "LONG":
            if tp is not None and tp <= entry_price:
                return False, "Take Profit must be above entry price for LONG positions"
            if sl is not None and sl >= entry_price:
                return False, "Stop Loss must be below entry price for LONG positions"
        elif position_type == "SHORT":
            if tp is not None and tp >= entry_price:
                return False, "Take Profit must be below entry price for SHORT positions"
            if sl is not None and sl <= entry_price:
                return False, "Stop Loss must be above entry price for SHORT positions"
        else:
            return False, f"Invalid position type: {position_type}. Must be LONG or SHORT."
        return True, "OK"
    
    @staticmethod
    def open_position(
        db: Session,
        user_id: int,
        ticker: str,
        position_type: str,
        quantity: int,
        entry_price: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        stock_name: Optional[str] = None
    ) -> Tuple[Optional[models.Position], str, Optional[models.Order]]:
        """
        Open a new trading position.
        Strictly rejects orders when the exchange is closed.
        """
        from exchange_calendar import nse_calendar
        now_ist = get_ist_now()
        is_open = nse_calendar.is_market_open(now_ist)

        if not is_open:
            next_day = nse_calendar.get_next_trading_day(now_ist.date())
            next_day_str = next_day.strftime("%A, %d %b %Y")
            return None, f"Market is closed. Trading is strictly disabled outside NSE market hours (09:15 - 15:30 IST). Please place your trade at 09:15 AM on {next_day_str}.", None

        # 1. Validate TP/SL if provided
        if take_profit is not None or stop_loss is not None:
            is_valid, msg = TradingService.validate_tp_sl(position_type, entry_price, take_profit, stop_loss)
            if not is_valid:
                return None, msg, None

        # ── LIVE MARKET ORDER EXECUTION ──
        total_investment = round(entry_price * quantity, 2)
        user = db.query(models.User).filter(models.User.user_id == user_id).with_for_update().first()
        if not user:
            db.rollback()
            return None, "User not found", None
        if user.virtual_balance < total_investment:
            db.rollback()
            return None, f"Insufficient balance. Required: ₹{total_investment:.2f}, Available: ₹{user.virtual_balance:.2f}", None

        try:
            user.virtual_balance -= total_investment
            new_balance = user.virtual_balance

            position = models.Position(
                user_id=user_id,
                ticker=ticker,
                stock_name=stock_name,
                position_type=position_type,
                quantity=quantity,
                entry_price=entry_price,
                total_investment=total_investment,
                take_profit=take_profit,
                stop_loss=stop_loss,
                status="OPEN"
            )
            db.add(position)
            db.flush()

            if take_profit is not None:
                tp_order = models.Order(
                    position_id=position.id,
                    user_id=user_id,
                    ticker=ticker,
                    order_type="TP",
                    trigger_price=take_profit,
                    status="PENDING"
                )
                db.add(tp_order)

            if stop_loss is not None:
                sl_order = models.Order(
                    position_id=position.id,
                    user_id=user_id,
                    ticker=ticker,
                    order_type="SL",
                    trigger_price=stop_loss,
                    status="PENDING"
                )
                db.add(sl_order)

            transaction = models.Transaction(
                user_id=user_id,
                position_id=position.id,
                ticker=ticker,
                stock_name=stock_name,
                transaction_type="OPEN",
                position_type=position_type,
                quantity=quantity,
                price=entry_price,
                amount=-total_investment,
                pnl=None,
                balance_after=new_balance
            )
            db.add(transaction)
            db.commit()
            db.refresh(position)
            return position, "Position opened successfully", None
        except Exception as e:
            db.rollback()
            return None, f"Failed to open position: {str(e)}", None

    @staticmethod
    def close_position(
        db: Session,
        user_id: int,
        position_id: int,
        closing_price: float,
        close_type: str = "MANUAL"
    ) -> Tuple[Optional[models.Position], str]:
        """
        Close an existing position.
        Strictly disabled when the exchange is closed.
        """
        from exchange_calendar import nse_calendar
        now_ist = get_ist_now()
        is_open = nse_calendar.is_market_open(now_ist)
        if not is_open:
            next_day = nse_calendar.get_next_trading_day(now_ist.date())
            next_day_str = next_day.strftime("%A, %d %b %Y")
            return None, f"Market is closed. Positions cannot be closed outside NSE trading hours (09:15 - 15:30 IST). Trading resumes at 09:15 AM on {next_day_str}."

        # 1. Get position (with lock to prevent race conditions)
        position = db.query(models.Position).filter(
            models.Position.id == position_id,
            models.Position.user_id == user_id,
            models.Position.status == "OPEN"
        ).with_for_update().first()
        
        if not position:
            return None, "Position not found or already closed"
        
        try:
            # 2. Calculate P&L
            if position.position_type == "LONG":
                pnl = (closing_price - position.entry_price) * position.quantity
            else:  # SHORT
                pnl = (position.entry_price - closing_price) * position.quantity
            
            # 3. Update user balance (return investment + P&L) and lock User row for update
            user = db.query(models.User).filter(models.User.user_id == user_id).with_for_update().first()
            if not user:
                return None, "User not found"
            credit_amount = position.total_investment + pnl
            new_balance = user.virtual_balance + credit_amount
            if new_balance < 0:
                # Prevent negative balance: cap loss at available balance
                credit_amount = -user.virtual_balance
                new_balance = 0
                pnl = credit_amount - position.total_investment
            user.virtual_balance = new_balance
            
            # 4. Update position
            position.closing_price = closing_price
            position.realized_pnl = pnl
            position.status = "CLOSED"
            position.close_type = close_type
            if close_type == "TP_EXECUTED":
                position.exit_reason = "TP_HIT"
            elif close_type == "SL_EXECUTED":
                position.exit_reason = "SL_HIT"
            else:
                position.exit_reason = "MANUAL_CLOSE"
            position.closed_at = get_ist_now()
            
            # 5. Cancel pending orders for this position
            db.query(models.Order).filter(
                models.Order.position_id == position_id,
                models.Order.status == "PENDING"
            ).update({"status": "CANCELLED"})
            
            # 6. Create transaction record
            transaction = models.Transaction(
                user_id=user_id,
                position_id=position.id,
                ticker=position.ticker,
                stock_name=position.stock_name,
                transaction_type=close_type,
                position_type=position.position_type,
                quantity=position.quantity,
                price=closing_price,
                amount=credit_amount,  # Positive = credit
                pnl=pnl,
                balance_after=new_balance
            )
            db.add(transaction)
            
            db.commit()
            db.refresh(position)
            
            return position, f"Position closed. P&L: ₹{pnl:.2f}"
            
        except Exception as e:
            db.rollback()
            return None, f"Failed to close position: {str(e)}"
    
    @staticmethod
    def set_limits(
        db: Session,
        user_id: int,
        position_id: int,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        Set or update TP/SL for a position.
        Strictly disabled when the exchange is closed.
        """
        from exchange_calendar import nse_calendar
        now_ist = get_ist_now()
        is_open = nse_calendar.is_market_open(now_ist)
        if not is_open:
            next_day = nse_calendar.get_next_trading_day(now_ist.date())
            next_day_str = next_day.strftime("%A, %d %b %Y")
            return False, f"Market is closed. Take Profit and Stop Loss cannot be modified outside NSE trading hours (09:15 - 15:30 IST). Modification resumes at 09:15 AM on {next_day_str}."

        # 1. Get position (with lock to prevent race conditions)
        position = db.query(models.Position).filter(
            models.Position.id == position_id,
            models.Position.user_id == user_id,
            models.Position.status == "OPEN"
        ).with_for_update().first()
        
        if not position:
            return False, "Position not found or already closed"
        
        # 2. Validate TP/SL
        # BUG-03 FIX: Use `is not None` — falsy check silently skips validation for TP/SL=0.0
        if take_profit is not None or stop_loss is not None:
            is_valid, msg = TradingService.validate_tp_sl(
                position.position_type, position.entry_price, take_profit, stop_loss
            )
            if not is_valid:
                return False, msg
        
        try:
            # 3. Handle Take Profit
            if take_profit is not None:
                if position.tp_edit_count >= TradingService.MAX_TP_SL_EDITS:
                    return False, "Maximum Edits Limit Reached"
                
                # Cancel existing TP order
                db.query(models.Order).filter(
                    models.Order.position_id == position_id,
                    models.Order.order_type == "TP",
                    models.Order.status == "PENDING"
                ).update({"status": "CANCELLED"})
                
                # Create new TP order
                tp_order = models.Order(
                    position_id=position.id,
                    user_id=user_id,
                    ticker=position.ticker,
                    order_type="TP",
                    trigger_price=take_profit,
                    status="PENDING"
                )
                db.add(tp_order)
                position.tp_edit_count += 1
            
            # 4. Handle Stop Loss
            if stop_loss is not None:
                if position.sl_edit_count >= TradingService.MAX_TP_SL_EDITS:
                    return False, "Maximum Edits Limit Reached"
                
                # Cancel existing SL order
                db.query(models.Order).filter(
                    models.Order.position_id == position_id,
                    models.Order.order_type == "SL",
                    models.Order.status == "PENDING"
                ).update({"status": "CANCELLED"})
                
                # Create new SL order
                sl_order = models.Order(
                    position_id=position.id,
                    user_id=user_id,
                    ticker=position.ticker,
                    order_type="SL",
                    trigger_price=stop_loss,
                    status="PENDING"
                )
                db.add(sl_order)
                position.sl_edit_count += 1
            
            db.commit()
            return True, "Limits updated successfully"
            
        except Exception as e:
            db.rollback()
            return False, f"Failed to update limits: {str(e)}"
    
    @staticmethod
    def get_open_positions(db: Session, user_id: int) -> list:
        """Get all open positions for a user"""
        return db.query(models.Position).filter(
            models.Position.user_id == user_id,
            models.Position.status == "OPEN"
        ).order_by(models.Position.created_at.desc()).all()
    
    @staticmethod
    def get_position_orders(db: Session, position_id: int) -> Dict[str, Optional[models.Order]]:
        """Get TP and SL orders for a position"""
        orders = db.query(models.Order).filter(
            models.Order.position_id == position_id,
            models.Order.status == "PENDING"
        ).all()

        result = {"TP": None, "SL": None}
        for order in orders:
            result[order.order_type] = order
        return result

    @staticmethod
    def get_orders_for_positions(db: Session, position_ids: List[int]) -> Dict[int, Dict[str, Optional[models.Order]]]:
        """Batched get_position_orders() for a list of positions in ONE query.

        DB-06: the open/closed-positions endpoints called get_position_orders
        once per position in a loop -- N positions meant N+1 queries. This
        fetches every position's pending TP/SL orders in a single IN(...)
        query and groups them in memory instead.
        """
        if not position_ids:
            return {}
        orders = db.query(models.Order).filter(
            models.Order.position_id.in_(position_ids),
            models.Order.status == "PENDING"
        ).all()

        result: Dict[int, Dict[str, Optional[models.Order]]] = {
            pid: {"TP": None, "SL": None} for pid in position_ids
        }
        for order in orders:
            bucket = result.get(order.position_id)
            if bucket is not None:
                bucket[order.order_type] = order
        return result
    
    @staticmethod
    def get_portfolio_summary(db: Session, user_id: int) -> dict:
        """Calculate portfolio summary statistics"""
        user = db.query(models.User).filter(models.User.user_id == user_id).first()
        if not user:
            return {}
        
        # Get positions
        open_positions = db.query(models.Position).filter(
            models.Position.user_id == user_id,
            models.Position.status == "OPEN"
        ).all()
        
        closed_positions = db.query(models.Position).filter(
            models.Position.user_id == user_id,
            models.Position.status == "CLOSED"
        ).all()
        
        # Calculate totals
        total_invested = sum(p.total_investment for p in open_positions)
        total_realized_pnl = sum(p.realized_pnl or 0 for p in closed_positions)
        
        # Win/Loss stats (breakeven excluded from denominator)
        wins = [p for p in closed_positions if (p.realized_pnl or 0) > 0]
        losses = [p for p in closed_positions if (p.realized_pnl or 0) < 0]
        total_decided = len(wins) + len(losses)
        win_rate = (len(wins) / total_decided * 100) if total_decided > 0 else 0
        
        return {
            "current_balance": user.virtual_balance,
            "total_invested": total_invested,
            "total_realized_pnl": total_realized_pnl,
            "total_unrealized_pnl": 0,  # Will be calculated with live prices
            "total_positions": len(open_positions) + len(closed_positions),
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "win_rate": round(win_rate, 2),
            "total_wins": len(wins),
            "total_losses": len(losses)
        }

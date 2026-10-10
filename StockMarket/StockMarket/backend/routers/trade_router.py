from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import List

import models, schemas, auth
from database import get_db
from trade_service import TradingService
from rate_limiter import limiter

router = APIRouter()

@router.post("/open", response_model=schemas.PositionResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("120/minute")
def open_position(
    request: Request,
    req: schemas.PlaceOrderRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # Look up stock name from metadata
    stock_name = None
    meta = db.query(models.StockMetadata).filter(
        models.StockMetadata.ticker == req.ticker
    ).first()
    if meta:
        stock_name = meta.name

    # TradingService.open_position() returns a 3-tuple (position, message,
    # amo_order). Unpacking only two values raised "too many values to unpack"
    # on every call to this legacy route (the live endpoint is
    # /api/trade/place-order). The AMO slot is intentionally unused here.
    # Server-authoritative entry price (same policy as /api/trade/place-order):
    # the client's req.entry_price never determines the executed price, and a
    # missing/stale market price is a clear rejection.
    from execution_engine import price_monitor
    entry_price = price_monitor.resolve_execution_price(req.ticker)
    if entry_price is None:
        raise HTTPException(
            status_code=409,
            detail=f"No fresh market price available for {req.ticker}; order not placed.",
        )

    position, error_msg, _amo_order = TradingService.open_position(
        db=db,
        user_id=current_user.user_id,
        ticker=req.ticker,
        stock_name=stock_name,
        position_type=req.position_type,
        quantity=req.quantity,
        entry_price=entry_price,
        take_profit=req.take_profit,
        stop_loss=req.stop_loss
    )
    
    if not position:
        raise HTTPException(status_code=400, detail=error_msg)
        
    return schemas.PositionResponse.from_orm(position)

@router.post("/close", response_model=schemas.PositionResponse)
@limiter.limit("120/minute")
def close_position(
    request: Request,
    req: schemas.ClosePositionRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # Server-authoritative exit price (same policy as /api/trade/close-position).
    pos = db.query(models.Position).filter(
        models.Position.id == req.position_id,
        models.Position.user_id == current_user.user_id,
        models.Position.status == "OPEN",
    ).first()
    closing_price = req.closing_price
    if pos:
        from execution_engine import price_monitor
        market_price = price_monitor.resolve_execution_price(pos.ticker)
        if market_price is None:
            raise HTTPException(
                status_code=409,
                detail=f"No fresh market price available for {pos.ticker}; position not closed.",
            )
        closing_price = market_price

    position, error_msg = TradingService.close_position(
        db=db,
        user_id=current_user.user_id,
        position_id=req.position_id,
        closing_price=closing_price,
        close_type="MANUAL"
    )
    
    if not position:
        raise HTTPException(status_code=400, detail=error_msg)
        
    return schemas.PositionResponse.from_orm(position)

@router.get("/positions", response_model=List[schemas.PositionResponse])
def get_positions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    positions = db.query(models.Position).filter(
        models.Position.user_id == current_user.user_id
    ).order_by(models.Position.created_at.desc()).all()
    return positions

@router.get("/history", response_model=List[schemas.TransactionResponse])
def get_transaction_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    transactions = db.query(models.Transaction).filter(
        models.Transaction.user_id == current_user.user_id
    ).order_by(models.Transaction.created_at.desc()).all()
    return transactions

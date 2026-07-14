from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

import models, schemas, auth
from database import get_db
from trade_service import TradingService

router = APIRouter()

@router.post("/open", response_model=schemas.PositionResponse, status_code=status.HTTP_201_CREATED)
def open_position(
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

    position, error_msg = TradingService.open_position(
        db=db,
        user_id=current_user.user_id,
        ticker=req.ticker,
        stock_name=stock_name,
        position_type=req.position_type,
        quantity=req.quantity,
        entry_price=req.entry_price,
        take_profit=req.take_profit,
        stop_loss=req.stop_loss
    )
    
    if not position:
        raise HTTPException(status_code=400, detail=error_msg)
        
    return schemas.PositionResponse.from_orm(position)

@router.post("/close", response_model=schemas.PositionResponse)
def close_position(
    req: schemas.ClosePositionRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    position, error_msg = TradingService.close_position(
        db=db,
        user_id=current_user.user_id,
        position_id=req.position_id,
        closing_price=req.closing_price,
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

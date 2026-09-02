from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import time
import threading

import models, schemas, auth
from database import get_db, get_ist_now
from rate_limiter import limiter

router = APIRouter()

# Simple in-memory OTP rate limiter
_otp_attempts = {}
_otp_lock = threading.Lock()
_OTP_MAX_ATTEMPTS = 10
_OTP_WINDOW = 300  # 5 minutes

@router.get("/me", response_model=schemas.UserResponse)
async def get_profile(current_user: models.User = Depends(auth.get_current_user)):
    return current_user

@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
def register(request: Request, user: schemas.UserRegister, db: Session = Depends(get_db)):
    # Check if email exists
    existing = db.query(models.User).filter(models.User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    # Validate password
    is_valid, msg = auth.validate_password(user.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=msg)
        
    hashed_pwd = auth.get_password_hash(user.password)
    
    new_user = models.User(
        email=user.email,
        password_hash=hashed_pwd,
        full_name=user.full_name,
        is_verified=False
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Generate OTP & Token
    otp = auth.generate_otp()
    token_jti = auth.generate_token()
    
    verification = models.VerificationToken(
        user_id=new_user.user_id,
        token_hash=token_jti,
        otp_code=otp,
        token_type="email_verification",
        expires_at=get_ist_now() + timedelta(hours=24)
    )
    db.add(verification)
    db.commit()
    
    # Send email
    auth.send_verification_email(new_user.email, otp, new_user.full_name, token_jti)
    
    return {"message": "User registered. Please verify your email.", "user_id": new_user.user_id}

@router.post("/login", response_model=schemas.TokenResponse)
@limiter.limit("20/hour")
def login(creds: schemas.UserLogin, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == creds.email).with_for_update().first()
    
    if user:
        is_locked, lock_msg = auth.check_account_locked(user)
        if is_locked:
            raise HTTPException(status_code=403, detail=lock_msg)
            
    if not user or not auth.verify_password(creds.password, user.password_hash):
        if user:
            auth.handle_failed_login(db, user)
        auth.log_login_attempt(db, creds.email, False, request, "Invalid credentials")
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    if not user.is_active:
        auth.log_login_attempt(db, creds.email, False, request, "Account inactive")
        raise HTTPException(status_code=401, detail="Account is disabled")

    # Success
    auth.reset_failed_attempts(db, user)
    auth.log_login_attempt(db, creds.email, True, request)
    
    token = auth.create_access_token(data={"sub": str(user.user_id), "user_id": user.user_id, "email": user.email})
    
    return schemas.TokenResponse(
        access_token=token,
        token_type="bearer",
        user=schemas.UserResponse.from_orm(user)
    )

@router.post("/verify-email")
def verify_email(req: schemas.VerifyEmailRequest, request: Request, db: Session = Depends(get_db)):
    # Rate limit OTP attempts per IP
    ip = request.client.host if request.client else "unknown"
    now_ts = time.time()
    with _otp_lock:
        if ip not in _otp_attempts:
            _otp_attempts[ip] = []
        _otp_attempts[ip] = [t for t in _otp_attempts[ip] if now_ts - t < _OTP_WINDOW]
        if not _otp_attempts[ip]:
            del _otp_attempts[ip]
        if ip in _otp_attempts and len(_otp_attempts[ip]) >= _OTP_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many verification attempts. Please try again later.")
        _otp_attempts.setdefault(ip, []).append(now_ts)

    if req.otp:
        token_record = db.query(models.VerificationToken).filter(
            models.VerificationToken.otp_code == req.otp,
            models.VerificationToken.token_type == "email_verification",
            models.VerificationToken.used == False,
            models.VerificationToken.expires_at > get_ist_now()
        ).first()
    elif req.token:
        token_record = db.query(models.VerificationToken).filter(
            models.VerificationToken.token_hash == req.token,
            models.VerificationToken.token_type == "email_verification",
            models.VerificationToken.used == False,
            models.VerificationToken.expires_at > get_ist_now()
        ).first()
    else:
        raise HTTPException(status_code=400, detail="Must provide OTP or token")

    if not token_record:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
        
    user = db.query(models.User).filter(models.User.user_id == token_record.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    user.is_verified = True
    token_record.used = True
    db.commit()
    
    auth.send_welcome_email(user.email, user.full_name)
    return {"message": "Email successfully verified"}

@router.post("/resend-verification")
@limiter.limit("3/hour")
def resend_verification(req: schemas.ResendVerificationRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == req.email).first()
    if not user:
        return {"message": "If that email is registered, a new verification code has been sent."}
    if user.is_verified:
        return {"message": "Email is already verified. You can sign in."}

    otp = auth.generate_otp()
    token_jti = auth.generate_token()
    verification = models.VerificationToken(
        user_id=user.user_id,
        token_hash=token_jti,
        otp_code=otp,
        token_type="email_verification",
        expires_at=get_ist_now() + timedelta(hours=24)
    )
    db.add(verification)
    db.commit()
    auth.send_verification_email(user.email, otp, user.full_name, token_jti)
    return {"message": "A new verification code has been sent to your email."}

@router.post("/forgot-password")
@limiter.limit("5/hour")
def forgot_password(req: schemas.ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == req.email).first()
    if user:
        token_jti = auth.generate_token()
        verification = models.VerificationToken(
            user_id=user.user_id,
            token_hash=token_jti,
            token_type="password_reset",
            expires_at=get_ist_now() + timedelta(hours=1)
        )
        db.add(verification)
        db.commit()
        
        reset_link = f"{auth.FRONTEND_URL}/reset-password.html?token={token_jti}"
        html = f"<p>Reset link: <a href='{reset_link}'>{reset_link}</a></p>"
        auth.send_email(user.email, "Password Reset Request", html)
        
    return {"message": "If that email is registered, a reset link has been sent."}

@router.post("/reset-password")
def reset_password(req: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    token_record = db.query(models.VerificationToken).filter(
        models.VerificationToken.token_hash == req.token,
        models.VerificationToken.token_type == "password_reset",
        models.VerificationToken.used == False,
        models.VerificationToken.expires_at > get_ist_now()
    ).with_for_update().first()
    
    if not token_record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")
        
    user = db.query(models.User).filter(models.User.user_id == token_record.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    is_valid, msg = auth.validate_password(req.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=msg)
        
    user.password_hash = auth.get_password_hash(req.new_password)
    token_record.used = True
    db.commit()
    
    return {"message": "Password updated successfully."}

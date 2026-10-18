from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
import time
import threading

import models, schemas, auth
from database import get_db, get_ist_now
from rate_limiter import limiter

router = APIRouter()

# SEC-01: the browser must never receive a JS-readable JWT; the HttpOnly
# session cookie is its credential. Non-browser API clients that legitimately
# need bearer auth opt in with `X-Auth-Mode: bearer`.
_BODY_TOKEN_HEADER = "X-Auth-Mode"
_BODY_TOKEN_VALUE = "bearer"

# SEC-01 follow-up: `X-Auth-Mode` is a *request* header, not a trust boundary --
# any same-origin page script (including injected/XSS) can set it, so it alone
# cannot prove a request came from a non-browser client. Browsers, however,
# always attach Fetch-metadata headers that page JavaScript is forbidden from
# setting or removing (Fetch spec "forbidden header names"):
#   * Sec-Fetch-Site / Sec-Fetch-Mode -- sent on every request by all modern
#     browsers; script cannot add, change or suppress them.
#   * Origin  -- sent by browsers on every non-GET/HEAD request (so on every
#     login POST) and on every cross-origin request; script cannot forge it.
#   * Referer -- browser-set navigation context (suppressible via referrer
#     policy, hence a secondary signal only).
# The presence of any of these marks a browser-origin request. A normal
# non-browser API client (curl, requests, Postman, server SDK) sends none of
# them. This fails closed: a machine client that deliberately mimics a browser
# is treated as a browser and gets no JWT in the body.
_BROWSER_FETCH_HEADERS = ("sec-fetch-site", "sec-fetch-mode", "origin", "referer")


def _is_browser_request(request: Request) -> bool:
    """True when browser-controlled Fetch-metadata headers are present.

    Page JS cannot set or strip these, so a browser-origin request is always
    classified as such even when it also sends the `X-Auth-Mode` opt-in."""
    try:
        headers = request.headers
        return any(headers.get(name) for name in _BROWSER_FETCH_HEADERS)
    except Exception:
        # If the request cannot be inspected, fail closed (no body token).
        return True


def _client_wants_body_token(request: Request) -> bool:
    """True only when a NON-BROWSER client explicitly asks for a bearer token
    in the response body.

    Requires the `X-Auth-Mode: bearer` opt-in AND the absence of any
    browser-controlled header. The custom header alone is deliberately not
    sufficient, because any page script can send it."""
    try:
        opted_in = request.headers.get(_BODY_TOKEN_HEADER, "").strip().lower() == _BODY_TOKEN_VALUE
    except Exception:
        return False
    return opted_in and not _is_browser_request(request)


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
    # HARDEN-03: flush (not commit) -- assigns new_user.user_id via the DB
    # sequence, which the verification token below needs as its FK, without
    # ending the transaction. Previously this committed here AND separately
    # after adding the verification token: if that second commit failed, a
    # User row was left permanently persisted with no verification token
    # (recoverable via /resend-verification, but not transactionally clean).
    # Now both rows are one atomic unit -- either both persist or neither does.
    db.flush()

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
    db.refresh(new_user)

    # Send email
    auth.send_verification_email(new_user.email, otp, new_user.full_name, token_jti)
    
    return {"message": "User registered. Please verify your email.", "user_id": new_user.user_id}

@router.post("/login", response_model=schemas.TokenResponse)
@limiter.limit("20/hour")
def login(creds: schemas.UserLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == creds.email).with_for_update().first()
    
    # SEC-03: the account lock exists to stop GUESSING, not to deny the account
    # owner. Verify the password FIRST so a correct password always signs in --
    # otherwise anyone who knows the email could lock the real user out on demand
    # by burning the failed-attempt budget (the previous order checked the lock
    # before the password, so 5 anonymous failures denied the owner for 30 min).
    # A WRONG password is still rejected while locked, and does not extend the
    # lock, so the 5-attempts / 30-minutes brute-force ceiling is unchanged.
    password_ok = bool(user) and auth.verify_password(creds.password, user.password_hash)
            
    if not password_ok:
        if user:
            is_locked, lock_msg = auth.check_account_locked(user)
            if is_locked:
                raise HTTPException(status_code=403, detail=lock_msg)
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

    # SEC-01: the HttpOnly session cookie set below is the browser credential.
    # The JWT is only returned to a non-browser client that opts in with
    # `X-Auth-Mode: bearer`; browser-origin requests are excluded even when they
    # send that header (see _client_wants_body_token).
    auth.set_auth_cookie(response, token, request)

    return schemas.TokenResponse(
        access_token=token if _client_wants_body_token(request) else None,
        token_type="bearer",
        user=schemas.UserResponse.from_orm(user)
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db),
):
    """Invalidate the current session: blacklist its JWT id and clear the
    HttpOnly session cookie. Idempotent and safe to call with a stale token."""
    token = None
    for candidate in auth.iter_request_tokens(request, credentials):
        token = candidate
        break
    if token:
        payload = auth.decode_token(token)
        jti = payload.get("jti") if payload else None
        if jti:
            auth.blacklist_token(db, jti)
    auth.clear_auth_cookie(response)
    return {"message": "Logged out"}

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
@limiter.limit("5/hour")
def reset_password(req: schemas.ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
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

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError, jwt
import os
import re
import secrets
import string
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import bcrypt

import models
from database import get_db, get_ist_now

load_dotenv()

# Configuration
# BUG-11 FIX: Raise RuntimeError if JWT_SECRET_KEY is missing.
# JWT-SECRET-VALIDATION: extended so a *present* secret can still be
# rejected -- "not empty" alone doesn't stop an operator from copying
# .env.example verbatim (its placeholder is 40 chars, long enough to pass
# a naive length check on its own) or from setting a trivially short value.
# Order: missing -> whitespace -> known placeholder -> minimum length.
# Whitespace is checked before the placeholder/length comparisons and is
# REJECTED, not silently stripped -- trimming a security credential here
# would mean the effective signing key silently differs from whatever an
# operator (or a secrets manager) believes was configured.
_JWT_SECRET_KEY_PLACEHOLDER = "change_me_to_a_random_64_char_hex_string"  # from .env.example (identical in every copy in this repo)
_JWT_SECRET_KEY_MIN_LENGTH = 32  # no prior convention existed; this phase's own minimum, per its instructions

_SECRET_KEY_RAW = os.getenv("JWT_SECRET_KEY", "")
if not _SECRET_KEY_RAW:
    raise RuntimeError("JWT_SECRET_KEY environment variable is not set! Application cannot run in unsafe mode.")
if _SECRET_KEY_RAW != _SECRET_KEY_RAW.strip():
    raise RuntimeError(
        "JWT_SECRET_KEY has leading/trailing whitespace, which would silently "
        "change the effective signing key. Fix the value at its source rather "
        "than relying on this check to trim it."
    )
if _SECRET_KEY_RAW == _JWT_SECRET_KEY_PLACEHOLDER:
    raise RuntimeError(
        "JWT_SECRET_KEY must not use the example placeholder from .env.example. "
        "Generate a real secret: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
if len(_SECRET_KEY_RAW) < _JWT_SECRET_KEY_MIN_LENGTH:
    raise RuntimeError(
        f"JWT_SECRET_KEY must be at least {_JWT_SECRET_KEY_MIN_LENGTH} characters "
        f"(got {len(_SECRET_KEY_RAW)})."
    )
SECRET_KEY = _SECRET_KEY_RAW
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Bearer token scheme. auto_error=False so a missing/malformed Authorization
# header does not short-circuit before the HttpOnly session cookie is tried
# (BUG-2: a browser that was closed and reopened has the cookie but no
# JS-readable token).
security = HTTPBearer(auto_error=False)

# ==================== PERSISTENT SESSION COOKIE (BUG-2) ====================
# The JWT is stored in an HttpOnly cookie so a user who closes/reopens the
# browser (or the tab) stays signed in for the configured token lifetime.
# The cookie is the ONLY persisted credential — it is never exposed to JS.
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "leverage_token")
ACCESS_TOKEN_EXPIRE_SECONDS = ACCESS_TOKEN_EXPIRE_HOURS * 3600


def _cookie_secure(request: Optional[Request] = None) -> bool:
    """Secure flag: explicit env override, else true for HTTPS requests.
    A Secure cookie is silently dropped over plain HTTP, which would break
    local development, so it is only set when the transport is provably TLS."""
    env = os.getenv("AUTH_COOKIE_SECURE", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    try:
        return bool(request is not None and request.url.scheme == "https")
    except Exception:
        return False


def set_auth_cookie(response, token: str, request: Optional[Request] = None) -> None:
    """Persist the access token as an HttpOnly, SameSite=Lax cookie."""
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        max_age=ACCESS_TOKEN_EXPIRE_SECONDS,
        path="/",
    )


def clear_auth_cookie(response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def iter_request_tokens(request: Optional[Request],
                        credentials: Optional[HTTPAuthorizationCredentials]):
    """Yield candidate JWT strings in priority order — Authorization header
    first (kept for API clients/tests), then the HttpOnly session cookie.
    Placeholder strings are never yielded."""
    seen = set()
    if credentials is not None and credentials.credentials:
        c = credentials.credentials.strip()
        if c and c.lower() not in ("null", "undefined") and c not in seen:
            seen.add(c)
            yield c
    if request is not None:
        c = request.cookies.get(AUTH_COOKIE_NAME)
        if c and c not in seen:
            yield c

# ==================== LOGGING SETUP ====================
if not os.path.exists('logs'):
    os.makedirs('logs')

auth_logger = logging.getLogger("auth_logger")
auth_logger.setLevel(logging.INFO)
handler = RotatingFileHandler("logs/auth.log", maxBytes=10240, backupCount=10)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
auth_logger.addHandler(handler)

# ==================== PASSWORD VALIDATION ====================

def validate_password(password: str) -> Tuple[bool, str]:
    """
    Validate password strength.
    Requirements: 12+ chars, uppercase, lowercase, digit, special char
    """
    if len(password) < 12:
        return False, "Password must be at least 12 characters long"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one number"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain at least one special character (!@#$%^&*(),.?\":{}|<>)"
    return True, "Password is valid"


# ==================== TOKEN GENERATION ====================

def generate_token(length: int = 32) -> str:
    """Generate a secure URL-safe token."""
    return secrets.token_urlsafe(length)


def generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return ''.join(secrets.choice(string.digits) for _ in range(6))


# ==================== PASSWORD HASHING (Native Bcrypt) ====================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password using native bcrypt."""
    try:
        # Bcrypt handles the 72-byte limit internally (ignores extra bytes)
        return bcrypt.checkpw(
            plain_password.encode('utf-8'), 
            hashed_password.encode('utf-8')
        )
    except Exception as e:
        print(f"[AUTH] Verification error: {e}")
        return False


def get_password_hash(password: str) -> str:
    """Hash a password using native bcrypt."""
    # salt = bcrypt.gensalt()
    # hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    # return hashed.decode('utf-8')
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


# ==================== JWT TOKEN ====================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token with jti for blacklisting.

    exp/iat MUST be true UTC (RFC 7519 NumericDate) -- this is independent of
    the app's separate, correct convention of storing candle timestamps as
    IST-naive. python-jose encodes a naive datetime by calling
    utctimetuple() on it, i.e. it treats the naive value AS ALREADY UTC.
    Passing get_ist_now() (IST wall-clock) here silently shifted every
    token's real exp/iat by +5:30 relative to true UTC 'now' -- tokens lived
    ~29.5h instead of the intended 24h, and a token built with a negative
    expires_delta to simulate "already expired" didn't actually verify as
    expired for another ~5.5 hours. Confirmed via test_auth.py.
    """
    to_encode = data.copy()
    now_utc = datetime.now(timezone.utc)
    if expires_delta:
        expire = now_utc + expires_delta
    else:
        expire = now_utc + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)

    # Add jti (JWT ID) for blacklisting
    jti = secrets.token_urlsafe(32)
    to_encode.update({
        "exp": expire,
        "iat": now_utc,
        "jti": jti
    })
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


# ==================== AUDIT LOGGING ====================

def log_login_attempt(
    db: Session, 
    email: str, 
    success: bool, 
    request: Request,
    failure_reason: Optional[str] = None
):
    """Log a login attempt for security auditing."""
    try:
        # DB Log
        attempt = models.LoginAttempt(
            email=email,
            ip_address=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", "")[:500],
            success=success,
            failure_reason=failure_reason
        )
        db.add(attempt)
        db.commit()
        
        # File Log (New)
        status = "SUCCESS" if success else "FAILURE"
        log_msg = f"Login {status} for {email} | IP: {attempt.ip_address} | Reason: {failure_reason or 'None'}"
        auth_logger.info(log_msg)
        
    except Exception as e:
        print(f"[AUTH] Failed to log login attempt: {e}")
        auth_logger.error(f"Failed to log login attempt for {email}: {e}")


# ==================== ACCOUNT LOCKOUT ====================

def check_account_locked(user: models.User) -> Tuple[bool, Optional[str]]:
    """Check if account is locked. Returns (is_locked, message)."""
    if user.locked_until and user.locked_until > get_ist_now():
        remaining = (user.locked_until - get_ist_now()).seconds // 60
        return True, f"Account is temporarily locked. Try again in {remaining} minutes."
    return False, None


def handle_failed_login(db: Session, user: models.User) -> int:
    """Increment failed attempts and lock if threshold reached. Returns remaining attempts."""
    user.failed_login_attempts += 1
    
    if user.failed_login_attempts >= 5:
        user.locked_until = get_ist_now() + timedelta(minutes=30)
        db.commit()
        return 0
    
    db.commit()
    return 5 - user.failed_login_attempts


def reset_failed_attempts(db: Session, user: models.User):
    """Reset failed login attempts on successful login."""
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = get_ist_now()
    db.commit()


# ==================== JWT BLACKLIST ====================

def is_token_blacklisted(db: Session, jti: str) -> bool:
    """Check if a JWT token is blacklisted."""
    return db.query(models.TokenBlacklist).filter(
        models.TokenBlacklist.token_jti == jti
    ).first() is not None


def blacklist_token(db: Session, jti: str):
    """Add a token to the blacklist. Idempotent, so calling logout twice (or
    a client retry) cannot violate the token_jti unique constraint."""
    if not jti:
        return
    if is_token_blacklisted(db, jti):
        return
    try:
        db.add(models.TokenBlacklist(token_jti=jti))
        db.commit()
    except Exception:
        db.rollback()


# ==================== EMAIL SENDING ====================

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USERNAME", "")  # Match .env Key
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # App password

def send_email(to_email: str, subject: str, html_content: str):
    """
    Send an email in a background daemon thread.
    BUG-06 FIX: The original version used blocking smtplib directly, freezing the async
    event loop for 1-5 seconds on every email send. Running in a thread keeps the server
    responsive. daemon=True ensures the thread is cleaned up if the server exits.
    """
    import threading

    def _send_in_thread():
        if not SMTP_USER or not SMTP_PASSWORD:
            print(f"[AUTH Mock Email] To: {to_email} | Subject: {subject}")
            return
        try:
            msg = MIMEMultipart()
            msg['From'] = f"Leverage Corporation <{SMTP_USER}>"
            msg['To'] = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(html_content, 'html'))
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
            print(f"[AUTH] Email sent to {to_email}")
        except Exception as e:
            print(f"[AUTH] Failed to send email to {to_email}: {e}")

    thread = threading.Thread(target=_send_in_thread, daemon=True)
    thread.start()



# Consolidating email logic to avoid duplicates
# Check for FRONTEND_URL
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:8000")

# Wait, I should do this properly.
# Legacy `send_verification_email(email, name, token, otp)`
# My current `send_verification_email(email, otp)`
# I will update `auth.py` to accept `name` optionally, and update `main.py` to pass it.

def send_verification_email(email: str, otp: str, name: str = "Trader", token: str = None):
    """Send verification OTP email with instant link (Matched to Screenshot)."""
    verification_link = f"{FRONTEND_URL}/verify-email.html?email={email}"
    if token:
        verification_link += f"&token={token}"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #000000; color: #ffffff; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #0a0a0a; border: 1px solid #333333; border-radius: 12px; padding: 40px; }}
            .logo {{ text-align: center; margin-bottom: 30px; }}
            .logo h1 {{ color: #ffffff; font-size: 28px; margin: 0; display: flex; align-items: center; justify-content: center; gap: 10px; }}
            .content {{ color: #e0e0e0; line-height: 1.6; }}
            .button-wrapper {{ text-align: center; margin: 30px 0; }}
            .button {{ display: inline-block; background-color: #ffffff; color: #000000; padding: 14px 30px; text-decoration: none; border-radius: 8px; font-weight: 700; }}
            .otp-box {{ background-color: #1a1a1a; border: 1px solid #333333; padding: 30px; text-align: center; margin: 25px 0; border-radius: 12px; }}
            .otp-code {{ font-size: 32px; letter-spacing: 10px; font-weight: 700; color: #ffffff; margin-bottom: 10px; font-family: 'Courier New', Courier, monospace; }}
            h2 {{ color: #ffffff; margin-top: 0; font-size: 24px; }}
            h3 {{ color: #ffffff; font-size: 18px; margin-top: 30px; margin-bottom: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <h1>🛡️ Leverage Corporation</h1>
            </div>
            <div class="content">
                <h2>Verify Your Email Address</h2>
                <p>Hello {name},</p>
                <p>Thank you for signing up with Leverage Corporation! To complete your registration, please verify your email address.</p>
                
                <h3>Option 1: Click the Verification Link</h3>
                <div class="button-wrapper">
                    <a href="{verification_link}" class="button">Verify Email Address</a>
                </div>
                
                <p style="text-align: center; color: #666; font-weight: bold; margin: 20px 0;">OR</p>
                
                <h3>Option 2: Enter this Verification Code</h3>
                <div class="otp-box">
                    <div class="otp-code">{otp}</div>
                    <p style="margin: 0; color: #666; font-size: 13px;">This code expires in 24 hours</p>
                </div>
                
                <hr style="border: 0; border-top: 1px solid #333333; margin: 40px 0 20px 0;">
                
                <div style="color: #6b7280; font-size: 12px; text-align: center;">
                    <p style="margin: 5px 0;">© 2026 Leverage Corporation. All rights reserved.</p>
                    <p style="margin: 5px 0;">This is an automated message, please do not reply.</p>
                </div>
                <div style="display: none; max-height: 0px; overflow: hidden; font-size: 1px; color: #0a0a0a;">ID: {datetime.now().timestamp()}</div>
            </div>
        </div>
    </body>
    </html>
    """
    send_email(email, "Verify Your Email - Leverage Corporation", html_content)


def send_welcome_email(email: str, name: Optional[str] = None):
    """Send welcome email after successful verification (Matched to Screenshot)."""
    display_name = name or email.split('@')[0]
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #000000; color: #ffffff; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #0a0a0a; border: 1px solid #333333; border-radius: 12px; padding: 40px; }}
            .logo {{ text-align: center; margin-bottom: 30px; }}
            .logo h1 {{ color: #ffffff; font-size: 28px; margin: 0; }}
            .content {{ color: #e0e0e0; line-height: 1.6; }}
            .highlight-box {{ background-color: #1a1a1a; border: 1px solid #333333; padding: 25px; margin: 25px 0; border-radius: 8px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <h1>🚀 Leverage Corporation</h1>
            </div>
            <div class="content">
                <h2 style="color: #ffffff;">Welcome to Leverage Corporation!</h2>
                <p>Dear {display_name},</p>
                <p>We're thrilled to have you join our community! Your account has been successfully verified and you're all set to explore our platform.</p>
                
                <div class="highlight-box">
                    <p style="margin: 0 0 10px 0; font-weight: bold; color: #ffffff;">We're committed to providing you with:</p>
                    <ul style="margin: 0; padding-left: 20px;">
                        <li>Exceptional service and support</li>
                        <li>Innovative solutions for your needs</li>
                        <li>A secure and professional platform</li>
                    </ul>
                </div>
                
                <p>If you have any questions or need assistance, our support team is always here to help.</p>
                
                <p>Best regards,<br><strong>The Leverage Corporation Team</strong></p>
                
                <hr style="border: 0; border-top: 1px solid #333333; margin: 40px 0 20px 0;">
                
                <div style="color: #6b7280; font-size: 12px; text-align: center;">
                    <p style="margin: 5px 0;">© 2026 Leverage Corporation. All rights reserved.</p>
                    <p style="margin: 5px 0;">This is an automated message, please do not reply.</p>
                </div>
                <div style="display: none; max-height: 0px; overflow: hidden; font-size: 1px; color: #0a0a0a;">ID: {datetime.now().timestamp()}</div>
            </div>
        </div>
    </body>
    </html>
    """
    send_email(email, "Welcome to Leverage Corporation 🚀", html_content)


def send_account_deleted_email(email: str, name: Optional[str] = None):
    """Send account deletion confirmation email (Matched to Screenshot)."""
    display_name = name or email.split('@')[0]
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #000000; color: #ffffff; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #0a0a0a; border: 1px solid #333333; border-radius: 12px; padding: 40px; }}
            .logo {{ text-align: center; margin-bottom: 30px; }}
            .logo h1 {{ color: #ffffff; font-size: 28px; margin: 0; }}
            .content {{ color: #e0e0e0; line-height: 1.6; }}
            .sad-box {{ background-color: #1a1a1a; border: 1px solid #333333; padding: 25px; margin: 25px 0; border-radius: 8px; }}
            .grey-text {{ color: #9ca3af; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <h1>👋 Leverage Corporation</h1>
            </div>
            <div class="content">
                <h2 style="color: #ffffff; margin-top: 0;">Account Deleted</h2>
                <p>Dear {display_name},</p>
                <p>Your account has been successfully deleted from Leverage Corporation. All your data and credentials have been permanently removed from our systems.</p>
                
                <div class="sad-box">
                    <p style="margin: 0 0 10px 0; font-weight: bold; color: #facc15;">We're sad to see you go! 😢</p>
                    <p style="margin: 0; font-size: 14px; color: #d1d5db;">If you ever change your mind, we'd love to have you back. You can always create a new account at any time.</p>
                </div>
                
                <p>Thank you for being a part of Leverage Corporation. We wish you all the best in your future endeavors!</p>
                
                <h3 style="color: #ffffff; margin-bottom: 5px; margin-top: 30px;">Goodbye and see you again! 👋</h3>
                <p>Warm regards,<br><strong style="color: #fca5a5;">The Leverage Corporation Team</strong></p>
                
                <hr style="border: 0; border-top: 1px solid #333333; margin: 40px 0 20px 0;">
                
                <div style="color: #6b7280; font-size: 12px; text-align: center;">
                    <p style="margin: 5px 0;">© 2026 Leverage Corporation. All rights reserved.</p>
                    <p style="margin: 5px 0;">This is an automated message, please do not reply.</p>
                </div>
                <div style="display: none; max-height: 0px; overflow: hidden; font-size: 1px; color: #0a0a0a;">ID: {datetime.now().timestamp()}</div>
            </div>
        </div>
    </body>
    </html>
    """
    send_email(email, "Goodbye from Leverage Corporation 👋", html_content)


def send_password_reset_email(email: str, token: str, name: Optional[str] = None):
    """Send password reset link (Consistent Design)."""
    display_name = name or "Trader"
    reset_link = f"{FRONTEND_URL}/reset-password.html?token={token}"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #000000; color: #ffffff; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #0a0a0a; border: 1px solid #333333; border-radius: 12px; padding: 40px; }}
            .logo {{ text-align: center; margin-bottom: 30px; }}
            .logo h1 {{ color: #ffffff; font-size: 28px; margin: 0; }}
            .content {{ color: #e0e0e0; line-height: 1.6; }}
            .button-wrapper {{ text-align: center; margin: 30px 0; }}
            .button {{ display: inline-block; background-color: #ffffff; color: #000000; padding: 14px 30px; text-decoration: none; border-radius: 8px; font-weight: 700; }}
            .warning {{ background-color: #2a1a1a; border-left: 4px solid #ff4444; padding: 15px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <h1>🔐 Leverage Corporation</h1>
            </div>
            <div class="content">
                <h2 style="color: #ffffff;">Password Reset Request</h2>
                <p>Hello {display_name},</p>
                <p>We received a request to reset your password. Click the button below to create a new password:</p>
                
                <div class="button-wrapper">
                    <a href="{reset_link}" class="button">Reset Password</a>
                </div>
                
                <div class="warning">
                    <p style="margin: 0; color: #ffffff;"><strong>⚠️ Security Notice:</strong></p>
                    <p style="margin: 5px 0 0 0;">This link will expire in 1 hour. If you didn't request a password reset, please ignore this email and ensure your account is secure.</p>
                </div>
                
                <hr style="border: 0; border-top: 1px solid #333333; margin: 40px 0 20px 0;">
                
                <div style="color: #6b7280; font-size: 12px; text-align: center;">
                    <p style="margin: 5px 0;">© 2026 Leverage Corporation. All rights reserved.</p>
                    <p style="margin: 5px 0;">This is an automated message, please do not reply.</p>
                </div>
                <div style="display: none; max-height: 0px; overflow: hidden; font-size: 1px; color: #0a0a0a;">ID: {datetime.now().timestamp()}</div>
            </div>
        </div>
    </body>
    </html>
    """
    send_email(email, "Password Reset Request - Leverage Corporation", html_content)


def send_password_reset_confirmation_email(email: str, name: Optional[str] = None):
    """Send password reset confirmation email."""
    display_name = name or "Trader"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #000000; color: #ffffff; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 40px auto; background-color: #0a0a0a; border: 1px solid #333333; border-radius: 12px; padding: 40px; }}
            .logo {{ text-align: center; margin-bottom: 30px; }}
            .logo h1 {{ color: #ffffff; font-size: 28px; margin: 0; }}
            .content {{ color: #e0e0e0; line-height: 1.6; }}
            .success-box {{ background-color: #0d2818; border-left: 4px solid #2ed573; padding: 15px; margin: 20px 0; }}
            .button-wrapper {{ text-align: center; margin: 30px 0; }}
            .button {{ display: inline-block; background: linear-gradient(135deg, #2ed573 0%, #1cb859 100%); color: #000000; padding: 14px 30px; text-decoration: none; border-radius: 8px; font-weight: 700; }}
            .warning {{ background-color: #2a1a1a; border-left: 4px solid #ff4444; padding: 15px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <h1>✅ Leverage Corporation</h1>
            </div>
            <div class="content">
                <h2 style="color: #2ed573;">Password Successfully Reset</h2>
                <p>Hello {display_name},</p>
                <p>Your password has been successfully updated. You can now sign in to your account with your new password.</p>
                
                <div class="success-box">
                    <p style="margin: 0; color: #2ed573;"><strong>✓ Password Changed</strong></p>
                    <p style="margin: 5px 0 0 0;">Your account is now secured with your new password.</p>
                </div>
                
                <div class="button-wrapper">
                    <a href="{FRONTEND_URL}/login.html" class="button">Sign In Now</a>
                </div>
                
                <div class="warning">
                    <p style="margin: 0; color: #ffffff;"><strong>⚠️ Wasn't You?</strong></p>
                    <p style="margin: 5px 0 0 0;">If you did not make this change, please contact our support team immediately and secure your account.</p>
                </div>
                
                <hr style="border: 0; border-top: 1px solid #333333; margin: 40px 0 20px 0;">
                
                <div style="color: #6b7280; font-size: 12px; text-align: center;">
                    <p style="margin: 5px 0;">© 2026 Leverage Corporation. All rights reserved.</p>
                    <p style="margin: 5px 0;">This is an automated message, please do not reply.</p>
                </div>
                <div style="display: none; max-height: 0px; overflow: hidden; font-size: 1px; color: #0a0a0a;">ID: {datetime.now().timestamp()}</div>
            </div>
        </div>
    </body>
    </html>
    """
    send_email(email, "Password Successfully Reset - Leverage Corporation", html_content)


# ==================== AUTH DEPENDENCIES ====================

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> models.User:
    """Dependency to get the current authenticated user.

    BUG-2: accepts the JWT from either the Authorization header or the
    HttpOnly session cookie, so a returning browser (whose JS storage was
    cleared) is still authenticated as long as the cookie is valid.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = None
    for token in iter_request_tokens(request, credentials):
        payload = decode_token(token)
        if payload is not None:
            break

    if payload is None:
        raise credentials_exception
    
    # Check blacklist
    jti = payload.get("jti")
    if jti and is_token_blacklisted(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked"
        )
    
    user_id: int = payload.get("user_id")
    if user_id is None:
        raise credentials_exception
    
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if user is None:
        raise credentials_exception
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated"
        )
    
    return user


def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db)
) -> Optional[models.User]:
    """Optional auth - returns None if not authenticated or on any error.

    BUG-2: also accepts the HttpOnly session cookie, mirroring
    get_current_user().
    """
    try:
        payload = None
        for token in iter_request_tokens(request, credentials):
            payload = decode_token(token)
            if payload is not None:
                break

        if payload is None:
            return None
        
        # Check blacklist
        jti = payload.get("jti")
        if jti and is_token_blacklisted(db, jti):
            return None
        
        user_id: int = payload.get("user_id")
        if user_id is None:
            return None
        
        user = db.query(models.User).filter(models.User.user_id == user_id).first()
        return user if user and user.is_active else None
    except Exception:
        return None

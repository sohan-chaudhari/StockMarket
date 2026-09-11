"""Shared slowapi Limiter instance.

Lives in its own module (not main.py) so routers/auth_router.py and
routers/trade_router.py can import it without a circular import -- main.py
imports those routers before it would otherwise define the limiter.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

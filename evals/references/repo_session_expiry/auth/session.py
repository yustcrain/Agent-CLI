from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass(frozen=True)
class Session:
    token: str
    issued_at: datetime
    expires_at: datetime

def _aware(value):
    return isinstance(value,datetime) and value.tzinfo is not None and value.utcoffset() is not None

def _token(value):
    return isinstance(value,str) and bool(value.strip())

def create_session(token, ttl_seconds, now):
    if not _token(token): raise ValueError("token must be non-empty")
    if type(ttl_seconds) is not int or ttl_seconds <= 0: raise ValueError("ttl_seconds must be a positive integer")
    if not _aware(now): raise ValueError("now must be timezone-aware")
    return Session(token,now,now+timedelta(seconds=ttl_seconds))

def is_active(session, now):
    if not _aware(now): raise ValueError("now must be timezone-aware")
    return now < session.expires_at

def rotate(session, new_token, now):
    if not _token(new_token): raise ValueError("token must be non-empty")
    if not is_active(session,now): raise ValueError("session is expired")
    return Session(new_token,now,session.expires_at)

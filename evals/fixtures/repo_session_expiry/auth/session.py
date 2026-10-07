from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass(frozen=True)
class Session:
    token: str
    issued_at: datetime
    expires_at: datetime

def create_session(token, ttl_seconds, now):
    return Session(token,now,now+timedelta(seconds=ttl_seconds))

def is_active(session, now):
    return now <= session.expires_at

def rotate(session, new_token, now):
    return Session(new_token,now,now+timedelta(seconds=3600))

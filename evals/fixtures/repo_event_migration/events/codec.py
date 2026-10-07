from .models import Event

def decode(payload):
    return Event(payload["user_id"], payload["kind"], payload.get("metadata", {}))

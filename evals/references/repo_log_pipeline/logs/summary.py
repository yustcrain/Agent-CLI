from datetime import timezone
from .parser import _timestamp

def _format(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00","Z")

def summarize(events):
    counts={}; times=[]
    for event in events:
        counts[event["level"]]=counts.get(event["level"],0)+1; times.append(_timestamp(event["timestamp"]))
    return {"level_counts":counts,"first_timestamp":_format(min(times)) if times else None,"last_timestamp":_format(max(times)) if times else None}

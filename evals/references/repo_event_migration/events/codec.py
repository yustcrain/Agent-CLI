from copy import deepcopy
from .models import Event
from .migrations import v1_to_v2

def decode(payload):
    if not isinstance(payload,dict) or type(payload.get("version")) is not int:
        raise ValueError("event must have an integer version")
    try:
        if payload["version"] == 1: data=v1_to_v2(payload)
        elif payload["version"] == 2: data=deepcopy(payload)
        else: raise ValueError("unsupported event version")
        if not isinstance(data["metadata"],dict): raise ValueError("metadata must be a dictionary")
        event=Event(data["user_id"],data["kind"],deepcopy(data["metadata"]))
    except (KeyError,TypeError) as exc:
        raise ValueError("invalid event payload") from exc
    return event

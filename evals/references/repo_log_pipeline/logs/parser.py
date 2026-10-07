import json
from datetime import datetime

def _timestamp(value):
    if not isinstance(value,str): raise ValueError("timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z","+00:00"))

def parse_jsonl(text):
    events=[]
    for number,line in enumerate(text.splitlines(),1):
        if not line.strip(): continue
        try:
            event=json.loads(line)
            if not isinstance(event,dict) or not isinstance(event.get("level"),str): raise ValueError
            _timestamp(event.get("timestamp"))
        except (json.JSONDecodeError,ValueError,TypeError) as exc:
            raise ValueError(f"invalid event on line {number}") from exc
        events.append(event)
    return events

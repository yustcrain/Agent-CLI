def summarize(events):
    counts={}
    for event in events:
        counts[event["level"]]=counts.get(event["level"],0)+1
    return {"level_counts":counts,"first_timestamp":events[0]["timestamp"],"last_timestamp":events[-1]["timestamp"]}

from copy import deepcopy

def v1_to_v2(payload):
    return {"version":2,"user_id":payload["user"],"kind":payload["type"],"metadata":deepcopy(payload.get("data",{}))}

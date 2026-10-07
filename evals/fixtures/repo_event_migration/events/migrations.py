def v1_to_v2(payload):
    payload["version"] = 2
    payload["user_id"] = payload.pop("user")
    return payload

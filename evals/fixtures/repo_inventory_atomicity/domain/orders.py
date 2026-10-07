def create_order(inventory, lines):
    inventory.reserve(lines)
    return {"lines": list(lines), "status": "reserved"}

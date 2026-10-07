def summarize_csv(text):
    rows = text.splitlines()[1:]
    result = {}
    for row in rows:
        name, amount = row.split(",")
        result[name] = float(amount)
    return result

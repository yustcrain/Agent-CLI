import json

def parse_jsonl(text):
    return [json.loads(line) for line in text.splitlines()]

from pathlib import Path

def resolve_under(root, user_path):
    return Path(root) / user_path

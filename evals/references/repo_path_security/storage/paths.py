from pathlib import Path

def resolve_under(root, user_path):
    root=Path(root).resolve()
    candidate=Path(user_path)
    if candidate.is_absolute(): raise ValueError("absolute paths are not allowed")
    resolved=(root/candidate).resolve()
    if not resolved.is_relative_to(root): raise ValueError("path escapes storage root")
    return resolved

from .paths import resolve_under

def read_text(root, user_path):
    return resolve_under(root,user_path).read_text(encoding="utf-8")

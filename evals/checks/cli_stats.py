import json, subprocess, sys
def run(*args):
    return subprocess.run([sys.executable, "stats.py", *args], capture_output=True, text=True, timeout=3)
r=run("1", "2.5", "-3")
assert r.returncode == 0 and not r.stderr
assert json.loads(r.stdout) == {"count": 3, "sum": 0.5}
r=run(); assert r.returncode == 0 and json.loads(r.stdout) == {"count": 0, "sum": 0}
for value in ("oops", "nan", "inf", "-inf"):
    r=run(value); assert r.returncode == 2 and r.stderr.strip() and not r.stdout

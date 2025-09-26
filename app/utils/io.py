import os, time
from app.config.settings import RUNS_DIR

def make_run_dir() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RUNS_DIR, ts)
    os.makedirs(path, exist_ok=True)
    return path

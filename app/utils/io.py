# app/utils/io.py
import os
from pathlib import Path
from datetime import datetime

def make_run_dir(base: str | None = None) -> str:
    """
    Create a timestamped run directory, trying several bases:
      1) explicit base (if passed)
      2) $RUNS_DIR
      3) /app/runs
      4) /tmp/runs
      5) ./runs
    Returns the absolute path (str). Raises only if every base fails.
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidates = []
    if base:
        candidates.append(base)
    env_dir = os.getenv("RUNS_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates += ["/app/runs", "/tmp/runs", "./runs"]

    last_err = None
    for root in candidates:
        try:
            p = Path(root).resolve()
            p.mkdir(parents=True, exist_ok=True)
            run_dir = p / ts
            run_dir.mkdir(parents=True, exist_ok=True)
            return str(run_dir)
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Could not create runs directory: {last_err}")

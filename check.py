"""Запустить независимые примеры стандартной библиотекой Python."""
from pathlib import Path
import subprocess
import sys


SAMPLES = (
    "payments/01_idempotency_and_reconcile_demo.py",
    "data/02_test_scd2_sql.py",
    "forms/04_concurrent_version_conflict_test.py",
    "crm/batch_replay.py",
    "rag/03_rag_retrieval_vs_citation_eval.py",
    "vision/demo.py",
    "booking/demo.py",
    "windows/demo.py",
    "ipc/demo.py",
    "studygram/demo.py",
)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    for sample in SAMPLES:
        print(f"\n{sample}", flush=True)
        subprocess.run([sys.executable, "-B", str(root / sample)], cwd=root, check=True)
    print(f"\nPassed: {len(SAMPLES)} examples.")

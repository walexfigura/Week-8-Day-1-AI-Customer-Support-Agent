#!/usr/bin/env python3
"""Run the agent-level evaluations (same as `python run.py eval`).

    python evals/run_evals.py                      # all cases
    python evals/run_evals.py --case prompt_injection --repeat 3

Requires OPENAI_API_KEY. Scoring logic: src/customer_support_agent/evaluation.py.
Results are saved to evals/results/ (git-ignored).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from customer_support_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["eval", *sys.argv[1:]]))

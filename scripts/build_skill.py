#!/usr/bin/env python3
"""Build dist/customer-support-skill.zip (same as `python run.py build-skill`).

The builder validates SKILL.md front matter, copies the authoritative
knowledge base, workflows, and sample data into the package, and checks that
the ZIP has the single top-level `customer-support/` folder Claude expects.
Logic: src/customer_support_agent/skill_builder.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from customer_support_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["build-skill"]))

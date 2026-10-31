#!/usr/bin/env python3
"""Единый интерфейс: python tools/study.py --help. Только стандартная библиотека."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "workspace"))
from study_cli import main
if __name__ == "__main__":
    sys.exit(main())

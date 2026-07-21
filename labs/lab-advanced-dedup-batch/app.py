from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI


def _load_factory() -> Any:
    spec = importlib.util.spec_from_file_location(
        "advanced_race_factory", Path(__file__).resolve().parents[1] / "_advanced_race_factory.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_app() -> FastAPI:
    return _load_factory().create_advanced_app("dedup-batch")


app = create_app()

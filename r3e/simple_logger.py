#!/usr/bin/env python3
"""
simple_logger.py - Minimal event logging for ECO iterations
"""
import json
import os
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(os.getenv("MEMORY_ROOT", "./.micro_surgeon_memory"))


def _log_dir() -> Path:
    """Create storage only when an event is actually written."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR

def log_eco_success(design: str, before_wns: float, after_wns: float, actions: list, iteration: int):
    """Log successful ECO iteration"""
    event = {
        "timestamp": datetime.now().isoformat(),
        "type": "success",
        "design": design,
        "iteration": iteration,
        "before_wns": before_wns,
        "after_wns": after_wns,
        "delta_wns": after_wns - before_wns,
        "actions": [{"type": a.action_type, "target": a.target_inst, "params": a.params} for a in actions]
    }
    with open(_log_dir() / "episodes.jsonl", "a") as f:
        f.write(json.dumps(event) + "\n")

def log_eco_failure(design: str, error_msg: str, actions: list, iteration: int):
    """Log failed ECO iteration"""
    event = {
        "timestamp": datetime.now().isoformat(),
        "type": "failure",
        "design": design,
        "iteration": iteration,
        "error": error_msg[:500],
        "actions": [{"type": a.action_type, "target": a.target_inst, "params": a.params} for a in actions]
    }
    with open(_log_dir() / "failures.jsonl", "a") as f:
        f.write(json.dumps(event) + "\n")

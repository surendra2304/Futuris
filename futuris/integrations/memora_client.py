"""
Universal Memora Client for Futuris Autonomous Forecasting & Scenario Simulation
"""

import sys
from pathlib import Path

# Try importing from central Memora SDK first. The path only makes sense on the
# author's Windows workstation; guard it so a stale absolute path is never
# inserted into sys.path on any other machine.
try:
    MEMORA_ROOT = Path("d:/FRIDAY Universe/Memora")
    if MEMORA_ROOT.is_dir() and str(MEMORA_ROOT) not in sys.path:
        sys.path.insert(0, str(MEMORA_ROOT))
        from sdk.memora_client import MemoraClient, memora_client
    else:
        raise ImportError("local Memora SDK checkout not present")
except Exception:
    from .memora_cloud_fallback import MemoraClient, memora_client

__all__ = ["MemoraClient", "memora_client"]

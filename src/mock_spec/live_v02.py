"""Compatibility command for historical ``python -m mock_spec.live_v02`` runs.

Current callers use audited_run. Retain this command while historical Step 6
protocols reference it; no inference or audit implementation belongs here.
"""

from .audited_run import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())

"""Deployment entry point.

The project uses a ``src/`` layout and is deliberately not pip-installed (the
test suite relies on ``pythonpath = ["src"]`` so there is no install step).
A platform's build, though, just runs ``pip install -r requirements.txt`` and
then a start command — so this shim puts ``src`` on the path and re-exports the
app under a name any platform can reach:

    uvicorn main:app --host 0.0.0.0 --port $PORT

Binding and port come from the environment, never from code, which is what makes
the same command correct on Railway and on a laptop.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from basketball_scout.web.app import app  # noqa: E402  (path setup must run first)

__all__ = ["app"]


def main() -> None:
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("RELOAD", "").lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


if __name__ == "__main__":
    uvicorn.run("nova_gateway.main:app", host="127.0.0.1", port=8765, reload=False)

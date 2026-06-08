from __future__ import annotations

import json
import sys


def main() -> int:
    """供后续 stdio MCP 烟测使用的占位 demo 进程。"""

    payload = {"name": "nova-demo-mcp", "tools": ["echo"], "resources": ["demo://readme"]}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Write the agent's OpenAPI document to /shared/openapi.json. Never hand-edit the output.

`npm run gen:api` in /web turns it into `web/types/api.ts`, so the web client's view of
every route response is the Pydantic model the route actually returns. The app is built
with a deps factory that yields nothing and its lifespan never runs: no database, Redis,
Qloo harness or network is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parents[1]
OUTPUT = AGENT_DIR.parent / "shared" / "openapi.json"

sys.path.insert(0, str(AGENT_DIR))

from draw.api.deps import Deps  # noqa: E402
from draw.main import create_app  # noqa: E402


async def _no_deps() -> Deps | None:
    return None


def openapi() -> dict[str, Any]:
    return create_app(_no_deps).openapi()


def main() -> None:
    OUTPUT.write_text(json.dumps(openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(AGENT_DIR.parent)}")


if __name__ == "__main__":
    main()

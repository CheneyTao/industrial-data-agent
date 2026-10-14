from __future__ import annotations

import uvicorn

from dataagent.app import create_app
from dataagent.config import get_settings

app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("dataagent.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()

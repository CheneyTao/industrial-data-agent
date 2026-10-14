from __future__ import annotations

import asyncio
import json

from dataagent.config import Settings
from dataagent.sandbox import DockerPythonSandbox


async def run() -> dict[str, object]:
    settings = Settings(_env_file=None)
    sandbox = DockerPythonSandbox(settings)
    result = await sandbox.execute(
        'import json\nprint(json.dumps({"sum": sum([1, 2, 3]), "runtime": "sandbox"}))',
        "",
    )
    if not result.success:
        raise RuntimeError(result.stderr or result.error_message or result.error_type)
    payload = json.loads(result.stdout)
    assert payload == {"sum": 6, "runtime": "sandbox"}
    return {
        "success": result.success,
        "payload": payload,
        "stderr": result.stderr,
        "image": settings.sandbox_image,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

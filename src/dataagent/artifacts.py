from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from filelock import FileLock

from dataagent.config import Settings
from dataagent.errors import DataAgentError, NotFoundError
from dataagent.version import build_identity


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ArtifactStore:
    """Durable per-run evidence, shared by HTTP and MCP via the data volume.

    Payloads are content-addressed; writes are atomic and guarded across processes.
    A hash detects modification, not whether an LLM's interpretation is true.
    """

    def __init__(self, settings: Settings):
        self.root = settings.vector_file_path.parent / "analysis-runs"

    def _directory(self, run_id: str) -> Path:
        try:
            return self.root / str(UUID(run_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DataAgentError("Invalid analysis run ID") from exc

    @staticmethod
    def _write(path: Path, value: Any) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(canonical_json(value), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, agent_id: int, question: str, run_id: str | None = None) -> dict[str, Any]:
        run_id = str(UUID(run_id)) if run_id else str(uuid4())
        directory = self._directory(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(directory / ".lock")):
            if (directory / "manifest.json").exists():
                return self.load(run_id, agent_id)
            manifest = {
                "traceVersion": "1.0",
                "build": build_identity(),
                "runId": run_id,
                "agentId": agent_id,
                "question": question,
                "createdAt": _now(),
                "status": "running",
                "artifacts": [],
                "steps": [],
                "integrityNotice": "Hashes verify stored bytes, not causal claims or database freshness.",
            }
            self._write(directory / "manifest.json", manifest)
        self.add(run_id, agent_id, "question", {"question": question})
        return self.load(run_id, agent_id)

    def load(self, run_id: str, agent_id: int) -> dict[str, Any]:
        path = self._directory(run_id) / "manifest.json"
        if not path.is_file():
            raise NotFoundError("Analysis run not found")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["agentId"] != agent_id:
            raise NotFoundError("Analysis run not found for this agent")
        return manifest

    def add(
        self,
        run_id: str,
        agent_id: int,
        kind: str,
        content: Any,
        parents: list[str] | None = None,
    ) -> dict[str, Any]:
        directory = self._directory(run_id)
        # Resolve ownership before even opening a lock at a caller-controlled path.
        self.load(run_id, agent_id)
        with FileLock(str(directory / ".lock")):
            manifest = self.load(run_id, agent_id)
            refs = list(dict.fromkeys(parents or []))
            known = {item["artifactId"] for item in manifest["artifacts"]}
            if set(refs) - known:
                raise DataAgentError("Artifact references unknown evidence")
            digest = fingerprint(content)
            artifact_id = fingerprint({"kind": kind, "sha256": digest, "parents": refs})[:32]
            existing = next((a for a in manifest["artifacts"] if a["artifactId"] == artifact_id), None)
            if existing:
                return existing
            metadata = {
                "artifactId": artifact_id,
                "kind": kind,
                "sha256": digest,
                "parentIds": refs,
                "createdAt": _now(),
            }
            self._write(directory / f"{artifact_id}.json", content)
            manifest["artifacts"].append(metadata)
            self._write(directory / "manifest.json", manifest)
        return metadata

    def get(self, run_id: str, agent_id: int, artifact_id: str) -> dict[str, Any]:
        manifest = self.load(run_id, agent_id)
        metadata = next((a for a in manifest["artifacts"] if a["artifactId"] == artifact_id), None)
        if metadata is None:
            raise NotFoundError("Artifact not found in this analysis run")
        payload = json.loads((self._directory(run_id) / f"{artifact_id}.json").read_text(encoding="utf-8"))
        if fingerprint(payload) != metadata["sha256"]:
            raise DataAgentError("Artifact integrity check failed")
        return {**metadata, "content": payload}

    def step(
        self,
        run_id: str,
        agent_id: int,
        name: str,
        *,
        status: str,
        duration_ms: float,
        inputs: list[str],
        outputs: list[str],
    ) -> None:
        directory = self._directory(run_id)
        with FileLock(str(directory / ".lock")):
            manifest = self.load(run_id, agent_id)
            known = {a["artifactId"] for a in manifest["artifacts"]}
            if (set(inputs) | set(outputs)) - known:
                raise DataAgentError("Step references unknown evidence")
            manifest["steps"].append(
                {
                    "stepId": str(uuid4()),
                    "node": name,
                    "status": status,
                    "durationMs": round(duration_ms, 2),
                    "inputArtifactIds": inputs,
                    "outputArtifactIds": outputs,
                }
            )
            self._write(directory / "manifest.json", manifest)

    def finish(self, run_id: str, agent_id: int, status: str) -> None:
        directory = self._directory(run_id)
        with FileLock(str(directory / ".lock")):
            manifest = self.load(run_id, agent_id)
            manifest.update(status=status, updatedAt=_now())
            self._write(directory / "manifest.json", manifest)

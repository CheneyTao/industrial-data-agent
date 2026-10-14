from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_dataset_version(*documents: Any) -> str:
    """Return a content-derived version for the exact source and semantic metadata snapshot."""
    digest = sha256_text(canonical_json(list(documents)))
    return f"industrial-pdm-{digest[:16]}"


class TraceRun:
    """Build a validated artifact lineage graph for one user-visible analysis run.

    The trace stores evidence and tool I/O summaries, not private model reasoning.
    """

    def __init__(
        self,
        question: str,
        source_id: str,
        dataset_version: str,
        *,
        run_id: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.run_id = run_id or str(uuid.uuid4())
        self.question = question
        self.source_id = source_id
        self.dataset_version = dataset_version
        self.started_at = started_at or datetime.now(timezone.utc)
        self.artifacts: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.claims: list[dict[str, Any]] = []
        self._artifact_ids: set[str] = set()
        self.status = "running"
        self.finished_at: datetime | None = None

    @staticmethod
    def _artifact_id(kind: str, payload: dict[str, Any]) -> str:
        prefix = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-") or "artifact"
        return f"{prefix}-{sha256_text(canonical_json(payload))[:12]}"

    def add_inline_artifact(
        self,
        kind: str,
        label: str,
        content: Any,
        *,
        parent_ids: Iterable[str] = (),
        evidence_class: str = "derived",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        parents = list(parent_ids)
        content_json = canonical_json(content)
        payload = {
            "kind": kind,
            "label": label,
            "content_sha256": sha256_text(content_json),
            "parents": parents,
            "evidence_class": evidence_class,
            "metadata": metadata or {},
        }
        artifact_id = self._artifact_id(kind, payload)
        if artifact_id in self._artifact_ids:
            return artifact_id
        artifact = {
            "artifact_id": artifact_id,
            "kind": kind,
            "label": label,
            "storage": "inline",
            "sha256": payload["content_sha256"],
            "parent_ids": parents,
            "evidence_class": evidence_class,
            "metadata": metadata or {},
            "content": content,
        }
        self.artifacts.append(artifact)
        self._artifact_ids.add(artifact_id)
        return artifact_id

    def add_file_artifact(
        self,
        kind: str,
        label: str,
        path: str | Path,
        *,
        parent_ids: Iterable[str] = (),
        evidence_class: str = "derived",
        metadata: dict[str, Any] | None = None,
        uri: str | None = None,
    ) -> str:
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(file_path)
        parents = list(parent_ids)
        file_hash = sha256_file(file_path)
        payload = {
            "kind": kind,
            "label": label,
            "sha256": file_hash,
            "parents": parents,
            "evidence_class": evidence_class,
            "metadata": metadata or {},
        }
        artifact_id = self._artifact_id(kind, payload)
        if artifact_id in self._artifact_ids:
            return artifact_id
        artifact = {
            "artifact_id": artifact_id,
            "kind": kind,
            "label": label,
            "storage": "file",
            "uri": uri or file_path.name,
            "sha256": file_hash,
            "size_bytes": file_path.stat().st_size,
            "parent_ids": parents,
            "evidence_class": evidence_class,
            "metadata": metadata or {},
        }
        self.artifacts.append(artifact)
        self._artifact_ids.add(artifact_id)
        return artifact_id

    def add_step(
        self,
        name: str,
        tool: str,
        *,
        input_artifact_ids: Iterable[str] = (),
        output_artifact_ids: Iterable[str] = (),
        duration_ms: float | None = None,
        status: str = "succeeded",
        summary: str | None = None,
    ) -> str:
        step_id = f"step-{len(self.steps) + 1:03d}"
        self.steps.append(
            {
                "step_id": step_id,
                "name": name,
                "tool": tool,
                "status": status,
                "duration_ms": duration_ms,
                "input_artifact_ids": list(input_artifact_ids),
                "output_artifact_ids": list(output_artifact_ids),
                "summary": summary,
            }
        )
        return step_id

    def add_claim(
        self,
        statement: str,
        evidence_artifact_ids: Iterable[str],
        *,
        claim_type: str = "data_supported",
        verification_status: str = "verified",
        limitations: str | None = None,
    ) -> str:
        refs = list(evidence_artifact_ids)
        if not refs:
            raise ValueError("Every displayed claim must have at least one evidence artifact")
        claim_id = f"claim-{len(self.claims) + 1:03d}"
        self.claims.append(
            {
                "claim_id": claim_id,
                "statement": statement,
                "claim_type": claim_type,
                "verification_status": verification_status,
                "evidence_artifact_ids": refs,
                "limitations": limitations,
            }
        )
        return claim_id

    def _validate_refs(self, refs: Iterable[str], context: str) -> None:
        missing = sorted(set(refs) - self._artifact_ids)
        if missing:
            raise ValueError(f"{context} references unknown artifacts: {missing}")

    def validate(self) -> None:
        for artifact in self.artifacts:
            self._validate_refs(artifact["parent_ids"], artifact["artifact_id"])
        for step in self.steps:
            self._validate_refs(step["input_artifact_ids"], step["step_id"])
            self._validate_refs(step["output_artifact_ids"], step["step_id"])
        for claim in self.claims:
            self._validate_refs(claim["evidence_artifact_ids"], claim["claim_id"])
            if claim["verification_status"] != "verified":
                raise ValueError(f"Unverified claim cannot be published: {claim['claim_id']}")

    def finish(self, status: str = "succeeded") -> None:
        self.status = status
        self.finished_at = datetime.now(timezone.utc)

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        finished_at = self.finished_at or datetime.now(timezone.utc)
        edges = [
            {"from": parent, "to": artifact["artifact_id"]}
            for artifact in self.artifacts
            for parent in artifact["parent_ids"]
        ]
        return {
            "trace_version": "1.0",
            "run_id": self.run_id,
            "status": self.status,
            "question": self.question,
            "source_id": self.source_id,
            "dataset_version": self.dataset_version,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "trace_scope": "tool inputs, outputs, data lineage and evidence; excludes private model reasoning",
            "artifacts": self.artifacts,
            "lineage_edges": edges,
            "steps": self.steps,
            "claims": self.claims,
            "integrity": {
                "artifact_count": len(self.artifacts),
                "step_count": len(self.steps),
                "claim_count": len(self.claims),
                "all_parent_refs_resolved": True,
                "all_claims_have_verified_evidence": True,
            },
        }

    def write_manifest(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        return destination


def find_run_manifest(root: str | Path, run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", run_id):
        raise ValueError("run_id must be a UUID")
    matches: list[Path] = []
    for path in Path(root).rglob("provenance_manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("run_id") == run_id:
            matches.append(path)
    if not matches:
        raise FileNotFoundError(f"No provenance manifest found for run_id={run_id}")
    matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0]

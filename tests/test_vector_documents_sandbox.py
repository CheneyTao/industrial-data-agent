from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dataagent.config import Settings
from dataagent.documents import DocumentExtractor, LocalFileStorage, TextSplitter, read_upload_limited
from dataagent.errors import ConfigurationError, DataAgentError, SandboxError
from dataagent.reports import chart_markdown, chart_option, report_html
from dataagent.sandbox import (
    RESULT_MARKER,
    DependencyParser,
    DockerPythonSandbox,
    parse_result,
)
from dataagent.vectorstore import (
    RetrievalService,
    SimpleVectorStore,
    VectorDocument,
    reciprocal_rank_fusion,
)


async def test_simple_vector_store_is_persistent_and_agent_isolated(tmp_path: Path) -> None:
    path = tmp_path / "vectors.json"
    store = SimpleVectorStore(path)
    await store.add(
        [
            VectorDocument(
                "a", "revenue sales", {"agentId": 1}, [1.0, 0.0], sparse_embedding={1: 0.5}
            ),
            VectorDocument("b", "private sales", {"agentId": 2}, [1.0, 0.0]),
            VectorDocument("c", "cost", {"agentId": 1}, [0.0, 1.0]),
        ]
    )
    matches = await store.search([1.0, 0.0], top_k=5, threshold=0.0, filters={"agentId": 1})
    assert [item.id for item in matches] == ["a", "c"]
    assert all(item.metadata["agentId"] == 1 for item in matches)

    reloaded = SimpleVectorStore(path)
    assert (await reloaded._load())["a"].sparse_embedding == {1: 0.5}
    # A second process keeps its own instance; atomic-file changes must still be
    # observed instead of serving a permanently stale in-memory snapshot.
    await store.add([VectorDocument("d", "new", {"agentId": 2}, [1.0, 0.0])])
    assert "d" in await reloaded._load()
    keyword = await reloaded.keyword_search("sales", top_k=5, min_score=0.0, filters={"agentId": 2})
    assert [item.id for item in keyword] == ["b", "d"]
    allowed = await reloaded.search(
        [1.0, 0.0], top_k=5, threshold=0.0, filters={"agentIdIn": [1]}
    )
    assert [item.id for item in allowed] == ["a", "c"]
    assert await reloaded.delete_by_metadata({"agentId": 1}) == 2
    assert {
        item.id for item in await reloaded.keyword_search("", top_k=5, min_score=0.0, filters={})
    } == {"b", "d"}


async def test_corrupt_vector_file_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "vectors.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="corrupt"):
        await SimpleVectorStore(path).search([1.0], top_k=1, threshold=0.0, filters={})


async def test_simple_vector_store_serializes_writes_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "shared-vectors.json"
    stores = [SimpleVectorStore(path), SimpleVectorStore(path)]

    await asyncio.gather(
        *(
            stores[index % 2].add(
                [VectorDocument(str(index), f"document-{index}", {"agentId": 1}, [1.0])]
            )
            for index in range(20)
        )
    )

    assert set((await SimpleVectorStore(path)._load()).keys()) == {
        str(index) for index in range(20)
    }


def test_embedding_batches_enforce_item_limit(settings: Settings) -> None:
    settings.embedding_batch_max_items = 2
    service = RetrievalService(settings)
    assert service._embedding_batches(["a", "b", "c", "d", "e"]) == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]


def test_reciprocal_rank_fusion_rewards_cross_ranking_hits() -> None:
    a = VectorDocument("a", "a")
    b = VectorDocument("b", "b")
    c = VectorDocument("c", "c")
    result = reciprocal_rank_fusion([a, b], [b, c], top_k=3)
    assert result[0].id == "b"
    assert result[0].score > result[1].score


async def test_local_storage_confines_deletion(settings: Settings, tmp_path: Path) -> None:
    storage = LocalFileStorage(settings)
    location, public_url = await storage.save("../unsafe name.txt", b"payload")
    saved = Path(location)
    assert await asyncio.to_thread(saved.read_bytes) == b"payload"
    assert saved.parent == settings.file_path.resolve()
    assert public_url.startswith("/uploads/")

    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    await storage.delete(str(outside))
    assert await asyncio.to_thread(outside.exists)
    await storage.delete(location)
    assert not await asyncio.to_thread(saved.exists)


async def test_upload_reader_stops_at_configured_limit() -> None:
    class Upload:
        def __init__(self, value: bytes) -> None:
            self.value = value
            self.position = 0

        async def read(self, size: int) -> bytes:
            chunk = self.value[self.position : self.position + size]
            self.position += len(chunk)
            return chunk

    assert await read_upload_limited(Upload(b"1234"), 4) == b"1234"
    with pytest.raises(DataAgentError) as captured:
        await read_upload_limited(Upload(b"12345"), 4)
    assert captured.value.status_code == 413


async def test_document_extraction_and_all_splitter_modes(settings: Settings, app) -> None:
    extractor = DocumentExtractor(settings)
    assert await extractor.extract("note.txt", "中文文本".encode()) == "中文文本"
    assert "a | b" in await extractor.extract("table.csv", b"a,b\n1,2")
    jsonl = await extractor.extract(
        "events.jsonl",
        '{"id":1}\n{"id":2,"name":"事件"}\n'.encode(),
    )
    assert jsonl.splitlines() == ['{"id": 1}', '{"id": 2, "name": "事件"}']

    splitter = TextSplitter(settings, app.state.llm)
    async with app.state.database.session_factory() as session:
        text = "第一句。第二句！\n\n第三段内容。第四句？"
        for mode in ("token", "recursive", "sentence", "semantic", "paragraph"):
            chunks = await splitter.split(session, text, mode)
            assert chunks
            assert all(chunk.strip() for chunk in chunks)


def test_dependency_parser_and_result_protocol(settings: Settings) -> None:
    parser = DependencyParser(settings)
    code = """# /// script
# dependencies = [\"pandas>=2,<3\"]
# requires-python = \">=3.11\"
# ///
print('ok')
"""
    metadata = parser.parse(code)
    assert metadata.dependencies == ["pandas>=2,<3"]
    assert metadata.requires_python == ">=3.11"

    with pytest.raises(SandboxError):
        parser.parse('# /// script\n# dependencies = ["pkg; rm -rf /"]\n# ///')
    with pytest.raises(SandboxError):
        parser.parse("# /// script\ndependencies = []\n# ///")

    payload = base64.b64encode(
        json.dumps({"success": True, "stdout": "42\n", "stderr": ""}).encode()
    ).decode()
    result = parse_result("noise\n" + RESULT_MARKER + payload)
    assert result.success and result.stdout == "42\n"
    assert parse_result("no marker").error_type == "SandboxProtocolError"


async def test_docker_sandbox_enforces_resource_flags_and_always_cleans_up(
    settings: Settings,
) -> None:
    payload = base64.b64encode(
        json.dumps({"success": True, "stdout": "done\n", "stderr": ""}).encode()
    ).decode()

    class FakeContainer:
        def __init__(self) -> None:
            self.exec_calls: list[tuple[list[str], bool]] = []
            self.removed = False

        def exec_run(self, command: list[str], *, demux: bool):
            self.exec_calls.append((command, demux))
            return SimpleNamespace(exit_code=0, output=(RESULT_MARKER + payload).encode())

        def remove(self, *, force: bool) -> None:
            assert force is True
            self.removed = True

    container = FakeContainer()
    run_call: dict[str, object] = {}

    class FakeContainers:
        def run(self, image: str, command: list[str], **kwargs):
            run_call.update(image=image, command=command, **kwargs)
            return container

    class FakeClient:
        containers = FakeContainers()

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = FakeClient()
    sandbox = DockerPythonSandbox(settings)
    sandbox._docker_client = lambda: client  # type: ignore[method-assign]

    result = await sandbox.execute("print('done')", "")

    assert result.success and result.stdout == "done\n"
    assert run_call["image"] == settings.sandbox_image
    assert run_call["detach"] is True
    assert run_call["mem_limit"] == f"{settings.sandbox_limit_memory_mb}m"
    assert run_call["nano_cpus"] == int(settings.sandbox_cpu_cores * 1_000_000_000)
    assert run_call["pids_limit"] == settings.sandbox_max_connections
    assert run_call["privileged"] is False
    assert run_call["cap_drop"] == ["ALL"]
    assert run_call["security_opt"] == ["no-new-privileges:true"]
    assert run_call["read_only"] is True
    assert run_call["tmpfs"]["/tmp"] == "rw,nosuid,nodev,noexec,size=256m"
    assert run_call["labels"] == {"dataagent.sandbox": "python"}
    assert container.removed is True
    assert client.closed is True


async def test_sandbox_health_reports_image_readiness_without_running_code(settings: Settings) -> None:
    class FakeClient:
        images = SimpleNamespace(get=lambda image: SimpleNamespace(id=f"sha256:{image}"))

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = FakeClient()
    sandbox = DockerPythonSandbox(settings)
    sandbox._docker_client = lambda: client  # type: ignore[method-assign]

    health = await sandbox.health()

    assert health == {
        "ready": True,
        "image": settings.sandbox_image,
        "imageId": f"sha256:{settings.sandbox_image}",
    }
    assert client.closed is True


def test_report_is_inert_and_chart_generation_is_data_driven(settings: Settings) -> None:
    malicious = "# title\n</script><script>alert(1)</script>"
    rendered = report_html(malicious, settings)
    assert malicious not in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert 'id="raw-data"' in rendered

    option = chart_option(["month", "revenue"], [["Jan", 10], ["Feb", 20]])
    assert option is not None
    assert option["series"][0]["data"] == [10, 20]
    assert chart_markdown(option).startswith("```echarts")

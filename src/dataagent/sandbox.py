from __future__ import annotations

import asyncio
import base64
import json
import re
import textwrap
import tomllib
from dataclasses import dataclass
from typing import Any

from dataagent.config import Settings, get_settings
from dataagent.errors import ConfigurationError, SandboxError

RESULT_MARKER = "__DATAAGENT_RESULT_V1__:"
PACKAGE_SPECIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?"
    r"(?:\s*[!<>=~]{1,2}\s*[A-Za-z0-9.*+!_-]+"
    r"(?:\s*,\s*[!<>=~]{1,2}\s*[A-Za-z0-9.*+!_-]+)*)?$"
)


@dataclass(slots=True)
class DependencyMetadata:
    dependencies: list[str]
    requires_python: str | None = None


@dataclass(slots=True)
class SandboxResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    error_type: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "errorType": self.error_type,
            "errorMessage": self.error_message,
        }


class DependencyParser:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def parse(self, code: str) -> DependencyMetadata:
        lines = code.splitlines()
        blocks: list[list[str]] = []
        index = 0
        while index < len(lines):
            if lines[index].strip() != "# /// script":
                index += 1
                continue
            block: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != "# ///":
                line = lines[index]
                if not line.lstrip().startswith("#"):
                    raise SandboxError("Each PEP 723 metadata line must be a Python comment")
                comment = line.lstrip()[1:]
                block.append(comment[1:] if comment.startswith(" ") else comment)
                index += 1
            if index >= len(lines):
                raise SandboxError("PEP 723 script block is not closed")
            blocks.append(block)
            index += 1
        if len(blocks) > 1:
            raise SandboxError("Python code must contain at most one PEP 723 script block")
        if not blocks:
            return DependencyMetadata([])
        metadata_text = "\n".join(blocks[0])
        if len(metadata_text.encode("utf-8")) > self.settings.sandbox_max_metadata_bytes:
            raise SandboxError("Python dependency metadata exceeds the configured size limit")
        try:
            metadata = tomllib.loads(metadata_text)
        except tomllib.TOMLDecodeError as exc:
            raise SandboxError(f"Invalid PEP 723 dependency metadata: {exc}") from exc
        if set(metadata) - {"dependencies", "requires-python"}:
            raise SandboxError("PEP 723 metadata contains unsupported fields")
        dependencies = metadata.get("dependencies", [])
        if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
            raise SandboxError("PEP 723 dependencies must be strings")
        if len(dependencies) > self.settings.sandbox_max_dependencies:
            raise SandboxError("Too many Python dependencies")
        for dependency in dependencies:
            if not PACKAGE_SPECIFIER.fullmatch(dependency):
                raise SandboxError(f"Unsupported Python dependency specifier: {dependency}")
        requires_python = metadata.get("requires-python")
        if requires_python is not None and not isinstance(requires_python, str):
            raise SandboxError("requires-python must be a string")
        return DependencyMetadata(list(dependencies), requires_python)


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _bootstrap(code: str, input_text: str, settings: Settings) -> str:
    return textwrap.dedent(
        f"""
        import base64, json, os, subprocess, sys
        _code = base64.b64decode("{_encoded(code)}").decode("utf-8")
        _input = base64.b64decode("{_encoded(input_text)}").decode("utf-8")
        _envelope = {{"success": True, "stdout": "", "stderr": "", "errorType": None, "errorMessage": None}}
        def _text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        try:
            _environment = dict(os.environ)
            _environment["PYTHONPATH"] = "/tmp/dataagent-deps"
            _completed = subprocess.run(
                [sys.executable, "-c", _code], input=_input, capture_output=True,
                text=True, timeout={settings.sandbox_code_timeout_seconds}, check=False, env=_environment
            )
            _envelope["stdout"] = _completed.stdout[-{settings.sandbox_max_stdout_bytes}:]
            _envelope["stderr"] = _completed.stderr[-{settings.sandbox_max_stderr_bytes}:]
            if _completed.returncode != 0:
                _envelope["success"] = False
                _envelope["errorType"] = "PythonProcessError"
                _envelope["errorMessage"] = "Python exited with status " + str(_completed.returncode)
        except subprocess.TimeoutExpired as _error:
            _envelope["success"] = False
            _envelope["errorType"] = "PythonTimeout"
            _envelope["errorMessage"] = (
                "Python code execution exceeded "
                + "{settings.sandbox_code_timeout_seconds} seconds"
            )
            _envelope["stdout"] = _text(_error.stdout)[-{settings.sandbox_max_stdout_bytes}:]
            _envelope["stderr"] = _text(_error.stderr)[-{settings.sandbox_max_stderr_bytes}:]
        except BaseException as _error:
            _envelope["success"] = False
            _envelope["errorType"] = type(_error).__name__
            _envelope["errorMessage"] = str(_error)
        _payload = base64.b64encode(json.dumps(_envelope, ensure_ascii=False).encode("utf-8")).decode("ascii")
        print("{RESULT_MARKER}" + _payload)
        """
    )


def _dependency_bootstrap(dependencies: list[str], settings: Settings) -> str:
    return textwrap.dedent(
        f"""
        import base64, json, subprocess, sys
        from importlib.metadata import PackageNotFoundError, distribution
        try:
            from packaging.requirements import Requirement
        except ImportError:
            from pip._vendor.packaging.requirements import Requirement
        _dependencies = json.loads(base64.b64decode("{_encoded(json.dumps(dependencies))}").decode("utf-8"))
        _index = base64.b64decode("{_encoded(settings.sandbox_package_index_url)}").decode("utf-8")
        _envelope = {{"success": True, "stdout": "", "stderr": "", "errorType": None, "errorMessage": None}}
        def _installed(spec, seen):
            requirement = Requirement(spec)
            try:
                installed = distribution(requirement.name)
            except PackageNotFoundError:
                return False
            if installed.version not in requirement.specifier:
                return False
            key = (requirement.name.lower().replace("_", "-"), tuple(sorted(requirement.extras)))
            if key in seen:
                return True
            seen.add(key)
            for dependency in installed.requires or []:
                child = Requirement(dependency)
                if child.marker and not any(
                    child.marker.evaluate({{"extra": extra}}) for extra in ("", *requirement.extras)
                ):
                    continue
                if not _installed(dependency, seen):
                    return False
            return True
        try:
            # Native extensions must load from the immutable runtime image, not
            # Docker's noexec /tmp. Do not reinstall satisfied packages there.
            if all(_installed(spec, set()) for spec in _dependencies):
                _envelope["stdout"] = "Dependencies already satisfied by runtime image."
            else:
                _completed = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                     "--no-cache-dir", "--target", "/tmp/dataagent-deps",
                     "--index-url", _index, *_dependencies],
                    capture_output=True, text=True,
                    timeout={settings.sandbox_dependency_timeout_seconds}, check=False
                )
                _envelope["stdout"] = _completed.stdout[-{settings.sandbox_max_stdout_bytes}:]
                _envelope["stderr"] = _completed.stderr[-{settings.sandbox_max_stderr_bytes}:]
                if _completed.returncode != 0:
                    _envelope.update(success=False, errorType="DependencyInstallError",
                                     errorMessage="pip exited with status " + str(_completed.returncode))
        except subprocess.TimeoutExpired as _error:
            _envelope.update(success=False, errorType="DependencyInstallTimeout", errorMessage=str(_error))
        except BaseException as _error:
            _envelope.update(success=False, errorType=type(_error).__name__, errorMessage=str(_error))
        _payload = base64.b64encode(json.dumps(_envelope, ensure_ascii=False).encode("utf-8")).decode("ascii")
        print("{RESULT_MARKER}" + _payload)
        """
    )


def parse_result(output: bytes | str) -> SandboxResult:
    value = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    marker = value.rfind(RESULT_MARKER)
    if marker < 0:
        return SandboxResult(
            False,
            stderr=value[-4096:],
            error_type="SandboxProtocolError",
            error_message="Missing result marker",
        )
    encoded = value[marker + len(RESULT_MARKER) :].strip().splitlines()[0]
    try:
        payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
        return SandboxResult(
            bool(payload.get("success")),
            str(payload.get("stdout") or ""),
            str(payload.get("stderr") or ""),
            payload.get("errorType"),
            payload.get("errorMessage"),
        )
    except Exception as exc:
        return SandboxResult(
            False, stderr=value[-4096:], error_type="SandboxProtocolError", error_message=str(exc)
        )


class DockerPythonSandbox:
    """One isolated, resource-bounded Docker container per generated-code task."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.parser = DependencyParser(self.settings)
        self._semaphore = asyncio.Semaphore(self.settings.sandbox_max_concurrency)
        self._waiting = 0
        self._waiting_lock = asyncio.Lock()

    async def _enter_queue(self) -> None:
        async with self._waiting_lock:
            if self._semaphore.locked() and self._waiting >= self.settings.sandbox_queue_capacity:
                raise SandboxError("Python sandbox queue is full")
            self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            async with self._waiting_lock:
                self._waiting -= 1

    async def execute(self, code: str, input_text: str) -> SandboxResult:
        if len(code.encode("utf-8")) > self.settings.sandbox_max_code_bytes:
            raise SandboxError("Python source exceeds the configured size limit")
        if len(input_text.encode("utf-8")) > self.settings.sandbox_max_input_bytes:
            raise SandboxError("Python stdin exceeds the configured size limit")
        metadata = self.parser.parse(code)
        await self._enter_queue()
        container: Any = None
        client: Any = None
        try:
            client = await asyncio.to_thread(self._docker_client)
            container = await asyncio.to_thread(
                client.containers.run,
                self.settings.sandbox_image,
                [
                    "sleep",
                    str(
                        self.settings.sandbox_dependency_timeout_seconds
                        + self.settings.sandbox_code_timeout_seconds
                        + 30
                    ),
                ],
                detach=True,
                mem_limit=f"{self.settings.sandbox_limit_memory_mb}m",
                nano_cpus=int(self.settings.sandbox_cpu_cores * 1_000_000_000),
                pids_limit=self.settings.sandbox_max_connections,
                privileged=False,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                read_only=True,
                tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=256m"},
                labels={"dataagent.sandbox": "python"},
            )
            if metadata.dependencies:
                installed = await self._exec(
                    container,
                    _dependency_bootstrap(metadata.dependencies, self.settings),
                    self.settings.sandbox_dependency_timeout_seconds + 10,
                )
                if not installed.success:
                    return installed
            return await self._exec(
                container,
                _bootstrap(code, input_text, self.settings),
                self.settings.sandbox_code_timeout_seconds + 10,
            )
        finally:
            if container is not None:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception:
                    pass
            if client is not None:
                try:
                    await asyncio.to_thread(client.close)
                except Exception:
                    pass
            self._semaphore.release()

    async def health(self) -> dict[str, Any]:
        client: Any = None
        try:
            client = await asyncio.to_thread(self._docker_client)
            image = await asyncio.to_thread(client.images.get, self.settings.sandbox_image)
            return {
                "ready": True,
                "image": self.settings.sandbox_image,
                "imageId": image.id,
            }
        except Exception as exc:
            return {
                "ready": False,
                "image": self.settings.sandbox_image,
                "error": str(exc),
            }
        finally:
            if client is not None:
                try:
                    await asyncio.to_thread(client.close)
                except Exception:
                    pass

    def _docker_client(self) -> Any:
        try:
            import docker
        except ImportError as exc:
            raise ConfigurationError("The docker Python package is required for sandbox execution") from exc
        try:
            client = (
                docker.DockerClient(base_url=self.settings.sandbox_docker_host)
                if self.settings.sandbox_docker_host
                else docker.from_env()
            )
            client.ping()
            return client
        except Exception as exc:
            raise ConfigurationError(f"Docker sandbox is unavailable: {exc}") from exc

    @staticmethod
    async def _exec(container: Any, bootstrap: str, operation_timeout: int) -> SandboxResult:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(container.exec_run, ["python", "-c", bootstrap], demux=False),
                operation_timeout,
            )
        except TimeoutError:
            return SandboxResult(
                False, error_type="SandboxTimeout", error_message="Sandbox operation timed out"
            )
        parsed = parse_result(result.output)
        if getattr(result, "exit_code", 0) and parsed.success:
            parsed.success = False
            parsed.error_type = "SandboxProcessError"
            parsed.error_message = f"Sandbox bootstrap exited with status {result.exit_code}"
        return parsed

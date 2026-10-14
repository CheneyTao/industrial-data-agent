from __future__ import annotations

from zipfile import ZipFile

from tools.package_release import build_archive


def test_release_archive_excludes_generated_build_artifacts(tmp_path) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / ".release-build").mkdir()
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / ".release-build" / "stale.whl").write_bytes(b"not-a-wheel")
    (root / ".env").write_text("SECRET=value\n", encoding="utf-8")

    output = tmp_path / "release.zip"
    result = build_archive(root, output)

    with ZipFile(output) as archive:
        names = archive.namelist()
    assert result["fileCount"] == 1
    assert names == ["DataAgent-Python/src/app.py"]

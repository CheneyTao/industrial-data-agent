from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dataagent.sandbox import _dependency_bootstrap, parse_result


@pytest.mark.parametrize(
    ("requested", "installed", "should_install"),
    [
        (["pandas>=2"], {"pandas": ("2.3.3", ["numpy>=1.26"]), "numpy": ("2.4.6", [])}, False),
        (["pandas>=3"], {"pandas": ("2.3.3", [])}, True),
        (["pandas>=2"], {"pandas": ("2.3.3", ["numpy>=1.26"])}, True),
        (["pandas>=2"], {"pandas": ("2.3.3", ["numpy>=2"]), "numpy": ("1.26.4", [])}, True),
        (["demo"], {"demo": ("1.0", ['missing; extra == "excel"'])}, False),
        (["demo[excel]"], {"demo": ("1.0", ['missing; extra == "excel"'])}, True),
        (["demo"], {"demo": ("1.0", ['missing; python_version < "3.0"'])}, False),
        (["demo"], {"demo": ("1.0", ["other"]), "other": ("1.0", ["demo"])}, False),
        (["missing"], {}, True),
    ],
)
def test_preinstalled_requirements_preserve_read_only_runtime(
    settings, monkeypatch, capsys, requested, installed, should_install
):
    def distribution(name):
        if name not in installed:
            raise PackageNotFoundError(name)
        version, requires = installed[name]
        return SimpleNamespace(version=version, requires=requires)

    monkeypatch.setattr("importlib.metadata.distribution", distribution)
    installer = Mock(return_value=SimpleNamespace(returncode=0, stdout="installed", stderr=""))
    monkeypatch.setattr("subprocess.run", installer)
    exec(_dependency_bootstrap(requested, settings), {})
    result = parse_result(capsys.readouterr().out)
    assert result.success
    assert installer.called is should_install
    if should_install:
        args = installer.call_args.args[0]
        assert args[args.index("--target") + 1] == "/tmp/dataagent-deps"
        assert args[-len(requested):] == requested
    else:
        assert "already satisfied" in result.stdout

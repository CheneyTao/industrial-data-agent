from tools.evaluate_report_release import evaluate


async def test_independent_multitrial_business_acceptance(tmp_path):
    result = await evaluate(tmp_path, trials=5)
    assert result["mode"] == "deterministic-contract"
    assert result["passed"] == result["total"] == 20
    assert (tmp_path / "acceptance.json").is_file()
    assert len({c["runId"] for c in result["checks"]}) == 5

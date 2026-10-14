#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def to_jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def render_html(results: list[dict[str, Any]], output: Path) -> None:
    cards: list[str] = []
    for result in results:
        headers = result.get("columns", [])
        header_html = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
        body_rows = []
        for row in result.get("preview", []):
            body_rows.append(
                "<tr>" + "".join(f"<td>{html.escape(str(row.get(name, '')))}</td>" for name in headers) + "</tr>"
            )
        table = (
            f"<div class='table'><table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"
            if headers
            else ""
        )
        badge = "source" if result["evidence_class"].startswith("source") else "synthetic/mixed"
        cards.append(
            f"<section><div class='meta'><span>{html.escape(result['id'])}</span>"
            f"<span>{html.escape(result['difficulty'])}</span><span>{html.escape(badge)}</span>"
            f"<span>{result['elapsed_ms']} ms</span></div>"
            f"<h2>{html.escape(result['question'])}</h2>"
            f"<pre>{html.escape(result['sql'])}</pre>"
            f"<p>返回 {result['row_count']} 行；状态：<strong>{html.escape(result['status'])}</strong></p>{table}</section>"
        )
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Industrial PdM NL2SQL 验证报告</title><style>
body{{margin:0;background:#f4f7fb;color:#172033;font-family:Arial,"Microsoft YaHei",sans-serif;line-height:1.55}}
main{{max-width:1200px;margin:30px auto;padding:0 20px 50px}}header{{background:linear-gradient(135deg,#111827,#1d4ed8);color:#fff;padding:30px;border-radius:16px}}
section{{background:#fff;border:1px solid #dfe6ef;border-radius:14px;padding:22px;margin:16px 0;box-shadow:0 8px 24px #0f172a0a}}
h1{{margin:0 0 8px}}h2{{font-size:19px}}.meta{{display:flex;gap:8px;flex-wrap:wrap}}.meta span{{background:#eaf1ff;color:#1d4ed8;padding:3px 9px;border-radius:999px;font-size:12px}}
pre{{background:#0f172a;color:#dbeafe;padding:14px;border-radius:9px;white-space:pre-wrap;overflow:auto}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px 10px;border-bottom:1px solid #e5e7eb;text-align:left;white-space:nowrap}}th{{background:#f8fafc}}
</style></head><body><main><header><h1>Industrial PdM NL2SQL 参考案例验证</h1><p>自然语言问题、参考 SQL 与实际执行结果；合成/混合案例必须保留证据边界。</p></header>{''.join(cards)}</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute and verify the reference SQLite SQL for NL2SQL cases")
    parser.add_argument("--db", default=str(ROOT / "generated" / "runtime" / "industrial_pdm.sqlite"))
    parser.add_argument("--cases", default=str(ROOT / "dataagent" / "nl2sql_cases.json"))
    parser.add_argument("--output", default=str(ROOT / "generated" / "reports" / "nl2sql_verification.json"))
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"SQLite database does not exist: {db_path}")
    payload = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    failures: list[str] = []

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for case in payload["cases"]:
            sql = str(case["sqlite_sql"]).strip().rstrip(";")
            if not sql.lower().startswith(("select", "with")) or ";" in sql:
                raise ValueError(f"Unsafe SQL in {case['id']}")
            started = time.perf_counter()
            try:
                cursor = conn.execute(sql)
                rows = cursor.fetchall()
                columns = [item[0] for item in cursor.description or []]
                expected = list(case["expected_columns"])
                missing = [name for name in expected if name not in columns]
                status = "PASS" if not missing and rows else "FAIL"
                if status == "FAIL":
                    failures.append(f"{case['id']}: missing={missing}, rows={len(rows)}")
                preview = [
                    {name: to_jsonable(row[name]) for name in columns}
                    for row in rows[:10]
                ]
                error = None
            except Exception as exc:
                rows, columns, preview = [], [], []
                status = "FAIL"
                error = str(exc)
                failures.append(f"{case['id']}: {exc}")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            results.append(
                {
                    "id": case["id"],
                    "difficulty": case["difficulty"],
                    "question": case["question"],
                    "evidence_class": case["evidence_class"],
                    "sql": sql,
                    "status": status,
                    "row_count": len(rows),
                    "columns": columns,
                    "preview": preview,
                    "elapsed_ms": elapsed_ms,
                    "error": error,
                }
            )
            print(f"{case['id']} {status} rows={len(rows)} elapsed={elapsed_ms}ms")
    finally:
        conn.close()

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "PASS" if not failures else "FAIL",
        "case_count": len(results),
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(results, output.with_suffix(".html"))
    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()

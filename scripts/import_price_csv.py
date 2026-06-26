from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


REQUIRED_COLUMNS = ["date", "commodity", "price", "currency", "unit", "source", "title", "url", "region"]


def validate_file(path: Path) -> dict:
    errors: list[dict] = []
    source_counts: dict[str, int] = {}
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            return {"path": str(path), "valid": False, "rows": 0, "errors": [{"row": 0, "error": "missing_columns", "columns": missing}], "source_counts": {}}
        for row_number, row in enumerate(reader, start=2):
            rows += 1
            row_errors = _validate_row(row)
            if row_errors:
                errors.append({"row": row_number, "error": "invalid_row", "fields": row_errors})
                continue
            source = (row.get("source") or "unknown").strip()
            source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "path": str(path),
        "valid": not errors,
        "rows": rows,
        "valid_rows": rows - len(errors),
        "errors": errors,
        "source_counts": source_counts,
    }


def import_file(path: Path, target_dir: Path, dry_run: bool = False) -> dict:
    report = validate_file(path)
    if report["valid"] and not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        shutil.copy2(path, target)
        report["imported_to"] = str(target)
    elif dry_run:
        report["imported_to"] = None
    return report


def _validate_row(row: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for column in REQUIRED_COLUMNS:
        if column != "region" and not (row.get(column) or "").strip():
            errors[column] = "required"
    try:
        date.fromisoformat((row.get("date") or "").strip())
    except ValueError:
        errors["date"] = "must_be_iso_date"
    try:
        Decimal((row.get("price") or "").strip())
    except (InvalidOperation, ValueError):
        errors["price"] = "must_be_decimal"
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and import licensed LME/SHFE/Mysteel price CSV files.")
    parser.add_argument("csv_files", nargs="+", type=Path)
    parser.add_argument("--target-dir", type=Path, default=Path("data/raw/prices"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any file has invalid rows.")
    args = parser.parse_args()

    reports = [import_file(path, args.target_dir, dry_run=args.dry_run) for path in args.csv_files]
    payload = {
        "status": "ok" if all(report["valid"] for report in reports) else "invalid",
        "files": reports,
        "total_valid_rows": sum(report.get("valid_rows", 0) for report in reports),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.strict and payload["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

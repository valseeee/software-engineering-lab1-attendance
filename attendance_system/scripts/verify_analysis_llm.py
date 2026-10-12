from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import app
from models import Employee
from services.analysis_service import AnalysisServiceError, generate_long_analysis_for_employee, generate_short_analysis_for_employee


DEFAULT_RESULTS_DIR = BASE_DIR / "analysis_results"


def serialize_analysis(analysis) -> dict[str, Any]:
    return {
        "id": analysis.id,
        "employee_id": analysis.employee_id,
        "type": analysis.type,
        "personality": analysis.personality,
        "motivation": analysis.motivation,
        "health": analysis.health,
        "report": analysis.report_text,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
    }


def serialize_frontend_payload(analysis) -> dict[str, Any]:
    return {
        "personality": analysis.personality,
        "motivation": analysis.motivation,
        "health": analysis.health,
        "report": analysis.report_text,
    }


def safe_filename_part(value: str) -> str:
    allowed = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_"):
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "unknown"


def run_for_employee(employee_id: str) -> dict[str, Any]:
    employee = Employee.query.filter_by(employee_id=employee_id).first()
    result: dict[str, Any] = {
        "employee_id": employee_id,
        "employee_name": employee.name if employee else "",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "analyses": {},
        "errors": {},
    }
    try:
        long_analysis = generate_long_analysis_for_employee(employee_id)
        result["long"] = serialize_analysis(long_analysis)
        result["analyses"]["long"] = serialize_frontend_payload(long_analysis)
    except AnalysisServiceError as exc:
        result["long_error"] = str(exc)
        result["errors"]["long"] = str(exc)

    try:
        short_analysis = generate_short_analysis_for_employee(employee_id)
        result["short"] = serialize_analysis(short_analysis)
        result["analyses"]["short"] = serialize_frontend_payload(short_analysis)
    except AnalysisServiceError as exc:
        result["short_error"] = str(exc)
        result["errors"]["short"] = str(exc)

    return result


def save_employee_result(item: dict[str, Any], output_dir: Path) -> Optional[Path]:
    if not item.get("analyses"):
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    employee_id = safe_filename_part(str(item.get("employee_id", "")))
    employee_name = safe_filename_part(str(item.get("employee_name", "")))
    filename = f"{employee_id}_{employee_name}_{timestamp}.json"
    output_path = output_dir / filename

    output_data = {
        "employee_id": item.get("employee_id"),
        "employee_name": item.get("employee_name"),
        "generated_at": item.get("generated_at"),
        "analyses": item.get("analyses", {}),
    }
    if item.get("errors"):
        output_data["errors"] = item["errors"]

    output_path.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify long/short analysis LLM calling and output format."
    )
    parser.add_argument(
        "--employee-id",
        action="append",
        dest="employee_ids",
        help="Employee ID to verify. Can be provided multiple times. Defaults to all employees.",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--save-dir",
        default=str(DEFAULT_RESULTS_DIR),
        help="Directory for per-employee JSON files. Default: analysis_results/",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with app.app_context():
        if args.employee_ids:
            employee_ids = args.employee_ids
        else:
            employee_ids = [employee.employee_id for employee in Employee.query.order_by(Employee.employee_id).all()]

        payload = []
        saved_paths = []
        save_dir = Path(args.save_dir).expanduser().resolve()
        for employee_id in employee_ids:
            print(f"Verifying employee {employee_id} ...")
            item = run_for_employee(employee_id)
            payload.append(item)
            saved_path = save_employee_result(item, save_dir)
            if saved_path is not None:
                saved_paths.append(saved_path)

        if args.output == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            print(
                json.dumps(
                    {
                        "saved_count": len(saved_paths),
                        "saved_paths": [str(path) for path in saved_paths],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        for item in payload:
            print("=" * 60)
            print(f"员工工号: {item['employee_id']}")
            if "long" in item:
                print("长期分析成功")
                print(json.dumps(item["long"], ensure_ascii=False, indent=2))
            else:
                print(f"长期分析失败: {item.get('long_error')}")
            if "short" in item:
                print("短期分析成功")
                print(json.dumps(item["short"], ensure_ascii=False, indent=2))
            else:
                print(f"短期分析失败: {item.get('short_error')}")

        print("=" * 60)
        print(f"JSON 保存成功数量: {len(saved_paths)}")
        if saved_paths:
            print("保存路径:")
            for path in saved_paths:
                print(f"- {path}")


if __name__ == "__main__":
    main()

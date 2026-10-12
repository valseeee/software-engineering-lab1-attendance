"""命令行签到系统。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


DEFAULT_STORE = Path(__file__).with_name("records.json")


def load_records(store_path: Path = DEFAULT_STORE) -> list[dict[str, str]]:
    if not store_path.exists():
        return []
    return json.loads(store_path.read_text(encoding="utf-8"))


def save_records(records: list[dict[str, str]], store_path: Path = DEFAULT_STORE) -> None:
    store_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def check_in(
    student_id: str,
    name: str,
    store_path: Path = DEFAULT_STORE,
    now: datetime | None = None,
) -> dict[str, str]:
    current_time = now or datetime.now()
    record = {
        "student_id": student_id,
        "name": name,
        "time": current_time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    records = load_records(store_path)
    records.append(record)
    save_records(records, store_path)
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="简单签到系统")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sign_parser = subparsers.add_parser("sign", help="新增签到记录")
    sign_parser.add_argument("student_id", help="学号")
    sign_parser.add_argument("name", help="姓名")

    subparsers.add_parser("list", help="显示全部签到记录")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "sign":
        record = check_in(args.student_id, args.name)
        print(f"签到成功：{record['student_id']} {record['name']} {record['time']}")
        return

    records = load_records()
    if not records:
        print("暂无签到记录")
        return
    print("学号\t姓名\t签到时间")
    for record in records:
        print(f"{record['student_id']}\t{record['name']}\t{record['time']}")


if __name__ == "__main__":
    main()

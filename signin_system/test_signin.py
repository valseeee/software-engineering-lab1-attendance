import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from signin import check_in, load_records


class SignInTests(unittest.TestCase):
    def test_check_in_saves_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "records.json"
            check_in(
                "20260001",
                "测试用户",
                store,
                datetime(2026, 10, 12, 9, 30, 0),
            )

            self.assertEqual(
                load_records(store),
                [
                    {
                        "student_id": "20260001",
                        "name": "测试用户",
                        "time": "2026-10-12 09:30:00",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()

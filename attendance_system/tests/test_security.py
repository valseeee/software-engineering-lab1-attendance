import os
import unittest


os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import app  # noqa: E402
from models import Admin  # noqa: E402
from security import hash_password, verify_password  # noqa: E402


class PasswordSecurityTests(unittest.TestCase):
    def test_default_admin_password_is_hashed_and_login_still_works(self):
        with app.app_context():
            admin = Admin.query.filter_by(username="root").one()
            self.assertNotEqual(admin.password, "123456")
            self.assertTrue(verify_password(admin.password, "123456"))

        response = app.test_client().post(
            "/api/login", json={"username": "root", "password": "123456"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

    def test_wrong_password_is_rejected(self):
        self.assertFalse(verify_password(hash_password("correct"), "wrong"))

    def test_legacy_plaintext_password_remains_compatible(self):
        self.assertTrue(verify_password("legacy-password", "legacy-password"))


if __name__ == "__main__":
    unittest.main()

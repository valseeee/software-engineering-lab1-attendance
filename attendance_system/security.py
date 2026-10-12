import hmac

from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_password, candidate_password):
    """Verify new hashes while keeping existing plaintext accounts usable."""
    if not stored_password or not candidate_password:
        return False
    if stored_password.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored_password, candidate_password)
    return hmac.compare_digest(stored_password, candidate_password)


from __future__ import annotations

import hashlib
import hmac
import secrets
import string

import bcrypt

API_KEY_PREFIX = "sk-"


def create_api_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return API_KEY_PREFIX + "".join(secrets.choice(alphabet) for _ in range(32))


def hash_api_key(api_key: str) -> str:
    encoded = bcrypt.hashpw(api_key.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")
    return "{bcrypt}" + encoded + "::" + api_key[-4:]


def verify_api_key(api_key: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        credential = encoded.rsplit("::", 1)[0]
        if credential.startswith("{bcrypt}"):
            credential = credential[len("{bcrypt}") :]
        if credential.startswith("$2"):
            return bcrypt.checkpw(api_key.encode("utf-8"), credential.encode("ascii"))
        if len(credential) == 64:
            # Compatibility with the first Python development snapshots.
            digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            if hmac.compare_digest(digest, credential):
                return True
        # Compatibility with the upstream project's historical plaintext rows.
        return hmac.compare_digest(api_key, encoded)
    except (ValueError, TypeError):
        return False


def masked_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if api_key.startswith("{bcrypt}") or api_key.startswith("$2"):
        return "****" + api_key.rsplit("::", 1)[1] if "::" in api_key else "****"
    return "****" + api_key[-4:] if len(api_key) > 8 else "****"

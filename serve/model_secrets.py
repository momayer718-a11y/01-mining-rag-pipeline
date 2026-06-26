from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:  # pragma: no cover - optional runtime dependency
    AESGCM = None


def encrypted_model_key(config_path: str = "config/model_api_key.enc.json") -> str:
    passphrase = os.getenv("MODEL_KEY_PASSPHRASE", "").strip()
    if not passphrase or AESGCM is None:
        return ""
    path = Path(config_path)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        salt = _b64(payload["salt"])
        nonce = _b64(payload["nonce"])
        ciphertext = _b64(payload["ciphertext"])
        key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, int(payload.get("iterations", 200000)), dklen=32)
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8").strip()
    except Exception:
        return ""


def _b64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))

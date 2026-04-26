"""사용자별 거래소 API 자격증명 안전 저장소.

- Fernet (AES-128 + HMAC) 으로 암호화하여 디스크에 저장.
- 마스터 키는 ALPHA_VAULT_KEY (Fernet 키, base64) 또는 자동 생성 후 ~/AlphaModels/.vault_key 에 보관.
- 같은 사용자는 거래소(broker)별 1개의 자격증명을 가질 수 있다.
- 응답에는 절대 raw 키를 반환하지 않는다 (마스킹된 4자리만).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Iterable, Optional

from cryptography.fernet import Fernet, InvalidToken

VAULT_FILE = os.path.expanduser("~/AlphaModels/credentials.vault")
VAULT_KEY_FILE = os.path.expanduser("~/AlphaModels/.vault_key")

SUPPORTED_BROKERS = {"alpaca", "upbit", "binance", "kis", "anthropic"}
# anthropic은 LLM 파서용 — 거래소는 아니지만 같은 vault에 저장한다.


def _load_master_key() -> bytes:
    env = os.getenv("ALPHA_VAULT_KEY")
    if env:
        return env.encode() if isinstance(env, str) else env
    if os.path.exists(VAULT_KEY_FILE):
        with open(VAULT_KEY_FILE, "rb") as f:
            return f.read().strip()
    os.makedirs(os.path.dirname(VAULT_KEY_FILE), exist_ok=True)
    key = Fernet.generate_key()
    with open(VAULT_KEY_FILE, "wb") as f:
        f.write(key)
    os.chmod(VAULT_KEY_FILE, 0o600)
    return key


_fernet: Optional[Fernet] = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_master_key())
    return _fernet


def _load_vault() -> dict:
    if not os.path.exists(VAULT_FILE):
        return {}
    try:
        with open(VAULT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_vault(vault: dict) -> None:
    os.makedirs(os.path.dirname(VAULT_FILE), exist_ok=True)
    with open(VAULT_FILE, "w", encoding="utf-8") as f:
        json.dump(vault, f, indent=2)
    os.chmod(VAULT_FILE, 0o600)


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def store_credentials(username: str, broker: str, fields: dict[str, str]) -> dict:
    """사용자의 broker 자격증명을 암호화 저장. 기존 항목은 덮어씀."""
    broker = broker.lower()
    if broker not in SUPPORTED_BROKERS:
        raise ValueError(f"지원하지 않는 broker: {broker}")
    if not fields:
        raise ValueError("fields가 비어 있습니다.")

    blob = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    encrypted = _cipher().encrypt(blob).decode("utf-8")

    vault = _load_vault()
    user_slot = vault.setdefault(username, {})
    user_slot[broker] = {
        "ciphertext": encrypted,
        "fingerprint": secrets.token_hex(4),
        "fields_preview": {k: _mask(str(v)) for k, v in fields.items()},
    }
    _save_vault(vault)
    return public_view(username, broker)


def get_credentials(username: str, broker: str) -> Optional[dict[str, str]]:
    vault = _load_vault()
    record = vault.get(username, {}).get(broker.lower())
    if not record:
        return None
    try:
        decrypted = _cipher().decrypt(record["ciphertext"].encode("utf-8"))
    except InvalidToken:
        return None
    return json.loads(decrypted.decode("utf-8"))


def delete_credentials(username: str, broker: str) -> bool:
    vault = _load_vault()
    user_slot = vault.get(username, {})
    if broker.lower() in user_slot:
        del user_slot[broker.lower()]
        _save_vault(vault)
        return True
    return False


def list_brokers(username: str) -> list[dict]:
    vault = _load_vault()
    return [public_view(username, b) for b in vault.get(username, {}).keys()]


def public_view(username: str, broker: str) -> dict:
    """raw 키 없이 안전하게 노출 가능한 메타데이터."""
    vault = _load_vault()
    record = vault.get(username, {}).get(broker.lower())
    if not record:
        return {"broker": broker, "exists": False}
    return {
        "broker": broker,
        "exists": True,
        "fingerprint": record.get("fingerprint"),
        "fields_preview": record.get("fields_preview", {}),
    }


def required_fields(broker: str) -> list[str]:
    """각 broker가 요구하는 필드 키 목록."""
    return {
        "alpaca": ["api_key", "api_secret", "base_url"],
        "upbit": ["access_key", "secret_key"],
        "binance": ["api_key", "api_secret"],
        "kis": ["app_key", "app_secret", "account_no", "account_product_code"],
        "anthropic": ["api_key"],
    }[broker.lower()]

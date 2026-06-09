import os
import hashlib
import base64
from cryptography.fernet import Fernet


class EncryptionService:

    @staticmethod
    def _get_key() -> bytes:
        raw = os.environ.get("ENCRYPTION_KEY", "").encode()
        if not raw:
            from config import config
            raw = config.BOT_TOKEN.encode()
        key = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(key)

    @staticmethod
    def encrypt(plaintext: str) -> str:
        f = Fernet(EncryptionService._get_key())
        return f.encrypt(plaintext.encode()).decode()

    @staticmethod
    def decrypt(ciphertext: str) -> str:
        f = Fernet(EncryptionService._get_key())
        return f.decrypt(ciphertext.encode()).decode()

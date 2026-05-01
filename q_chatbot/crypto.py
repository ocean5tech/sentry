"""企业微信消息加解密 (AES-256-CBC + SHA1 验签)"""
import base64
import hashlib
import struct
from Crypto.Cipher import AES


class WeChatCrypto:
    def __init__(self, token: str, aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        self.aes_key = base64.b64decode(aes_key + "=")  # 43→44 chars, 32 bytes

    def verify(self, timestamp: str, nonce: str, signature: str, msg: str = "") -> bool:
        parts = sorted([self.token, timestamp, nonce] + ([msg] if msg else []))
        return hashlib.sha1("".join(parts).encode()).hexdigest() == signature

    def decrypt(self, encrypted: str) -> str:
        raw = base64.b64decode(encrypted)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.aes_key[:16])
        plain = cipher.decrypt(raw)
        plain = plain[: -plain[-1]]          # PKCS7 unpad
        msg_len = struct.unpack(">I", plain[16:20])[0]
        return plain[20: 20 + msg_len].decode("utf-8")

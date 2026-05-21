import base64
import random
import string
import threading
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from .LogUtils import LogUtils


class AesCoder:
    ALGORITHMTYPE = "AES"
    CIPHER_MODE = "AES/CBC/NoPadding"
    HEX = "0123456789ABCDEF"
    KEY = "<aes@leelen.com>"
    KEY_BYTES = KEY.encode('utf-8')
    TAG = "AesCoder"
    _lock = threading.Lock()

    _instance = None

    @classmethod
    def get_instance(cls) -> 'AesCoder':
        with cls._lock:
            if not cls._instance:
                cls._instance = AesCoder()
            return cls._instance

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AesCoder, cls).__new__(cls)
        return cls._instance

    @staticmethod
    def append_hex(string_buffer: str, byte: int) -> str:
        return string_buffer + AesCoder.HEX[(byte >> 4) & 0x0F] + AesCoder.HEX[byte & 0x0F]

    @staticmethod
    def decrypt(encrypted_data: str, key: str) -> str:
        try:
            raw_key = AesCoder.get_raw_key(key.encode('utf-8'))
            bytes_data = AesCoder.to_byte(encrypted_data)
            decrypted = AesCoder._decrypt(raw_key, bytes_data)
            return decrypted.decode('utf-8')
        except Exception as e:
            LogUtils.d(f"{AesCoder.TAG} decrypt Exception: {str(e)}")
            return ""

    @staticmethod
    def _decrypt(key: bytes, encrypted_data: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted_data) + decryptor.finalize()
        unpadder = padding.PKCS7(16).unpadder()
        return unpadder.update(decrypted) + unpadder.finalize()

    @staticmethod
    def encrypt(data: str) -> str:
        if not data:
            return ""
        try:
            encrypted = AesCoder._encrypt(AesCoder.KEY_BYTES, data.encode('ISO-8859-1'))
            return encrypted.decode('ISO-8859-1')
        except Exception as e:
            LogUtils.d(f"{AesCoder.TAG} encrypt Exception: {str(e)}")
            return ""

    @staticmethod
    def encrypt_with_key(data: str, key: str) -> str:
        try:
            raw_key = AesCoder.get_raw_key(key.encode('utf-8'))
            encrypted = AesCoder._encrypt(raw_key, data.encode('utf-8'))
            return AesCoder.to_hex(encrypted)
        except Exception as e:
            LogUtils.d(f"{AesCoder.TAG} encrypt_with_key Exception: {str(e)}")
            return ""

    @staticmethod
    def _encrypt(key: bytes, data: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
        encryptor = cipher.encryptor()
        padder = padding.PKCS7(16).padder()
        padded_data = padder.update(data) + padder.finalize()
        return encryptor.update(padded_data) + encryptor.finalize()

    @staticmethod
    def encrypt_log(data: str) -> str:
        if not data:
            return ""
        try:
            encrypted = AesCoder._encrypt(AesCoder.KEY_BYTES, data.encode('utf-8'))
            return base64.b64encode(encrypted).decode('utf-8')
        except Exception as e:
            LogUtils.d(f"{AesCoder.TAG} encrypt_log Exception: {str(e)}")
            return ""

    @staticmethod
    def from_hex(hex_str: str) -> str:
        return AesCoder.to_byte(hex_str).decode('utf-8')

    @staticmethod
    def get_raw_key(seed: bytes) -> bytes:
        # 使用PBKDF2替代原来的实现
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA1(),
            length=16,
            salt=b'',
            iterations=1,
            backend=default_backend()
        )
        return kdf.derive(seed)

    @staticmethod
    def get_secret(length: int) -> str:
        chars = string.ascii_letters + string.digits
        return ''.join(random.choice(chars) for _ in range(length))

    @staticmethod
    def http_decrypt(encrypted_data: str, key: str) -> str:
        try:
            cipher = Cipher(algorithms.AES(key.encode('utf-8')), modes.ECB(), backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(base64.b64decode(encrypted_data)) + decryptor.finalize()
            return decrypted.decode('utf-8').rstrip('\x00').replace("��", "")
        except Exception as e:
            LogUtils.d(f"{AesCoder.TAG} http_decrypt Exception: {str(e)}")
            return ""

    @staticmethod
    def http_encrypt(data: str, key: str) -> str:
        """AES/ECB/NoPadding 加密 (兼容Java版本)

        Args:
            data: 要加密的原始字符串
            key:  加密密钥 (UTF-8字符串)

        Returns:
            Base64编码的加密结果
        """
        try:
            # 1. 初始化加密器
            cipher = Cipher(algorithms.AES(key.encode('utf-8')), modes.ECB(), backend=default_backend())
            encryptor = cipher.encryptor()
            block_size = 16  # AES固定为16字节

            # 2. 处理明文填充
            data_bytes = data.encode('utf-8')
            data_len = len(data_bytes)

            # 计算需要填充的长度
            padded_len = data_len + (block_size - data_len % block_size) if data_len % block_size != 0 else data_len

            # 创建填充缓冲区并拷贝数据
            padded_data = bytearray(padded_len)
            padded_data[:data_len] = data_bytes

            # 3. 执行加密
            encrypted = encryptor.update(bytes(padded_data)) + encryptor.finalize()

            # 4. Base64编码结果
            return base64.b64encode(encrypted).decode('utf-8')

        except Exception as e:
            LogUtils.d(f"[ERROR] httpEncrypt Exception: {str(e)}")
            return ""

    @staticmethod
    def to_byte(hex_str: str) -> bytes:
        length = len(hex_str) // 2
        return bytes(int(hex_str[i * 2:i * 2 + 2], 16) for i in range(length))

    @staticmethod
    def to_hex(data: str) -> str:
        return AesCoder.to_hex_bytes(data.encode('utf-8'))

    @staticmethod
    def to_hex_bytes(data: bytes) -> str:
        if not data:
            return ""
        return ''.join(f"{byte:02X}" for byte in data)

    @staticmethod
    def wallet_decrypt(encrypted_data: str, key: str) -> str:
        try:
            cipher = Cipher(algorithms.AES(key.encode('utf-8')), modes.ECB(), backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(base64.b64decode(encrypted_data)) + decryptor.finalize()
            unpadder = padding.PKCS7(16).unpadder()
            unpadded = unpadder.update(decrypted) + unpadder.finalize()
            return unpadded.decode('utf-8')
        except Exception as e:
            LogUtils.d(f"{AesCoder.TAG} wallet_decrypt Exception: {str(e)}")
            return ""

    @staticmethod
    def wallet_encrypt(data: str, key: str) -> str:
        try:
            cipher = Cipher(algorithms.AES(key.encode('utf-8')), modes.ECB(), backend=default_backend())
            encryptor = cipher.encryptor()
            padder = padding.PKCS7(16).padder()
            padded_data = padder.update(data.encode('utf-8')) + padder.finalize()
            encrypted = encryptor.update(padded_data) + encryptor.finalize()
            return base64.b64encode(encrypted).decode('utf-8')
        except Exception as e:
            LogUtils.d(f"{AesCoder.TAG} wallet_encrypt Exception: {str(e)}")
            return ""

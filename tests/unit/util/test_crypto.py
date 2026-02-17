import pytest
from cryptography.fernet import InvalidToken

from util.crypto import decrypt_payload, encrypt_payload


class TestCrypto:
    def test_encrypt_decrypt_roundtrip(self):
        payload = {"key": "value", "foo": 123}
        token = "secret-token"

        encrypted = encrypt_payload(payload, token)
        assert isinstance(encrypted, str)
        assert encrypted != ""

        decrypted = decrypt_payload(encrypted, token)
        assert decrypted == payload

    def test_decrypt_with_wrong_token_fails(self):
        payload = {"key": "value"}
        token = "secret-token"
        wrong_token = "wrong-token"

        encrypted = encrypt_payload(payload, token)
        with pytest.raises(InvalidToken):
            decrypt_payload(encrypted, wrong_token)

    def test_decrypt_invalid_blob_fails(self):
        token = "secret-token"
        with pytest.raises(InvalidToken):
            decrypt_payload("not-a-fernet-blob", token)

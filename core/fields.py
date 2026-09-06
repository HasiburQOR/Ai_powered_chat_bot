import base64
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def _fernet():
    key = settings.FIELD_ENCRYPTION_KEY
    # Accept both a raw Fernet key and a base64-encoded 32-byte key.
    try:
        return Fernet(key)
    except Exception:
        return Fernet(base64.urlsafe_b64encode(key.encode()[:32]).decode())


class EncryptedTextField(models.TextField):
    """
    Transparently encrypts the stored value with Fernet (AES-128-CBC + HMAC).
    Never store secrets in plaintext.
    """

    description = 'Encrypted text'

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # Value was written before encryption was enabled, or key changed.
            return value

    def get_prep_value(self, value):
        if value in (None, ''):
            return value
        return _fernet().encrypt(str(value).encode()).decode()


class EncryptedJSONField(EncryptedTextField):
    """Encrypts a JSON-serializable value at rest; presents it as Python data."""

    description = 'Encrypted JSON'

    def from_db_value(self, value, expression, connection):
        decrypted = super().from_db_value(value, expression, connection)
        if decrypted in (None, ''):
            return decrypted
        try:
            return json.loads(decrypted)
        except (TypeError, json.JSONDecodeError):
            return decrypted

    def get_prep_value(self, value):
        if value in (None, ''):
            return value
        return super().get_prep_value(json.dumps(value))

    def validate(self, value, model_instance):
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            raise ValidationError(self.error_messages['invalid'], code='invalid', params={'value': value})
        super().validate(value, model_instance)

    def value_to_string(self, obj):
        return self.value_from_object(obj)

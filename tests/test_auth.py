import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException
from pwdlib import PasswordHash

from api.auth import (
    ALGORITHM,
    AuthConfigurationError,
    AuthSettings,
    authenticate_credentials,
    decode_access_token,
    get_current_user,
    issue_access_token,
)


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "correct horse battery staple"
        cls.password_hash = PasswordHash.recommended().hash(cls.password)
        cls.settings = AuthSettings(
            username="demo",
            user_id="demo-user",
            password_hash=cls.password_hash,
            jwt_secret="s" * 48,
            token_expire_minutes=15,
            issuer="test-issuer",
            audience="test-audience",
        )

    def test_settings_require_credentials_and_strong_secret(self):
        with self.assertRaises(AuthConfigurationError):
            AuthSettings.from_env({})

        values = {
            "AUTH_USERNAME": "demo",
            "AUTH_USER_ID": "demo-user",
            "AUTH_PASSWORD_HASH": self.password_hash,
            "JWT_SECRET_KEY": "too-short",
        }
        with self.assertRaises(AuthConfigurationError):
            AuthSettings.from_env(values)

        values["JWT_SECRET_KEY"] = "<random-secret-at-least-32-bytes>"
        with self.assertRaises(AuthConfigurationError):
            AuthSettings.from_env(values)

        values["JWT_SECRET_KEY"] = "s" * 48
        values["AUTH_PASSWORD_HASH"] = "not-an-argon2-hash"
        with self.assertRaises(AuthConfigurationError):
            AuthSettings.from_env(values)

    def test_credentials_and_token_round_trip(self):
        principal = authenticate_credentials(
            "demo", self.password, self.settings
        )
        self.assertIsNotNone(principal)
        self.assertEqual(principal.subject, "demo-user")

        now = datetime.now(timezone.utc)
        token = issue_access_token(
            principal.subject,
            self.settings,
            now=now,
        )
        claims = jwt.decode(
            token,
            self.settings.jwt_secret,
            algorithms=[ALGORITHM],
            issuer=self.settings.issuer,
            audience=self.settings.audience,
        )
        self.assertEqual(claims["sub"], "demo-user")
        self.assertIn("iat", claims)
        self.assertIn("exp", claims)
        principal = decode_access_token(token, self.settings)
        self.assertEqual(principal.subject, "demo-user")
        self.assertEqual(principal.expires_at, float(claims["exp"]))

    def test_wrong_credentials_are_rejected(self):
        self.assertIsNone(
            authenticate_credentials("wrong-user", self.password, self.settings)
        )
        self.assertIsNone(
            authenticate_credentials("demo", "wrong-password", self.settings)
        )

    def test_expired_token_is_rejected(self):
        token = issue_access_token(
            "demo-user",
            self.settings,
            now=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        with self.assertRaises(ValueError):
            decode_access_token(token, self.settings)

    def test_algorithm_tampering_is_rejected(self):
        payload = {
            "sub": "demo-user",
            "iss": self.settings.issuer,
            "aud": self.settings.audience,
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
        token = jwt.encode(payload, key="", algorithm="none")
        with self.assertRaises(ValueError):
            decode_access_token(token, self.settings)

    def test_missing_required_claim_is_rejected(self):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "demo-user",
            "iss": self.settings.issuer,
            "aud": self.settings.audience,
            "iat": int(now.timestamp()),
        }
        token = jwt.encode(
            payload,
            self.settings.jwt_secret,
            algorithm=ALGORITHM,
        )
        with self.assertRaises(ValueError):
            decode_access_token(token, self.settings)

    def test_wrong_issuer_audience_and_subject_are_rejected(self):
        token = issue_access_token("demo-user", self.settings)
        wrong_issuer = AuthSettings(**{**self.settings.__dict__, "issuer": "other"})
        wrong_audience = AuthSettings(
            **{**self.settings.__dict__, "audience": "other"}
        )
        wrong_subject = AuthSettings(
            **{**self.settings.__dict__, "user_id": "other-user"}
        )
        with self.assertRaises(ValueError):
            decode_access_token(token, wrong_issuer)
        with self.assertRaises(ValueError):
            decode_access_token(token, wrong_audience)
        with self.assertRaises(ValueError):
            decode_access_token(token, wrong_subject)

    def test_bearer_dependency_rejects_missing_and_invalid_tokens(self):
        with self.assertRaises(HTTPException) as missing:
            get_current_user(None)
        self.assertEqual(missing.exception.status_code, 401)
        self.assertEqual(missing.exception.headers["WWW-Authenticate"], "Bearer")

        with self.assertRaises(ValueError):
            decode_access_token("not-a-token", self.settings)


if __name__ == "__main__":
    unittest.main()

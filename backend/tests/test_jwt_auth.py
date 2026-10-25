import time
import unittest
from uuid import uuid4
import jwt
from fastapi import HTTPException
from app.config import settings
from app.auth.jwt import verify_supabase_jwt

# Use a test HS256 secret for deterministic local unit testing
TEST_SECRET = "super-secret-test-key-with-sufficient-length-for-hmac-sha256"


class TestJWTAuthentication(unittest.TestCase):
    def setUp(self):
        # Configure test secret and default leeway
        self.original_secret = settings.SUPABASE_JWT_SECRET
        self.original_leeway = settings.JWT_LEEWAY_SECONDS
        settings.SUPABASE_JWT_SECRET = TEST_SECRET
        settings.JWT_LEEWAY_SECONDS = 60

    def tearDown(self):
        settings.SUPABASE_JWT_SECRET = self.original_secret
        settings.JWT_LEEWAY_SECONDS = self.original_leeway

    def _generate_token(self, iat_offset: int = 0, exp_offset: int = 3600, aud: str = "authenticated", user_id: str = None):
        now = int(time.time())
        user_uuid = user_id or str(uuid4())
        payload = {
            "sub": user_uuid,
            "email": "test.user@manualmind.internal",
            "role": "authenticated",
            "aud": aud,
            "iat": now + iat_offset,
            "exp": now + exp_offset,
        }
        return jwt.encode(payload, TEST_SECRET, algorithm="HS256"), user_uuid

    def test_clock_skew_slight_future_iat_accepted_with_leeway(self):
        """
        Reproduces the issue: Token issued by cloud server (Supabase) is 5 seconds ahead
        of local machine clock. With leeway=60, this token must be accepted.
        """
        token, user_uuid = self._generate_token(iat_offset=5, exp_offset=3600)
        auth_user = verify_supabase_jwt(token)
        self.assertEqual(str(auth_user.id), user_uuid)
        self.assertEqual(auth_user.email, "test.user@manualmind.internal")

    def test_clock_skew_beyond_leeway_rejected(self):
        """
        Tokens with iat far in the future (> leeway tolerance, e.g. 5 minutes)
        must still be strictly rejected with HTTP 401.
        """
        token, _ = self._generate_token(iat_offset=300, exp_offset=3600)
        with self.assertRaises(HTTPException) as ctx:
            verify_supabase_jwt(token)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("clock skew exceeded", ctx.exception.detail)

    def test_expired_token_rejected_even_with_leeway(self):
        """
        Tokens expired beyond the leeway window must be strictly rejected.
        """
        token, _ = self._generate_token(iat_offset=-3600, exp_offset=-120)
        with self.assertRaises(HTTPException) as ctx:
            verify_supabase_jwt(token)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("expired", ctx.exception.detail)

    def test_invalid_signature_rejected(self):
        """
        Tokens signed with a different key must be strictly rejected.
        """
        now = int(time.time())
        payload = {
            "sub": str(uuid4()),
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
        }
        tampered_token = jwt.encode(payload, "wrong-unauthorized-key-with-long-length", algorithm="HS256")
        with self.assertRaises(HTTPException) as ctx:
            verify_supabase_jwt(tampered_token)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("cryptographic verification failed", ctx.exception.detail)

    def test_invalid_audience_rejected(self):
        """
        Tokens with audience other than 'authenticated' must be rejected.
        """
        token, _ = self._generate_token(aud="unauthorized-service")
        with self.assertRaises(HTTPException) as ctx:
            verify_supabase_jwt(token)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("audience", ctx.exception.detail)

    def test_invalid_uuid_rejected(self):
        """
        Tokens with invalid UUID sub claim must be rejected.
        """
        token, _ = self._generate_token(user_id="not-a-valid-uuid")
        with self.assertRaises(HTTPException) as ctx:
            verify_supabase_jwt(token)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Invalid user UUID", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()

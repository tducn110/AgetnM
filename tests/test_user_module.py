#!/usr/bin/env python3
"""
Unit and Integration Test Suite for AgentMesh User Module
Verifies:
1. Security & Password Hashing (PBKDF2-HMAC-SHA256, Salt isolation, No plaintext leak)
2. Registration Validation (Username, Email format, Password length, Uniqueness)
3. Authentication & Session Tokens (HMAC signature, Anti-tamper, Expiry, Suspended status)
4. User CRUD & Atomic JSON Persistence across reloads
5. Full REST API Lifecycle (/api/auth/register, /api/auth/login, /api/auth/me, /api/users, RBAC)
"""

import os
import sys
import time
import json
import shutil
import tempfile
import unittest
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

# Add workspace root to sys.path
sys.path.insert(0, os.path.abspath("."))

from core.user import UserManager, UserRole, User, UserSession
from server import MeshRequestHandler, user_manager as server_user_manager


class TestUserSecurity(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_users.json")
        self.mgr = UserManager(storage_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_salted_hashing_uniqueness(self):
        """Identical passwords with different salts must yield completely different hashes."""
        pw = "SuperSecretPassword123!"
        salt1 = "salt_alpha_001"
        salt2 = "salt_beta_002"
        h1 = self.mgr.hash_password(pw, salt1)
        h2 = self.mgr.hash_password(pw, salt2)
        self.assertNotEqual(h1, h2, "Salts must diversify hash output")
        self.assertEqual(len(h1), 64, "SHA-256 output must be 64 hex characters")

    def test_no_plaintext_password_in_safe_dict(self):
        """User.to_dict(safe=True) must NEVER expose password_hash or salt."""
        u = self.mgr.register_user(
            username="security_tester",
            email="sec@agentmesh.local",
            password="SecurePassword999!",
            role=UserRole.OPERATOR.value
        )
        safe_dict = u.to_dict(safe=True)
        self.assertNotIn("password_hash", safe_dict)
        self.assertNotIn("salt", safe_dict)
        self.assertIn("username", safe_dict)
        self.assertIn("user_id", safe_dict)


class TestUserRegistrationAndValidation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_users.json")
        self.mgr = UserManager(storage_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_valid_registration(self):
        """Valid registration creates active user with proper attributes."""
        u = self.mgr.register_user(
            username="alice_dev",
            email="alice@mesh.internal",
            password="StrongPassword123!",
            role=UserRole.OPERATOR.value
        )
        self.assertTrue(u.user_id.startswith("usr_"))
        self.assertEqual(u.username, "alice_dev")
        self.assertEqual(u.email, "alice@mesh.internal")
        self.assertEqual(u.role, UserRole.OPERATOR.value)
        self.assertEqual(u.status, "active")

    def test_invalid_username_rejected(self):
        """Usernames with symbols, spaces or < 3 chars must be rejected."""
        with self.assertRaises(ValueError):
            self.mgr.register_user("ab", "ab@test.com", "Password123!")
        with self.assertRaises(ValueError):
            self.mgr.register_user("alice with space", "alice@test.com", "Password123!")
        with self.assertRaises(ValueError):
            self.mgr.register_user("alice@evil#", "alice@test.com", "Password123!")

    def test_invalid_email_rejected(self):
        """Malformed emails must be rejected."""
        with self.assertRaises(ValueError):
            self.mgr.register_user("bob_dev", "not-an-email", "Password123!")
        with self.assertRaises(ValueError):
            self.mgr.register_user("bob_dev", "bob@", "Password123!")

    def test_weak_password_rejected(self):
        """Passwords under 8 characters must be rejected."""
        with self.assertRaises(ValueError):
            self.mgr.register_user("charlie", "charlie@test.com", "short")

    def test_duplicate_username_and_email_rejected(self):
        """Duplicate usernames or emails (case-insensitive) must be rejected."""
        self.mgr.register_user("dan_user", "dan@test.com", "Password123!")
        with self.assertRaises(ValueError):
            self.mgr.register_user("DAN_USER", "other@test.com", "Password123!")
        with self.assertRaises(ValueError):
            self.mgr.register_user("other_user", "DAN@test.com", "Password123!")


class TestAuthenticationAndTokens(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_users.json")
        self.mgr = UserManager(storage_path=self.db_path)
        self.user = self.mgr.register_user(
            username="eve_tester",
            email="eve@mesh.internal",
            password="EvePassword123!",
            role=UserRole.VIEWER.value
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_successful_auth(self):
        """Authenticating by username or email yields valid UserSession with token."""
        # By username
        sess1 = self.mgr.authenticate("eve_tester", "EvePassword123!")
        self.assertIsNotNone(sess1)
        self.assertEqual(sess1.username, "eve_tester")

        # By email
        sess2 = self.mgr.authenticate("eve@mesh.internal", "EvePassword123!")
        self.assertIsNotNone(sess2)
        self.assertEqual(sess2.username, "eve_tester")

        # Verify token recovers user
        recovered = self.mgr.verify_token(sess1.token)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.user_id, self.user.user_id)

    def test_failed_auth(self):
        """Wrong password or non-existent user returns None."""
        self.assertIsNone(self.mgr.authenticate("eve_tester", "WrongPassword!"))
        self.assertIsNone(self.mgr.authenticate("ghost_user", "SomePassword!"))

    def test_token_tampering_rejected(self):
        """Modifying token payload or signature must cause verify_token to reject."""
        sess = self.mgr.authenticate("eve_tester", "EvePassword123!")
        parts = sess.token.split(":")
        # Elevate role in payload: parts[2] = "admin"
        tampered_token = f"{parts[0]}:{parts[1]}:admin:{parts[3]}:{parts[4]}"
        self.assertIsNone(self.mgr.verify_token(tampered_token), "Tampered payload must fail signature check")

        # Corrupt signature
        bad_sig_token = f"{parts[0]}:{parts[1]}:{parts[2]}:{parts[3]}:00000000deadbeef"
        self.assertIsNone(self.mgr.verify_token(bad_sig_token), "Bad signature must be rejected")

    def test_expired_token_rejected(self):
        """Token with past expiration must be rejected."""
        # Issue token with ttl=-10 seconds
        token = self.mgr.generate_token(self.user, ttl_seconds=-10)
        self.assertIsNone(self.mgr.verify_token(token), "Expired token must be rejected")

    def test_suspended_user_token_rejected(self):
        """If user status changes to suspended, valid token is rejected."""
        sess = self.mgr.authenticate("eve_tester", "EvePassword123!")
        self.assertIsNotNone(self.mgr.verify_token(sess.token))

        # Suspend user
        self.mgr.update_user(self.user.user_id, {"status": "suspended"})
        self.assertIsNone(self.mgr.verify_token(sess.token), "Suspended user token must be denied")


class TestUserCRUDAndPersistence(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_users.json")
        self.mgr = UserManager(storage_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_update_and_delete_user(self):
        """Updating user attributes and deletion work as expected."""
        u = self.mgr.register_user("frank", "frank@test.com", "Password123!")
        updated = self.mgr.update_user(u.user_id, {
            "email": "frank_new@test.com",
            "role": UserRole.OPERATOR.value
        })
        self.assertEqual(updated.email, "frank_new@test.com")
        self.assertEqual(updated.role, UserRole.OPERATOR.value)

        # Delete
        self.assertTrue(self.mgr.delete_user(u.user_id))
        self.assertIsNone(self.mgr.get_user(u.user_id))

    def test_atomic_persistence_across_instances(self):
        """Data persisted to disk is completely recovered in new UserManager instance."""
        u1 = self.mgr.register_user("user_one", "one@test.com", "Password123!", role=UserRole.OPERATOR.value)
        u2 = self.mgr.register_user("user_two", "two@test.com", "Password123!", role=UserRole.VIEWER.value)

        # Create fresh instance pointing to same file
        new_mgr = UserManager(storage_path=self.db_path)
        self.assertEqual(len(new_mgr.users), len(self.mgr.users))

        loaded_u1 = new_mgr.get_user(u1.user_id)
        self.assertIsNotNone(loaded_u1)
        self.assertEqual(loaded_u1.username, "user_one")
        self.assertEqual(loaded_u1.role, UserRole.OPERATOR.value)

        # Authenticate on new instance with original credentials
        sess = new_mgr.authenticate("user_one", "Password123!")
        self.assertIsNotNone(sess)


class TestServerRestApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Start a test server on an ephemeral port
        cls.server_dir = tempfile.mkdtemp()
        cls.test_db = os.path.join(cls.server_dir, "server_test_users.json")
        # Swap server_user_manager storage for test isolation
        server_user_manager.storage_path = cls.test_db
        server_user_manager.users.clear()
        server_user_manager._bootstrap_admin()

        cls.port = 8429
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), MeshRequestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.server_dir, ignore_errors=True)

    def _post(self, path, payload, headers=None):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        req = Request(url, data=data, headers=req_headers, method="POST")
        try:
            with urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path, headers=None):
        url = f"{self.base_url}{path}"
        req_headers = {}
        if headers:
            req_headers.update(headers)
        req = Request(url, headers=req_headers, method="GET")
        try:
            with urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_full_rest_auth_lifecycle(self):
        """End-to-end test of register -> login -> /api/auth/me -> /api/users with RBAC."""
        # 1. Register a new user
        code, reg = self._post("/api/auth/register", {
            "username": "api_tester",
            "email": "tester@agentmesh.local",
            "password": "ApiTester123!",
            "role": "operator"
        })
        self.assertEqual(code, 201)
        self.assertTrue(reg.get("success"))
        token = reg.get("token")
        self.assertIsNotNone(token)

        # 2. Login
        code, login = self._post("/api/auth/login", {
            "username": "api_tester",
            "password": "ApiTester123!"
        })
        self.assertEqual(code, 200)
        self.assertTrue(login.get("success"))
        auth_token = login.get("token")

        # 3. /api/auth/me with valid Bearer token
        code, me = self._get("/api/auth/me", headers={"Authorization": f"Bearer {auth_token}"})
        self.assertEqual(code, 200)
        self.assertEqual(me.get("user", {}).get("username"), "api_tester")

        # 4. /api/auth/me without token -> 401
        code, me_fail = self._get("/api/auth/me")
        self.assertEqual(code, 401)

        # 5. /api/users list accessed by Operator -> 200
        code, users_list = self._get("/api/users", headers={"Authorization": f"Bearer {auth_token}"})
        self.assertEqual(code, 200)
        self.assertTrue(len(users_list.get("users", [])) >= 2)  # admin + api_tester

        # 6. Register a Viewer and confirm 403 on /api/users/update
        code, v_reg = self._post("/api/auth/register", {
            "username": "viewer_user",
            "email": "viewer@agentmesh.local",
            "password": "ViewerPassword123!",
            "role": "viewer"
        })
        v_token = v_reg.get("token")

        # Viewer attempts to list users -> 403
        code, forbidden = self._get("/api/users", headers={"Authorization": f"Bearer {v_token}"})
        self.assertEqual(code, 403)


if __name__ == "__main__":
    print("==========================================================")
    print("    RUNNING AGENT MESH USER MODULE TEST SUITE             ")
    print("==========================================================")
    unittest.main(verbosity=2)

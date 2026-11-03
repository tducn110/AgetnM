"""
AgentMesh User Module & Authentication Service
Provides:
- User Identity, Roles (Admin, Operator, Viewer) & Password Security
- Salted PBKDF2-HMAC-SHA256 Password Hashing (100,000 iterations)
- HMAC-SHA256 Session Token Generation & Expiration Checking
- Thread-safe Atomic JSON Persistence
- Role-Based Access Control (RBAC) Validation
"""

import os
import re
import json
import time
import hmac
import secrets
import hashlib
import threading
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass, asdict

DEFAULT_USERS_FILE = os.path.expanduser("~/.config/agent-mesh/users.json")
SECRET_KEY_FILE = os.path.expanduser("~/.config/agent-mesh/.auth_secret")

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


@dataclass
class User:
    user_id: str
    username: str
    email: str
    password_hash: str
    salt: str
    role: str
    status: str  # "active" | "suspended"
    created_at: float
    updated_at: float

    def to_dict(self, safe: bool = True) -> Dict[str, Any]:
        data = asdict(self)
        if safe:
            data.pop("password_hash", None)
            data.pop("salt", None)
        return data


@dataclass
class UserSession:
    session_id: str
    user_id: str
    username: str
    role: str
    token: str
    expires_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UserManager:
    def __init__(self, storage_path: str = DEFAULT_USERS_FILE):
        self.storage_path = storage_path
        self.lock = threading.RLock()
        self.users: Dict[str, User] = {}  # keyed by user_id
        self.secret_key = self._get_or_create_secret()
        self.load()
        if not self.users:
            self._bootstrap_admin()

    def _get_or_create_secret(self) -> str:
        os.makedirs(os.path.dirname(SECRET_KEY_FILE), exist_ok=True)
        if os.path.exists(SECRET_KEY_FILE):
            try:
                with open(SECRET_KEY_FILE, "r") as f:
                    secret = f.read().strip()
                if secret and len(secret) >= 32:
                    return secret
            except Exception:
                pass
        new_secret = secrets.token_hex(32)
        try:
            with open(SECRET_KEY_FILE, "w") as f:
                f.write(new_secret)
        except Exception:
            pass
        return new_secret

    def hash_password(self, password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000
        ).hex()

    def generate_token(self, user: User, ttl_seconds: int = 86400) -> str:
        expires_at = int(time.time() + ttl_seconds)
        payload = f"{user.user_id}:{user.username}:{user.role}:{expires_at}"
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"{payload}:{sig}"

    def verify_token(self, token: str) -> Optional[User]:
        if not token or not isinstance(token, str):
            return None
        parts = token.split(":")
        if len(parts) != 5:
            return None

        user_id, username, role, expires_str, sig = parts
        try:
            expires_at = int(expires_str)
        except ValueError:
            return None

        if time.time() > expires_at:
            return None  # Token expired

        expected_payload = f"{user_id}:{username}:{role}:{expires_at}"
        expected_sig = hmac.new(
            self.secret_key.encode("utf-8"),
            expected_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(sig, expected_sig):
            return None  # Signature tampered

        user = self.get_user(user_id)
        if not user or user.status != "active":
            return None
        return user

    def register_user(
        self,
        username: str,
        email: str,
        password: str,
        role: str = UserRole.VIEWER.value
    ) -> User:
        username = (username or "").strip()
        email = (email or "").strip().lower()

        if not USERNAME_REGEX.match(username):
            raise ValueError(
                "Invalid username. Must be 3-32 characters and contain only letters, numbers, underscores, or hyphens."
            )
        if not EMAIL_REGEX.match(email):
            raise ValueError("Invalid email format.")
        if not password or len(password) < 8:
            raise ValueError("Password must be at least 8 characters long.")

        valid_roles = [r.value for r in UserRole]
        if role not in valid_roles:
            raise ValueError(f"Invalid role '{role}'. Allowed roles: {valid_roles}")

        with self.lock:
            # Check uniqueness
            for u in self.users.values():
                if u.username.lower() == username.lower():
                    raise ValueError(f"Username '{username}' is already taken.")
                if u.email.lower() == email:
                    raise ValueError(f"Email '{email}' is already registered.")

            user_id = f"usr_{secrets.token_hex(6)}"
            salt = secrets.token_hex(16)
            password_hash = self.hash_password(password, salt)
            now = time.time()

            new_user = User(
                user_id=user_id,
                username=username,
                email=email,
                password_hash=password_hash,
                salt=salt,
                role=role,
                status="active",
                created_at=now,
                updated_at=now
            )

            self.users[user_id] = new_user
            self.save()
            return new_user

    def authenticate(
        self,
        username_or_email: str,
        password: str,
        ttl_seconds: int = 86400
    ) -> Optional[UserSession]:
        ident = (username_or_email or "").strip().lower()
        if not ident or not password:
            return None

        with self.lock:
            user = None
            for u in self.users.values():
                if u.username.lower() == ident or u.email.lower() == ident:
                    user = u
                    break

            if not user or user.status != "active":
                return None

            computed_hash = self.hash_password(password, user.salt)
            if not hmac.compare_digest(computed_hash, user.password_hash):
                return None

            token = self.generate_token(user, ttl_seconds=ttl_seconds)
            session = UserSession(
                session_id=f"sess_{secrets.token_hex(8)}",
                user_id=user.user_id,
                username=user.username,
                role=user.role,
                token=token,
                expires_at=time.time() + ttl_seconds
            )
            return session

    def get_user(self, user_id: str) -> Optional[User]:
        with self.lock:
            return self.users.get(user_id)

    def get_user_by_username(self, username: str) -> Optional[User]:
        with self.lock:
            target = username.lower().strip()
            for u in self.users.values():
                if u.username.lower() == target:
                    return u
            return None

    def list_users(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [u.to_dict(safe=True) for u in sorted(self.users.values(), key=lambda x: x.created_at)]

    def update_user(self, user_id: str, updates: Dict[str, Any]) -> User:
        with self.lock:
            user = self.users.get(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            if "email" in updates:
                new_email = updates["email"].strip().lower()
                if not EMAIL_REGEX.match(new_email):
                    raise ValueError("Invalid email format.")
                for uid, u in self.users.items():
                    if uid != user_id and u.email.lower() == new_email:
                        raise ValueError(f"Email '{new_email}' is already taken.")
                user.email = new_email

            if "role" in updates:
                valid_roles = [r.value for r in UserRole]
                if updates["role"] not in valid_roles:
                    raise ValueError(f"Invalid role. Allowed: {valid_roles}")
                user.role = updates["role"]

            if "status" in updates:
                if updates["status"] not in ["active", "suspended"]:
                    raise ValueError("Invalid status. Allowed: 'active', 'suspended'")
                user.status = updates["status"]

            if "password" in updates:
                new_pw = updates["password"]
                if len(new_pw) < 8:
                    raise ValueError("Password must be at least 8 characters.")
                new_salt = secrets.token_hex(16)
                user.salt = new_salt
                user.password_hash = self.hash_password(new_pw, new_salt)

            user.updated_at = time.time()
            self.save()
            return user

    def delete_user(self, user_id: str) -> bool:
        with self.lock:
            if user_id in self.users:
                del self.users[user_id]
                self.save()
                return True
            return False

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            tmp_path = f"{self.storage_path}.tmp"
            data = {uid: asdict(u) for uid, u in self.users.items()}
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.storage_path)

    def load(self):
        with self.lock:
            if not os.path.exists(self.storage_path):
                return
            try:
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                loaded = {}
                for uid, udict in data.items():
                    loaded[uid] = User(**udict)
                self.users = loaded
            except Exception as e:
                print(f"[UserManager Warning] Failed to load {self.storage_path}: {e}")

    def _bootstrap_admin(self):
        with self.lock:
            admin_user = self.register_user(
                username="admin",
                email="admin@agentmesh.local",
                password="AdminMesh@2026!",
                role=UserRole.ADMIN.value
            )
            print(f"[UserManager] Bootstrapped default admin user: {admin_user.username}")

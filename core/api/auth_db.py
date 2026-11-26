import sqlite3
import os
from passlib.context import CryptContext
from core.settings.storage_config import load_storage_config
from core.storage.postgres import PostgresDatabase

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class AuthDB:
    def __init__(self, db_path="data/users.sqlite"):
        self.db_path = db_path
        storage = load_storage_config()
        self._postgres = PostgresDatabase(storage) if storage.uses_postgres else None
        if self._postgres is not None:
            self._postgres.initialize_schema()
        else:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def get_user(self, username: str):
        if self._postgres is not None:
            with self._postgres.connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, username, hashed_password, role, created_at
                    FROM auth_users
                    WHERE username = %s
                    """,
                    (username,),
                ).fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "username": row[1],
                "hashed_password": row[2],
                "role": row[3],
                "created_at": row[4],
            }
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            return cursor.fetchone()

    def count_users(self) -> int:
        if self._postgres is not None:
            with self._postgres.connection() as conn:
                row = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()
            return int(row[0] or 0)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            row = cursor.fetchone()
            return int(row[0] or 0)

    def create_user(self, username: str, password: str, role: str = "user"):
        hashed = pwd_context.hash(password)
        if self._postgres is not None:
            try:
                with self._postgres.connection() as conn:
                    conn.execute(
                        """
                        INSERT INTO auth_users (username, hashed_password, role)
                        VALUES (%s, %s, %s)
                        """,
                        (username, hashed, role),
                    )
                return True
            except Exception as exc:  # noqa: BLE001
                if exc.__class__.__name__ == "UniqueViolation":
                    return False
                raise
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
                    (username, hashed, role)
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def create_initial_admin(self, username: str, password: str) -> str:
        """Atomically create the one-time bootstrap administrator.

        Returns ``created`` when this request won bootstrap, ``initialized``
        when another account already exists, and ``conflict`` for a duplicate
        username race. The database transaction, rather than a prior count,
        owns the decision.
        """
        hashed = pwd_context.hash(password)
        if self._postgres is not None:
            try:
                with self._postgres.connection() as conn:
                    conn.execute("LOCK TABLE auth_users IN EXCLUSIVE MODE")
                    existing = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()
                    if int(existing[0] or 0) > 0:
                        return "initialized"
                    conn.execute(
                        """
                        INSERT INTO auth_users (username, hashed_password, role)
                        VALUES (%s, %s, 'admin')
                        """,
                        (username, hashed),
                    )
                return "created"
            except Exception as exc:  # noqa: BLE001
                if exc.__class__.__name__ == "UniqueViolation":
                    return "conflict"
                raise

        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            if int(existing[0] or 0) > 0:
                conn.rollback()
                return "initialized"
            try:
                conn.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, 'admin')",
                    (username, hashed),
                )
                conn.commit()
                return "created"
            except sqlite3.IntegrityError:
                conn.rollback()
                return "conflict"

    def verify_password(self, plain_password, hashed_password):
        return pwd_context.verify(plain_password, hashed_password)

    def reset_password(self, username: str, password: str) -> bool:
        """Replace one existing user's password without changing their role."""

        normalized_username = str(username or "").strip()
        if not normalized_username or not str(password):
            raise ValueError("username and password are required")
        hashed = pwd_context.hash(password)
        if self._postgres is not None:
            with self._postgres.connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE auth_users
                    SET hashed_password = %s
                    WHERE username = %s
                    """,
                    (hashed, normalized_username),
                )
                return bool(cursor.rowcount)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE users SET hashed_password = ? WHERE username = ?",
                (hashed, normalized_username),
            )
            conn.commit()
            return bool(cursor.rowcount)

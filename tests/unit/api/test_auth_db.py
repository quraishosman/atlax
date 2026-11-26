from core.api.auth_db import AuthDB


def test_reset_password_preserves_admin_role(tmp_path):
    db = AuthDB(str(tmp_path / "users.sqlite"))
    assert db.create_initial_admin("quraish", "old-password-123") == "created"

    assert db.reset_password("quraish", "new-password-456") is True
    user = db.get_user("quraish")

    assert user["role"] == "admin"
    assert db.verify_password("new-password-456", user["hashed_password"])
    assert not db.verify_password("old-password-123", user["hashed_password"])

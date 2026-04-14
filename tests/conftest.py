"""Shared fixtures and synthetic git repo builder."""

import os
import subprocess
import textwrap
from datetime import datetime, timedelta

import pytest


def run_git(*args, cwd, date="", env=None):
    base_env = {**os.environ}
    if date:
        base_env["GIT_AUTHOR_DATE"] = date
        base_env["GIT_COMMITTER_DATE"] = date
    if env:
        base_env.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=base_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


@pytest.fixture
def synthetic_repo(tmp_path):
    """Create a git repo with a known history for testing.

    History:
    1. Initial commit with base README
    2. feat: add auth — adds src/auth.py with 50 lines
    3. fix: auth edge case — modifies 10 lines in src/auth.py
    4. feat: add api — adds src/api.py with 30 lines
    5. Revert commit — reverts commit 2 (feat: add auth)
    6. feat: add utils — adds src/utils.py, never modified afterward
    """
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "test@test.com")
    rg("config", "user.name", "Test User")

    base_date = datetime(2026, 1, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 1. Initial commit
    (repo_dir / "README.md").write_text("# Test Repo\n")
    rg("add", "README.md")
    rg("commit", "-m", "Initial commit", date=iso(base_date))
    initial_sha = rg("rev-parse", "HEAD")

    # 2. feat: add auth (50 lines)
    src_dir = repo_dir / "src"
    src_dir.mkdir()
    auth_content = textwrap.dedent("""\
        # auth.py
        import hashlib
        import secrets


        def hash_password(password: str) -> str:
            salt = secrets.token_hex(16)
            return salt + ":" + hashlib.sha256((salt + password).encode()).hexdigest()


        def verify_password(password: str, hashed: str) -> bool:
            salt, digest = hashed.split(":")
            return hashlib.sha256((salt + password).encode()).hexdigest() == digest


        def generate_token() -> str:
            return secrets.token_urlsafe(32)


        def validate_token(token: str) -> bool:
            return len(token) == 43


        def create_session(user_id: int) -> dict:
            return {"user_id": user_id, "token": generate_token()}


        def destroy_session(session: dict) -> None:
            session.clear()


        def is_authenticated(session: dict) -> bool:
            return "token" in session and validate_token(session["token"])


        def get_user_id(session: dict) -> int:
            return session.get("user_id", -1)


        def require_auth(session: dict) -> None:
            if not is_authenticated(session):
                raise PermissionError("Not authenticated")


        def refresh_token(session: dict) -> dict:
            session["token"] = generate_token()
            return session


        def logout(session: dict) -> None:
            destroy_session(session)


        def login(user_id: int, password: str, stored_hash: str) -> dict:
            if verify_password(password, stored_hash):
                return create_session(user_id)
            raise ValueError("Invalid credentials")


        def change_password(
            session: dict, old_pw: str, new_pw: str, stored_hash: str,
        ) -> str:
            require_auth(session)
            if not verify_password(old_pw, stored_hash):
                raise ValueError("Wrong password")
            return hash_password(new_pw)
    """)
    (src_dir / "auth.py").write_text(auth_content)
    rg("add", "src/auth.py")
    feat_auth_date = base_date + timedelta(days=1)
    rg("commit", "-m", "feat: add auth", date=iso(feat_auth_date))
    feat_auth_sha = rg("rev-parse", "HEAD")

    # 3. fix: auth edge case (modifies ~10 lines in auth.py)
    fixed_auth = auth_content.replace(
        "    return len(token) == 43",
        "    return isinstance(token, str) and len(token) >= 43",
    ).replace(
        "    return session.get(\"user_id\", -1)",
        "    if \"user_id\" not in session:\n"
        "        raise KeyError(\"user_id not in session\")\n"
        "    return session[\"user_id\"]",
    )
    (src_dir / "auth.py").write_text(fixed_auth)
    rg("add", "src/auth.py")
    fix_date = feat_auth_date + timedelta(days=2)
    rg("commit", "-m", "fix: auth edge case", date=iso(fix_date))
    fix_auth_sha = rg("rev-parse", "HEAD")

    # 4. feat: add api (30 lines in src/api.py)
    api_content = textwrap.dedent("""\
        # api.py
        from src.auth import require_auth, get_user_id


        def get_user(session: dict, user_id: int) -> dict:
            require_auth(session)
            return {"id": user_id, "name": f"User {user_id}"}


        def list_users(session: dict) -> list:
            require_auth(session)
            return [{"id": i, "name": f"User {i}"} for i in range(10)]


        def create_user(session: dict, name: str) -> dict:
            require_auth(session)
            return {"id": 100, "name": name}


        def delete_user(session: dict, user_id: int) -> bool:
            require_auth(session)
            return True


        def update_user(session: dict, user_id: int, name: str) -> dict:
            require_auth(session)
            return {"id": user_id, "name": name}


        def get_current_user(session: dict) -> dict:
            require_auth(session)
            uid = get_user_id(session)
            return get_user(session, uid)
    """)
    (src_dir / "api.py").write_text(api_content)
    rg("add", "src/api.py")
    feat_api_date = fix_date + timedelta(days=1)
    rg("commit", "-m", "feat: add api", date=iso(feat_api_date))
    feat_api_sha = rg("rev-parse", "HEAD")

    # 5. Revert feat: add auth
    revert_date = feat_api_date + timedelta(days=1)
    # Standard git revert format
    (src_dir / "auth.py").write_text("# auth.py — reverted\n")
    rg("add", "src/auth.py")
    rg(
        "commit",
        "-m",
        f'Revert "feat: add auth"\n\nThis reverts commit {feat_auth_sha}.',
        date=iso(revert_date),
    )
    revert_sha = rg("rev-parse", "HEAD")

    # 6. feat: add utils (stable, never modified)
    utils_content = textwrap.dedent("""\
        # utils.py
        import json
        import os
        from pathlib import Path


        def load_config(path: str) -> dict:
            with open(path) as f:
                return json.load(f)


        def save_config(path: str, data: dict) -> None:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)


        def ensure_dir(path: str) -> Path:
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            return p


        def read_env(key: str, default: str = "") -> str:
            return os.environ.get(key, default)
    """)
    (src_dir / "utils.py").write_text(utils_content)
    rg("add", "src/utils.py")
    utils_date = revert_date + timedelta(days=3)
    rg("commit", "-m", "feat: add utils", date=iso(utils_date))
    feat_utils_sha = rg("rev-parse", "HEAD")

    return {
        "repo_dir": repo_dir,
        "shas": {
            "initial": initial_sha,
            "feat_auth": feat_auth_sha,
            "fix_auth": fix_auth_sha,
            "feat_api": feat_api_sha,
            "revert_auth": revert_sha,
            "feat_utils": feat_utils_sha,
        },
        "dates": {
            "initial": base_date,
            "feat_auth": feat_auth_date,
            "fix_auth": fix_date,
            "feat_api": feat_api_date,
            "revert_auth": revert_date,
            "feat_utils": utils_date,
        },
    }


@pytest.fixture
def binary_file_repo(tmp_path):
    """Repo with a binary file to test binary handling."""
    repo_dir = tmp_path / "binary-repo"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "test@test.com")
    rg("config", "user.name", "Test User")

    base_date = datetime(2026, 2, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 1. Initial commit
    (repo_dir / "README.md").write_text("# Binary test\n")
    rg("add", "README.md")
    rg("commit", "-m", "Initial commit", date=iso(base_date))
    initial_sha = rg("rev-parse", "HEAD")

    # 2. Add a binary file (fake PNG header)
    (repo_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    rg("add", "image.png")
    add_binary_date = base_date + timedelta(days=1)
    rg("commit", "-m", "feat: add logo image", date=iso(add_binary_date))
    add_binary_sha = rg("rev-parse", "HEAD")

    # 3. Add a text file alongside
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "app.py").write_text("# app\ndef main():\n    print('hello')\n")
    rg("add", "src/app.py")
    add_text_date = add_binary_date + timedelta(days=1)
    rg("commit", "-m", "feat: add app", date=iso(add_text_date))
    add_text_sha = rg("rev-parse", "HEAD")

    return {
        "repo_dir": repo_dir,
        "shas": {
            "initial": initial_sha,
            "add_binary": add_binary_sha,
            "add_text": add_text_sha,
        },
    }


@pytest.fixture
def renamed_file_repo(tmp_path):
    """Repo where a file gets renamed via git mv."""
    repo_dir = tmp_path / "rename-repo"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "test@test.com")
    rg("config", "user.name", "Test User")

    base_date = datetime(2026, 2, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 1. Initial commit
    (repo_dir / "README.md").write_text("# Rename test\n")
    rg("add", "README.md")
    rg("commit", "-m", "Initial commit", date=iso(base_date))
    initial_sha = rg("rev-parse", "HEAD")

    # 2. Add a file
    (repo_dir / "src").mkdir()
    content = "\n".join(f"line_{i} = {i}" for i in range(20)) + "\n"
    (repo_dir / "src" / "old_name.py").write_text(content)
    rg("add", "src/old_name.py")
    add_date = base_date + timedelta(days=1)
    rg("commit", "-m", "feat: add module", date=iso(add_date))
    add_sha = rg("rev-parse", "HEAD")

    # 3. Rename it
    rg("mv", "src/old_name.py", "src/new_name.py")
    rename_date = add_date + timedelta(days=2)
    rg("commit", "-m", "refactor: rename module", date=iso(rename_date))
    rename_sha = rg("rev-parse", "HEAD")

    return {
        "repo_dir": repo_dir,
        "shas": {
            "initial": initial_sha,
            "add_file": add_sha,
            "rename": rename_sha,
        },
    }


@pytest.fixture
def empty_commit_repo(tmp_path):
    """Repo with an empty commit (--allow-empty)."""
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "test@test.com")
    rg("config", "user.name", "Test User")

    base_date = datetime(2026, 2, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 1. Initial commit
    (repo_dir / "README.md").write_text("# Empty test\n")
    rg("add", "README.md")
    rg("commit", "-m", "Initial commit", date=iso(base_date))
    initial_sha = rg("rev-parse", "HEAD")

    # 2. Empty commit
    empty_date = base_date + timedelta(days=1)
    rg("commit", "--allow-empty", "-m", "chore: trigger ci", date=iso(empty_date))
    empty_sha = rg("rev-parse", "HEAD")

    return {
        "repo_dir": repo_dir,
        "shas": {"initial": initial_sha, "empty": empty_sha},
    }


@pytest.fixture
def shallow_clone_repo(tmp_path, synthetic_repo):
    """Shallow clone of synthetic_repo with depth=1."""
    clone_dir = tmp_path / "shallow-clone"
    env = {**os.environ, "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "protocol.file.allow",
           "GIT_CONFIG_VALUE_0": "always"}
    subprocess.run(
        ["git", "clone", "--depth=1", str(synthetic_repo["repo_dir"]), str(clone_dir)],
        check=True,
        capture_output=True,
        env=env,
    )
    return {"repo_dir": clone_dir}


@pytest.fixture
def large_file_repo(tmp_path):
    """Repo with a 1000+ line file to test blame performance."""
    repo_dir = tmp_path / "large-repo"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "test@test.com")
    rg("config", "user.name", "Test User")

    base_date = datetime(2026, 2, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 1. Initial commit
    (repo_dir / "README.md").write_text("# Large file test\n")
    rg("add", "README.md")
    rg("commit", "-m", "Initial commit", date=iso(base_date))
    initial_sha = rg("rev-parse", "HEAD")

    # 2. Add large file (1200 lines)
    lines = [f"def func_{i}():\n    return {i}\n" for i in range(600)]
    (repo_dir / "large_file.py").write_text("\n".join(lines))
    rg("add", "large_file.py")
    add_date = base_date + timedelta(days=1)
    rg("commit", "-m", "feat: add large module", date=iso(add_date))
    add_sha = rg("rev-parse", "HEAD")

    # 3. Modify 50 lines in the middle
    modified_lines = lines[:]
    for i in range(250, 300):
        modified_lines[i] = f"def func_{i}():\n    return {i} + 1  # modified\n"
    (repo_dir / "large_file.py").write_text("\n".join(modified_lines))
    rg("add", "large_file.py")
    mod_date = add_date + timedelta(days=2)
    rg("commit", "-m", "fix: update calculations", date=iso(mod_date))
    mod_sha = rg("rev-parse", "HEAD")

    return {
        "repo_dir": repo_dir,
        "shas": {"initial": initial_sha, "add_large": add_sha, "modify": mod_sha},
    }


@pytest.fixture
def submodule_repo(tmp_path):
    """Repo containing a git submodule."""
    # Create the submodule source repo
    sub_dir = tmp_path / "sub-source"
    sub_dir.mkdir()

    def rg_sub(*args, date=""):
        return run_git(*args, cwd=sub_dir, date=date)

    rg_sub("init")
    rg_sub("config", "user.email", "test@test.com")
    rg_sub("config", "user.name", "Test User")
    (sub_dir / "lib.py").write_text("def helper():\n    return 42\n")
    rg_sub("add", "lib.py")
    rg_sub("commit", "-m", "Initial sub commit")

    # Create the main repo
    repo_dir = tmp_path / "main-repo"
    repo_dir.mkdir()

    def rg(*args, date=""):
        return run_git(*args, cwd=repo_dir, date=date)

    rg("init")
    rg("config", "user.email", "test@test.com")
    rg("config", "user.name", "Test User")
    rg("config", "protocol.file.allow", "always")

    base_date = datetime(2026, 2, 1, 12, 0, 0)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    (repo_dir / "README.md").write_text("# Submodule test\n")
    rg("add", "README.md")
    rg("commit", "-m", "Initial commit", date=iso(base_date))
    initial_sha = rg("rev-parse", "HEAD")

    # Add submodule
    sub_date = base_date + timedelta(days=1)
    rg(
        "-c", "protocol.file.allow=always",
        "submodule", "add", str(sub_dir), "vendor/sub",
        date=iso(sub_date),
    )
    rg("commit", "-m", "feat: add submodule", date=iso(sub_date))
    sub_sha = rg("rev-parse", "HEAD")

    return {
        "repo_dir": repo_dir,
        "shas": {"initial": initial_sha, "add_submodule": sub_sha},
    }

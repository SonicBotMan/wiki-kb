"""Tests for atomic write and file lock (P0-1, P3-1)."""
import threading
import time
from pathlib import Path


class TestSafeWrite:
    def test_basic_write(self, mcp_module):
        mcp_mod, root = mcp_module
        target = root / "concepts" / "atomic-test.md"
        mcp_mod._safe_write(target, "---\ntitle: atomic\n---\nbody")
        assert target.read_text(encoding="utf-8") == "---\ntitle: atomic\n---\nbody"

    def test_overwrite_preserves_content(self, mcp_module):
        mcp_mod, root = mcp_module
        target = root / "concepts" / "overwrite-test.md"
        mcp_mod._safe_write(target, "first")
        mcp_mod._safe_write(target, "second")
        assert target.read_text(encoding="utf-8") == "second"

    def test_creates_parent_dirs(self, mcp_module):
        mcp_mod, root = mcp_module
        target = root / "newdir" / "subdir" / "test.md"
        mcp_mod._safe_write(target, "content")
        assert target.exists()


class TestFileLock:
    def test_lock_context_manager(self, mcp_module):
        mcp_mod, root = mcp_module
        target = root / "concepts" / "lock-test.md"
        target.write_text("data", encoding="utf-8")
        with mcp_mod._FileLock(target) as lock:
            assert lock.lock_path.exists()

    def test_concurrent_writes_ordered(self, mcp_module):
        """Two threads writing with lock should not corrupt."""
        mcp_mod, root = mcp_module
        target = root / "concepts" / "concurrent.md"
        errors = []

        def write_value(val):
            try:
                with mcp_mod._FileLock(target):
                    mcp_mod._safe_write(target, val)
                    time.sleep(0.01)
                    read_back = target.read_text(encoding="utf-8")
                    assert read_back == val, f"Expected {val!r}, got {read_back!r}"
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=write_value, args=("value-A",))
        t2 = threading.Thread(target=write_value, args=("value-B",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Concurrent write errors: {errors}"

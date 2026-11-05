import os
import stat

import pytest

from gai_pro.paths import Layout, secure_file
from gai_pro.store import Store

posix_only = pytest.mark.skipif(os.name != "posix", reason="mode bits are a POSIX concept")


@posix_only
def test_layout_creates_owner_only_dirs(tmp_path):
    layout = Layout(str(tmp_path / "data"))
    assert stat.S_IMODE(layout.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(layout.screenshots.stat().st_mode) == 0o700


@posix_only
def test_config_and_accounts_files_are_0600(layout):
    store = Store(layout)
    store.update_config({"mode": "mock"})
    store.replace_accounts([])
    assert stat.S_IMODE(layout.config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(layout.accounts_file.stat().st_mode) == 0o600


@posix_only
def test_secure_file_tightens_existing_file(tmp_path):
    target = tmp_path / "secret.txt"
    target.write_text("x")
    os.chmod(target, 0o644)
    secure_file(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@posix_only
def test_log_file_is_owner_only(layout):
    store = Store(layout)
    store.log("hello")
    assert stat.S_IMODE(layout.log_file.stat().st_mode) == 0o600

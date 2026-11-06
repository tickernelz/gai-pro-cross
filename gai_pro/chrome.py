"""Start the user's own Chrome with a DevTools port and no automation flags."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CDP_READY_TIMEOUT = 20.0
CDP_POLL_INTERVAL = 0.25

CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

_PROXYLESS = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ChromeError(RuntimeError):
    """Raised when Chrome cannot be located or started."""


def chrome_candidates():
    found = []
    for name in CHROME_NAMES:
        path = shutil.which(name)
        if path:
            found.append(path)
    if os.name == "nt":
        roots = (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        )
        for root in roots:
            if root:
                found.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
                found.append(os.path.join(root, "Chromium", "Application", "chrome.exe"))
    elif sys.platform == "darwin":
        found.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]
        )
    return list(dict.fromkeys(found))


def find_chrome():
    for candidate in chrome_candidates():
        if not os.path.isfile(candidate):
            continue
        if os.name == "nt" or os.access(candidate, os.X_OK):
            return candidate
    raise ChromeError(
        "Chrome tidak ditemukan (Chrome not found): install Google Chrome, or put google-chrome/chromium on PATH"
    )


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch_args(executable, profile_dir, port, proxy=""):
    args = [
        str(executable),
        f"--user-data-dir={Path(profile_dir)}",
        f"--remote-debugging-port={int(port)}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if proxy:
        args.append(f"--proxy-server={proxy}")
    args.append("about:blank")
    return args


def launch(profile_dir, port, proxy=""):
    executable = find_chrome()
    command = launch_args(executable, profile_dir, port, proxy)
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "posix":
        options["start_new_session"] = True
    try:
        return subprocess.Popen(command, **options)
    except OSError as exc:
        raise ChromeError(f"Chrome tidak ditemukan atau gagal dijalankan (Chrome did not start): {exc}") from exc


def stop(process, timeout=5.0):
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
        process.terminate()
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            pass
    except OSError:
        pass


def cdp_version(port, timeout=0.5):
    url = f"http://127.0.0.1:{int(port)}/json/version"
    try:
        with _PROXYLESS.open(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and payload else None


def wait_for_cdp(port, timeout=CDP_READY_TIMEOUT, interval=CDP_POLL_INTERVAL):
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if cdp_version(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)

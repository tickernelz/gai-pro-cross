import pathlib
import re

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "gai_pro"
ALLOWED_HOSTS = {"accounts.google.com", "one.google.com", "play.google.com", "127.0.0.1", "localhost"}
URL = re.compile(r"https?://([^/\"'\s]+)")


def _python_sources():
    return sorted(PACKAGE.rglob("*.py"))


def test_every_outbound_host_is_google_or_loopback():
    offenders = []
    for path in _python_sources():
        for host in URL.findall(path.read_text(encoding="utf-8")):
            host = host.split(":")[0].lower()
            if "{" in host or host in ALLOWED_HOSTS:
                continue
            offenders.append(f"{path.name}: {host}")
    assert offenders == [], offenders


def test_no_analytics_or_http_client_dependency():
    banned = ("requests.", "httpx.", "sentry_sdk", "posthog", "mixpanel", "segment.", "analytics")
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert offenders == [], offenders


def test_declared_dependencies_stay_minimal():
    pyproject = (PACKAGE.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "fastapi" not in pyproject
    assert "uvicorn" not in pyproject

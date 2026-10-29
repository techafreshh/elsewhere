"""Deploy-contract tests: compose stack, Caddy proxy, README steps, repo hygiene."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_compose_defines_api_and_caddy() -> None:
    text = _read("docker-compose.yml")
    assert "api:" in text
    assert "caddy:" in text
    assert "80:80" in text
    assert "depends_on" in text


def test_caddy_proxies_compose_api() -> None:
    text = _read("Caddyfile")
    assert "api:8000" in text


def test_readme_setup_steps_bounded_with_keywords() -> None:
    text = _read("README.md")
    steps = re.findall(r"(?m)^\d+\.\s", text)
    assert 1 <= len(steps) <= 10
    assert "cp .env.example .env" in text
    assert "docker compose up" in text
    assert "seed_cache" in text


def test_license_is_mit() -> None:
    assert "MIT" in _read("LICENSE")


def test_dockerignore_hides_env() -> None:
    path = ROOT / ".dockerignore"
    assert path.is_file()
    assert ".env" in path.read_text(encoding="utf-8")

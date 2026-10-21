"""Where uni finds your Canvas address and access token. The token is never printed, logged or sent anywhere but Canvas."""

from __future__ import annotations

import getpass
import json
import os
import stat
from pathlib import Path

from .canvas import Canvas, CanvasError, check_base_url


class ConfigError(Exception):
    pass


def config_dir() -> Path:
    if os.environ.get("UNI_CONFIG_DIR"):
        return Path(os.environ["UNI_CONFIG_DIR"])
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "uni"


def _read_token_file(path: Path) -> str:
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ConfigError(f"no token file at {path}; run `uni setup`") from None
    if hasattr(os, "getuid") and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ConfigError(f"{path} must be readable by you alone: chmod 600 {path}")
    token = path.read_text(encoding="ascii").strip()
    if not token or any(ch.isspace() for ch in token):
        raise ConfigError(f"{path} must hold one access token")
    return token


def load() -> Canvas:
    """CANVAS_BASE_URL and CANVAS_TOKEN (or CANVAS_TOKEN_FILE) win; otherwise what `uni setup` saved."""
    folder = config_dir()
    saved: dict = {}
    if (folder / "config.json").exists():
        saved = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    base = os.environ.get("CANVAS_BASE_URL") or saved.get("base_url")
    if not base:
        raise ConfigError("no Canvas address: run `uni setup`, or set CANVAS_BASE_URL")
    token = os.environ.get("CANVAS_TOKEN") or _read_token_file(Path(os.environ.get("CANVAS_TOKEN_FILE") or folder / "token"))
    try:
        return Canvas(base, token)
    except ValueError as error:
        raise ConfigError(str(error)) from None


def setup() -> str:
    """Asks for the Canvas address and a token, checks them with one read of your profile, and saves them (token mode 600)."""
    print("Canvas address (the site you log in to, e.g. https://canvas.instructure.com):")
    try:
        base = check_base_url(input("> "))
    except ValueError as error:
        raise ConfigError(str(error)) from None
    print("Access token (Canvas: Account > Settings > New access token). It is not shown as you paste it.")
    token = getpass.getpass("> ").strip()
    if not token:
        raise ConfigError("no token given")
    try:
        me = Canvas(base, token).get("/api/v1/users/self")
    except CanvasError as error:
        raise ConfigError(f"Canvas did not accept that token: {error}") from None
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = folder / "token"
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(token + "\n")
    os.chmod(token_file, 0o600)
    (folder / "config.json").write_text(json.dumps({"base_url": base}) + "\n", encoding="utf-8")
    return f"Connected to {base} as {me.get('name')}. Saved in {folder}."

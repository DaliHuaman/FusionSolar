"""Publica el dashboard (docs/ + data.json) en la rama gh-pages del repo (GitHub Pages).

La rama gh-pages se mantiene con UN solo commit (se reescribe en cada corrida), asi el repo
no crece con 48 commits diarios. La rama principal nunca se toca.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from .common import ROOT, STATE_DIR
from .sitedata import SITE_DIR

log = logging.getLogger("fusionsolar.publish")
PAGES_DIR = STATE_DIR / "pages"
RAMA = "gh-pages"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=PAGES_DIR, capture_output=True, text=True)
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout)
        tok = os.environ.get("GITHUB_TOKEN")
        if tok:
            msg = msg.replace(tok, "***")
        raise RuntimeError(f"git {args[0]} fallo: {msg.strip()}")
    return r


def _remoto() -> str:
    if os.environ.get("GIT_REMOTE_URL"):          # solo para pruebas locales
        return os.environ["GIT_REMOTE_URL"]
    repo = os.environ.get("GITHUB_REPO", "dalihuaman/fusionsolar")
    tok = os.environ.get("GITHUB_TOKEN")
    return f"https://x-access-token:{tok}@github.com/{repo}.git" if tok else f"https://github.com/{repo}.git"


def _preparar_carpeta() -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    if not (PAGES_DIR / ".git").exists():
        _git("init", "-q")
        _git("checkout", "-q", "-B", RAMA)
        _git("config", "user.name", "FusionSolar Bot")
        _git("config", "user.email", "fusionsolar-bot@users.noreply.github.com")
    for item in PAGES_DIR.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    shutil.copytree(ROOT / "docs", PAGES_DIR, dirs_exist_ok=True)
    nuevo = SITE_DIR / "data" / "data.json"
    if nuevo.exists():
        shutil.copy2(nuevo, PAGES_DIR / "data" / "data.json")
    (PAGES_DIR / ".nojekyll").write_text("", encoding="utf-8")


def publicar(mensaje: str) -> None:
    _preparar_carpeta()
    _git("add", "-A")
    hay_commit = _git("rev-parse", "--verify", "HEAD", check=False).returncode == 0
    if hay_commit:
        _git("commit", "-q", "--amend", "--allow-empty", "-m", mensaje, "--reset-author")
    else:
        _git("commit", "-q", "--allow-empty", "-m", mensaje)
    _git("push", "-q", "--force", _remoto(), f"HEAD:{RAMA}")
    log.info("dashboard publicado en %s", RAMA)

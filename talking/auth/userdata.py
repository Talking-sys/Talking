"""Delete or relocate per-user file data (chat, media, 3-minute history).

Used when an abandoned anonymous account expires and when the owner explicitly
confirms a merge into a different existing account. User ids are path-safe
via talking.ids.check_id; nothing here logs phones, codes, or raw ips.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from talking.ids import check_id

log = logging.getLogger("talking.auth")


def _rmtree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def drop_history(engine, user_id: str) -> None:
    try:
        uid = check_id(user_id, "user_id")
    except ValueError:
        return
    hist = getattr(engine, "_histories", None)
    buf = hist.pop(uid, None) if isinstance(hist, dict) else None
    if buf is not None:
        try:
            buf.close()
        except Exception:
            log.debug("history close failed", exc_info=True)
    data_dir = getattr(getattr(engine, "s", None), "data_dir", None)
    if not data_dir:
        return
    _rmtree(Path(data_dir) / "history" / uid)


def relocate_history(engine, src_id: str, dest_id: str) -> None:
    try:
        src = check_id(src_id, "user_id")
        dest = check_id(dest_id, "user_id")
    except ValueError:
        return
    if src == dest:
        return
    data_dir = getattr(getattr(engine, "s", None), "data_dir", None)
    if not data_dir:
        return
    hist = getattr(engine, "_histories", None)
    if isinstance(hist, dict):
        for key in (src, dest):
            buf = hist.pop(key, None)
            if buf is not None:
                try:
                    buf.close()
                except Exception:
                    log.debug("history close failed", exc_info=True)
    src_path = Path(data_dir) / "history" / src
    dest_path = Path(data_dir) / "history" / dest
    if not src_path.is_dir():
        return
    if not dest_path.exists():
        src_path.rename(dest_path)
        return
    imported = dest_path / f"imported-{src[:8]}"
    try:
        shutil.copytree(src_path, imported, dirs_exist_ok=True)
    except OSError:
        log.debug("history copy failed", exc_info=True)
    _rmtree(src_path)


def purge_user_files(
    *,
    user_id: str,
    engine=None,
    chat_store=None,
    media=None,
) -> None:
    if engine is not None:
        drop_history(engine, user_id)
    if chat_store is not None and hasattr(chat_store, "delete_user"):
        chat_store.delete_user(user_id)
        return
    if media is not None and hasattr(media, "delete_user"):
        media.delete_user(user_id)


def relocate_user_files(
    *,
    src_id: str,
    dest_id: str,
    engine=None,
    chat_store=None,
    media=None,
) -> dict[str, int]:
    """Move anonymous file data onto an existing account. Returns copy counts."""
    media_map: dict[str, str] = {}
    if media is not None and hasattr(media, "relocate_user"):
        media_map = media.relocate_user(src_id, dest_id) or {}
    messages = 0
    if chat_store is not None and hasattr(chat_store, "relocate_user"):
        messages = int(chat_store.relocate_user(src_id, dest_id, media_id_map=media_map) or 0)
    if engine is not None:
        relocate_history(engine, src_id, dest_id)
    purge_user_files(user_id=src_id, engine=engine, chat_store=chat_store, media=media)
    return {"messages": messages, "media": len(media_map)}


def cleaner_from_app(app):
    """Return a callable(user_id) that deletes file data for that user."""
    def _clean(user_id: str) -> None:
        purge_user_files(
            user_id=user_id,
            engine=getattr(app.state, "engine", None),
            chat_store=getattr(app.state, "chat_store", None),
            media=getattr(app.state, "chat_media", None),
        )
    return _clean


def relocator_from_app(app):
    def _move(src_id: str, dest_id: str) -> dict[str, int]:
        return relocate_user_files(
            src_id=src_id,
            dest_id=dest_id,
            engine=getattr(app.state, "engine", None),
            chat_store=getattr(app.state, "chat_store", None),
            media=getattr(app.state, "chat_media", None),
        )
    return _move

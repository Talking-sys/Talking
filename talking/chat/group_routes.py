"""HTTP routes for group conversations. Attached from ``register_chat``."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from talking.auth.deps import require_user
from talking.languages import WHISPER_LANGUAGES

from .groups import (
    DEFAULT_INVITE_TTL_S,
    GroupError,
    validate_lang,
)
from .present import member_limit, publish_group


def _user_id(request: Request) -> str:
    return require_user(request).id


def _group_http(err: Exception) -> HTTPException:
    if isinstance(err, GroupError):
        return HTTPException(err.status, err.message)
    if isinstance(err, KeyError):
        return HTTPException(404, str(err) or "not found")
    if isinstance(err, ValueError):
        return HTTPException(400, str(err))
    return HTTPException(400, str(err))


def _json(request: Request):
    return request.headers.get("content-type", "").startswith("application/json")


def _cap(app: FastAPI, uid: str) -> int:
    return member_limit(app.state.settings, app.state.engine.commercial, uid)


def _fallback_lang(app: FastAPI, body_lang: str | None) -> str:
    if body_lang:
        return validate_lang(body_lang)
    stored = app.state.engine.store.get("target_lang") or "en"
    return stored if stored in WHISPER_LANGUAGES else "en"


def register_group_routes(app: FastAPI) -> None:
    s = app.state.settings

    @app.get("/api/chat/groups")
    def list_groups(request: Request):
        uid = _user_id(request)
        return {
            "ok": True,
            "groups": [g.to_public(uid) for g in app.state.chat_store.groups.list_for(uid)],
            "max_members": _cap(app, uid),
            "trial_max_members": int(getattr(s, "chat_group_trial_max_members", 5) or 5),
        }

    @app.post("/api/chat/groups")
    async def create_group(request: Request):
        uid = _user_id(request)
        try:
            data = await request.json() if _json(request) else {}
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        member_ids = data.get("member_ids") or data.get("members") or []
        if member_ids and not isinstance(member_ids, list):
            raise HTTPException(400, "member_ids must be a list")
        try:
            lang = _fallback_lang(app, data.get("lang"))
            group = app.state.chat_store.groups.create(
                uid,
                str(data.get("name") or data.get("title") or ""),
                avatar=str(data.get("avatar") or ""),
                description=str(data.get("description") or ""),
                lang=lang,
                member_ids=[str(x) for x in member_ids],
                max_members=_cap(app, uid),
            )
        except (GroupError, ValueError) as e:
            raise _group_http(e)
        publish_group(app, group, "chat.group")
        return JSONResponse(group.to_public(uid), status_code=201)

    @app.get("/api/chat/groups/join/{token}")
    def preview_invite(request: Request, token: str):
        _user_id(request)
        try:
            group, inv = app.state.chat_store.groups.preview_invite(token)
        except GroupError as e:
            raise _group_http(e)
        return {"ok": True, "group": group.to_preview(), "invite": inv.to_public()}

    @app.post("/api/chat/groups/join")
    async def join_group(request: Request):
        uid = _user_id(request)
        try:
            data = await request.json() if _json(request) else {}
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        token = str(data.get("token") or "").strip()
        if not token:
            raise HTTPException(400, "token required")
        try:
            lang = _fallback_lang(app, data.get("lang"))
            group = app.state.chat_store.groups.join(
                token, uid, lang=lang, max_members=_cap(app, uid),
            )
        except (GroupError, ValueError) as e:
            raise _group_http(e)
        publish_group(app, group, "chat.group.member", extra={"action": "join", "user_id": uid})
        return group.to_public(uid)

    @app.get("/api/chat/groups/{group_id}")
    def get_group(request: Request, group_id: str):
        uid = _user_id(request)
        try:
            group, _member = app.state.chat_store.groups.require_member(group_id, uid)
        except GroupError as e:
            raise _group_http(e)
        return group.to_public(uid)

    @app.patch("/api/chat/groups/{group_id}")
    async def patch_group(request: Request, group_id: str):
        uid = _user_id(request)
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        kw = {}
        if "name" in data or "title" in data:
            kw["title"] = str(data.get("name") or data.get("title") or "")
        if "avatar" in data:
            kw["avatar"] = str(data.get("avatar") or "")
        if "description" in data:
            kw["description"] = str(data.get("description") or "")
        try:
            group = app.state.chat_store.groups.update(group_id, uid, **kw)
        except GroupError as e:
            raise _group_http(e)
        publish_group(app, group, "chat.group")
        return group.to_public(uid)

    @app.delete("/api/chat/groups/{group_id}")
    def dissolve_group(request: Request, group_id: str):
        uid = _user_id(request)
        try:
            group, member = app.state.chat_store.groups.require_member(group_id, uid)
            ids = group.member_ids()
            app.state.chat_store.groups.dissolve(group_id, uid)
        except GroupError as e:
            raise _group_http(e)
        event = {"type": "chat.group.dissolved", "group_id": group_id}
        for mid in ids:
            app.state.chat_hub.publish(mid, event)
        return {"ok": True, "id": group_id, "role": member.role}

    @app.post("/api/chat/groups/{group_id}/leave")
    def leave_group(request: Request, group_id: str):
        uid = _user_id(request)
        try:
            before = app.state.chat_store.groups.require(group_id)
            remaining = app.state.chat_store.groups.leave(group_id, uid)
        except GroupError as e:
            raise _group_http(e)
        app.state.chat_hub.publish(uid, {"type": "chat.group.left", "group_id": group_id})
        if remaining is not None:
            publish_group(app, remaining, "chat.group.member", extra={"action": "leave", "user_id": uid})
        else:
            for mid in before.member_ids():
                if mid != uid:
                    app.state.chat_hub.publish(mid, {"type": "chat.group.dissolved", "group_id": group_id})
        return {"ok": True, "id": group_id}

    @app.post("/api/chat/groups/{group_id}/members")
    async def add_member(request: Request, group_id: str):
        uid = _user_id(request)
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        target = str(data.get("user_id") or "").strip()
        if not target:
            raise HTTPException(400, "user_id required")
        try:
            lang = _fallback_lang(app, data.get("lang"))
            role = str(data.get("role") or "member")
            group = app.state.chat_store.groups.add_member(
                group_id, uid, target, role=role, lang=lang, max_members=_cap(app, uid),
            )
        except (GroupError, ValueError) as e:
            raise _group_http(e)
        publish_group(app, group, "chat.group.member", extra={"action": "add", "user_id": target})
        return group.to_public(uid)

    @app.patch("/api/chat/groups/{group_id}/members/{member_id}")
    async def patch_member(request: Request, group_id: str, member_id: str):
        uid = _user_id(request)
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        try:
            group = app.state.chat_store.groups.set_member(
                group_id, uid, member_id,
                role=data.get("role"),
                lang=data.get("lang"),
            )
        except (GroupError, ValueError) as e:
            raise _group_http(e)
        publish_group(app, group, "chat.group.member", extra={"action": "update", "user_id": member_id})
        return group.to_public(uid)

    @app.delete("/api/chat/groups/{group_id}/members/{member_id}")
    def remove_member(request: Request, group_id: str, member_id: str):
        uid = _user_id(request)
        try:
            remaining = app.state.chat_store.groups.remove_member(group_id, uid, member_id)
        except GroupError as e:
            raise _group_http(e)
        app.state.chat_hub.publish(member_id, {
            "type": "chat.group.kicked" if member_id != uid else "chat.group.left",
            "group_id": group_id,
        })
        if remaining is not None:
            publish_group(app, remaining, "chat.group.member", extra={"action": "remove", "user_id": member_id})
        return {"ok": True, "id": group_id, "user_id": member_id}

    @app.post("/api/chat/groups/{group_id}/transfer")
    async def transfer_owner(request: Request, group_id: str):
        uid = _user_id(request)
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "invalid JSON")
        target = str(data.get("user_id") or "").strip()
        if not target:
            raise HTTPException(400, "user_id required")
        try:
            group = app.state.chat_store.groups.transfer(group_id, uid, target)
        except GroupError as e:
            raise _group_http(e)
        publish_group(app, group, "chat.group.member", extra={"action": "transfer", "user_id": target})
        return group.to_public(uid)

    @app.post("/api/chat/groups/{group_id}/invites")
    async def create_invite(request: Request, group_id: str):
        uid = _user_id(request)
        data: dict = {}
        if _json(request):
            try:
                parsed = await request.json()
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {}
        ttl = int(data.get("expires_in") or data.get("ttl_s") or getattr(s, "chat_group_invite_ttl_s", DEFAULT_INVITE_TTL_S))
        max_uses = data.get("max_uses")
        if max_uses is None:
            max_uses = 20
        try:
            _group, invite, token = app.state.chat_store.groups.create_invite(
                group_id, uid, ttl_s=ttl, max_uses=int(max_uses),
            )
        except (GroupError, ValueError, TypeError) as e:
            raise _group_http(e) if isinstance(e, GroupError) else HTTPException(400, str(e))
        return JSONResponse(invite.to_public(token=token), status_code=201)

    @app.get("/api/chat/groups/{group_id}/invites")
    def list_invites(request: Request, group_id: str):
        uid = _user_id(request)
        try:
            invites = app.state.chat_store.groups.list_invites(group_id, uid)
        except GroupError as e:
            raise _group_http(e)
        return {"ok": True, "invites": [i.to_public() for i in invites]}

    @app.delete("/api/chat/groups/{group_id}/invites/{invite_id}")
    def revoke_invite(request: Request, group_id: str, invite_id: str):
        uid = _user_id(request)
        try:
            app.state.chat_store.groups.revoke_invite(group_id, uid, invite_id)
        except GroupError as e:
            raise _group_http(e)
        return {"ok": True, "id": invite_id}

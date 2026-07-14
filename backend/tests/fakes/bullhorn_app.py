"""Fake Bullhorn REST server (FastAPI, in-memory, deterministic).

Implements the verified fact-sheet contract against :class:`FakeBullhornState`
so every Bullhorn contract test and staging E2E can run with no real instance.
This is the ONLY place the destructive/edge paths (token strand, event-gap,
429 storm, verb inversion) can be exercised safely.

Build one app per test via :func:`build_app`; it carries its own fresh state on
``app.state.bh``. Drive it either in-process (httpx ``ASGITransport``) or over a
live uvicorn socket — see ``bullhorn_fakes.py`` for both fixture forms.

Endpoints (all under the returned discovery base):
  * ``GET  /rest-services/loginInfo``            — cluster discovery
  * ``POST /oauth/authorize``                    — automated auth-code grant
  * ``POST /oauth/token``                        — code + refresh exchange (rotating)
  * ``POST /rest-services/fake/login``           — REST session -> BhRestToken
  * ``GET  /rest-services/fake/ping``            — session heartbeat
  * ``GET  /rest-services/fake/search/{entity}`` — Lucene reads (mandatory fields)
  * ``GET  /rest-services/fake/query/{entity}``  — JPQL reads (mandatory fields)
  * ``GET/PUT/POST /rest-services/fake/entity/{entity}[/{id}]`` — CRUD (verb inversion)
  * ``GET  /rest-services/fake/meta/{entity}``   — minimal field metadata
  * ``GET  /rest-services/fake/settings/{name}`` — status list + categorization
  * event subscription create / destructive poll / requestId re-fetch / delete
  * ``GET  /rest-services/fake/entity/Candidate/{id}/fileAttachments``
  * ``GET  /rest-services/fake/file/Candidate/{id}/{fileId}/raw``
  * ``POST /rest-services/fake/resume/convertToText``
  * ``GET  /rest-services/fake/entitlements/{entity}``
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from .bullhorn_state import (
    DEFAULT_CATEGORIZATION,
    FAKE_OAUTH_PATH,
    FAKE_REST_PATH,
    SEARCH_PAGE_CAP,
    FakeBullhornState,
    OrgState,
)


def _err(status: int, message: str, *, code: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"errorMessage": message}
    if code:
        body["error"] = code
    return JSONResponse(status_code=status, content=body)


def build_app(state: FakeBullhornState | None = None) -> FastAPI:
    app = FastAPI()
    app.state.bh = state or FakeBullhornState()

    def bh() -> FakeBullhornState:
        return app.state.bh

    # --- REST-session guard: session check + 429 injection + counters -------

    async def rest_session(request: Request) -> dict[str, Any]:
        st = app.state.bh
        st.request_count += 1
        if st.should_serve_429():
            # Bullhorn's rate-limit reply; client backs off + counts against
            # its circuit breaker.
            return JSONResponse(  # type: ignore[return-value]
                status_code=429,
                content={"errorMessage": "rate limited"},
                headers={"Retry-After": "1"},
            )
        token = request.query_params.get("BhRestToken") or request.headers.get("BhRestToken")
        if not token:
            return _err(401, "missing BhRestToken")  # type: ignore[return-value]
        sess = st.session(token)
        if sess is None:
            return _err(401, "session expired or invalid")  # type: ignore[return-value]
        return sess

    # A dependency that either yields the session dict or short-circuits with a
    # Response. FastAPI can't return a Response from a dependency cleanly, so we
    # detect it in each handler via ``_guard``.
    def _guard(sess: Any) -> Response | None:
        return sess if isinstance(sess, Response) else None

    # --- discovery ----------------------------------------------------------

    @app.get("/rest-services/loginInfo")
    async def login_info(request: Request, username: str) -> Any:
        st = bh()
        org = st.org_by_username(username)
        if org is None:
            return _err(400, "unknown username")
        base = str(request.base_url).rstrip("/")
        return {
            "oauthUrl": f"{base}{FAKE_OAUTH_PATH}",
            "restUrl": f"{base}{FAKE_REST_PATH}",
        }

    # --- oauth --------------------------------------------------------------

    @app.post("/oauth/authorize")
    async def oauth_authorize(
        client_id: str = Form(...),
        username: str = Form(...),
        password: str = Form(...),
        action: str = Form("Login"),
        response_type: str = Form("code"),
        redirect_uri: str | None = Form(None),
    ) -> Any:
        st = bh()
        org = st.org_by_client_id(client_id)
        if org is None or org.username != username or org.password != password:
            return _err(401, "invalid credentials", code="access_denied")
        # Real Bullhorn's automated auth-code grant returns a 302 whose Location
        # header carries ?code=<authcode> (NOT a JSON body). Mirror that so the
        # client's redirect-parsing path is what gets exercised. redirect_uri is
        # optional; when absent Bullhorn uses the key's registered URI — we stand in
        # a deterministic default. Record it so the token exchange can require the
        # echo (Bullhorn rejects a mismatch).
        code = st.mint_auth_code(org, redirect_uri=redirect_uri)
        base = redirect_uri or "https://app.example/bh-callback"
        sep = "&" if "?" in base else "?"
        location = f"{base}{sep}code={code}&client_id={client_id}"
        return Response(status_code=302, headers={"Location": location})

    @app.post("/oauth/token")
    async def oauth_token(
        grant_type: str = Form(...),
        code: str | None = Form(None),
        refresh_token: str | None = Form(None),
        client_id: str | None = Form(None),
        client_secret: str | None = Form(None),
        redirect_uri: str | None = Form(None),
    ) -> Any:
        st = bh()
        if grant_type == "authorization_code":
            if not code:
                return _err(400, "missing code", code="invalid_request")
            rec = st.exchange_auth_code(code, redirect_uri=redirect_uri)
            if rec == "redirect_uri_mismatch":
                return _err(400, "redirect_uri mismatch", code="invalid_grant")
            if rec is None:
                return _err(400, "bad code", code="invalid_grant")
        elif grant_type == "refresh_token":
            if not refresh_token:
                return _err(400, "missing refresh_token", code="invalid_request")
            rec = st.exchange_refresh_token(refresh_token)
            if rec == "invalid_grant":
                # single-use violation / unknown token — the strand signal
                return _err(400, "refresh token already used or invalid", code="invalid_grant")
        else:
            return _err(400, "unsupported grant_type", code="unsupported_grant_type")
        return {
            "access_token": rec.access_token,
            "refresh_token": rec.refresh_token,
            "token_type": "Bearer",
            "expires_in": 600,
        }

    # --- REST login / ping --------------------------------------------------

    @app.post("/rest-services/fake/login")
    async def rest_login(access_token: str, version: str = "*") -> Any:
        st = bh()
        if not st.access_token_valid(access_token):
            return _err(401, "invalid or expired access_token")
        rec = st.access_record(access_token)
        assert rec is not None
        bh_token = st.open_session(rec.org_key)
        base_rest = FAKE_REST_PATH  # already carries the fake corpToken segment
        return {"BhRestToken": bh_token, "restUrl": base_rest + "/"}

    @app.get("/rest-services/fake/ping")
    async def ping(sess: Any = Depends(rest_session)) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        st = bh()
        expires = st.session_expires_in(sess["_token"])
        return {"sessionExpires": st.now + expires}

    # --- helpers for reads --------------------------------------------------

    def _org_of(sess: dict[str, Any]) -> OrgState:
        return app.state.bh.orgs[sess["org_key"]]

    def _project(record: dict[str, Any], fields: str | None) -> dict[str, Any]:
        # fields is MANDATORY: without it real Bullhorn returns only ids.
        if not fields:
            return {"id": record.get("id")}
        wanted = [f.strip() for f in fields.split(",") if f.strip()]
        if "*" in wanted:
            return dict(record)
        out: dict[str, Any] = {}
        for f in wanted:
            if f in record:
                out[f] = record[f]
        # id always present in Bullhorn payloads
        out.setdefault("id", record.get("id"))
        return out

    def _page(records: list[dict[str, Any]], start: int, count: int) -> tuple[list[dict[str, Any]], int]:
        total = len(records)
        capped = min(count, SEARCH_PAGE_CAP)
        return records[start : start + capped], total

    # --- search / query -----------------------------------------------------

    @app.get("/rest-services/fake/search/{entity}")
    async def search(
        entity: str,
        request: Request,
        fields: str | None = None,
        query: str | None = None,
        start: int = 0,
        count: int = 100,
        sess: Any = Depends(rest_session),
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        table = list(org.entities.get(entity, {}).values())
        # Mirror the lifecycle selector used by every full/reconciliation read.
        # Leaving this unfiltered made a closed JobOrder look remotely open and
        # hid missed-close recovery bugs from the contract suite.
        normalized_query = "".join((query or "").lower().split())
        if entity == "JobOrder" and "isopen:true" in normalized_query:
            table = [
                record
                for record in table
                if record.get("isOpen") is True
                or str(record.get("isOpen") or "").strip().lower() in {"true", "1", "yes"}
            ]
        data, total = _page(table, start, count)
        return {
            "total": total,
            "start": start,
            "count": len(data),
            "data": [_project(rec, fields) for rec in data],
        }

    @app.get("/rest-services/fake/query/{entity}")
    async def query(
        entity: str,
        request: Request,
        fields: str | None = None,
        where: str | None = None,
        start: int = 0,
        count: int = 100,
        sess: Any = Depends(rest_session),
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        table = list(org.entities.get(entity, {}).values())
        data, total = _page(table, start, count)
        return {
            "total": total,
            "start": start,
            "count": len(data),
            "data": [_project(rec, fields) for rec in data],
        }

    # --- entity CRUD (VERB INVERSION) --------------------------------------

    @app.put("/rest-services/fake/entity/{entity}")
    async def entity_create(entity: str, request: Request, sess: Any = Depends(rest_session)) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        body = await request.json()
        if "id" in body and body["id"] in org.entities.get(entity, {}):
            return _err(400, f"PUT-create on existing {entity}/{body['id']} — use POST to update")
        rec = app.state.bh._put_entity(org, entity, dict(body))  # noqa: SLF001
        return {"changedEntityId": rec["id"], "changeType": "INSERT", "data": rec}

    @app.post("/rest-services/fake/entity/{entity}/{ent_id}")
    async def entity_update(
        entity: str, ent_id: int, request: Request, sess: Any = Depends(rest_session)
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        table = org.entities.get(entity, {})
        if ent_id not in table:
            return _err(400, f"POST-update on missing {entity}/{ent_id} — use PUT to create")
        body = await request.json()
        table[ent_id].update(body)
        table[ent_id]["dateLastModified"] = app.state.bh.now
        return {"changedEntityId": ent_id, "changeType": "UPDATE", "data": table[ent_id]}

    @app.get("/rest-services/fake/entity/{entity}/{ent_id}")
    async def entity_get(
        entity: str,
        ent_id: int,
        fields: str | None = None,
        sess: Any = Depends(rest_session),
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        rec = org.entities.get(entity, {}).get(ent_id)
        if rec is None:
            return _err(404, f"{entity}/{ent_id} not found")
        return {"data": _project(rec, fields)}

    @app.get("/rest-services/fake/meta/{entity}")
    async def meta(entity: str, sess: Any = Depends(rest_session)) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        return {
            "entity": entity,
            "fields": [
                {"name": "id", "type": "ID"},
                {"name": "status", "type": "String"},
                {"name": "dateLastModified", "type": "Timestamp"},
            ],
        }

    # --- settings (status list + categorization) ---------------------------

    @app.get("/rest-services/fake/settings/{name}")
    async def settings(name: str, sess: Any = Depends(rest_session)) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        if name == "jobResponseStatusList":
            return {"jobResponseStatusList": list(org.status_list)}
        if name in DEFAULT_CATEGORIZATION:
            return {name: org.categorization.get(name)}
        return {name: None}

    # --- event subscriptions -----------------------------------------------

    @app.put("/rest-services/fake/event/subscription/{sub_id}")
    async def create_subscription(
        sub_id: str,
        type: str = "entity",
        names: str = "",
        eventTypes: str = "INSERTED,UPDATED,DELETED",
        sess: Any = Depends(rest_session),
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        from .bullhorn_state import SubscriptionState

        org = _org_of(sess)
        st = app.state.bh
        org.subscriptions[sub_id] = SubscriptionState(
            sub_id=sub_id,
            entity_names=[n for n in names.split(",") if n],
            event_types=[e for e in eventTypes.split(",") if e],
            created_at=st.now,
        )
        return {"subscriptionId": sub_id, "createdOn": st.now}

    @app.get("/rest-services/fake/event/subscription/{sub_id}")
    async def poll_events(
        sub_id: str,
        maxEvents: int = 100,
        requestId: int | None = None,
        sess: Any = Depends(rest_session),
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        st = app.state.bh
        sub = org.subscriptions.get(sub_id)
        if sub is None or sub.expired or (st.now - sub.created_at) >= _sub_ttl():
            return _err(404, "subscription not found or expired")
        # requestId re-fetch: replay ONLY the last batch, no new drain.
        if requestId is not None:
            if requestId == sub.last_request_id:
                return {"requestId": sub.last_request_id, "events": list(sub.last_batch)}
            return _err(400, "unknown or superseded requestId")
        # destructive drain
        batch = sub.queue[:maxEvents]
        del sub.queue[: len(batch)]
        sub.last_batch = batch
        sub.last_request_id = st._next()  # noqa: SLF001
        return {"requestId": sub.last_request_id, "events": batch}

    @app.delete("/rest-services/fake/event/subscription/{sub_id}")
    async def delete_subscription(sub_id: str, sess: Any = Depends(rest_session)) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        existed = org.subscriptions.pop(sub_id, None) is not None
        return {"result": existed}

    # --- files --------------------------------------------------------------

    @app.get("/rest-services/fake/entity/Candidate/{cand_id}/fileAttachments")
    async def file_attachments(
        cand_id: int, fields: str | None = None, sess: Any = Depends(rest_session)
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        files = org.files.get(cand_id, {})
        return {"data": [_project(f["meta"], fields) for f in files.values()]}

    @app.get("/rest-services/fake/file/Candidate/{cand_id}/{file_id}/raw")
    async def file_raw(cand_id: int, file_id: int, sess: Any = Depends(rest_session)) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        entry = org.files.get(cand_id, {}).get(file_id)
        if entry is None:
            return _err(404, "file not found")
        return Response(
            content=entry["raw"],
            media_type=entry["meta"].get("contentType", "application/octet-stream"),
        )

    @app.post("/rest-services/fake/resume/convertToText")
    async def convert_resume(
        file: UploadFile, sess: Any = Depends(rest_session)
    ) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        raw = await file.read()
        # Deterministic: echo a decoded text envelope.
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover
            text = ""
        return {"convertedText": f"[resume-text] {text}"}

    # --- entitlements -------------------------------------------------------

    @app.get("/rest-services/fake/entitlements/{entity}")
    async def entitlements(entity: str, sess: Any = Depends(rest_session)) -> Any:
        if (r := _guard(sess)) is not None:
            return r
        org = _org_of(sess)
        # Default to full CRUD if not explicitly seeded.
        return org.entitlements.get(entity, ["GET", "PUT", "POST", "DELETE"])

    return app


def _sub_ttl() -> int:
    from .bullhorn_state import SUBSCRIPTION_TTL

    return SUBSCRIPTION_TTL

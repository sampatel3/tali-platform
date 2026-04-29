import logging as _logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
# Friendly messages for API error codes (returned to frontend)
_API_ERROR_MESSAGES = {
    "REGISTER_USER_ALREADY_EXISTS": "An account with this email already exists. Sign in instead or use a different email.",
}
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from .platform.brand import BRAND_APP_DESCRIPTION, BRAND_NAME
from .platform.config import settings
from .platform.logging import setup_logging
from .platform.middleware import RequestLoggingMiddleware, RateLimitMiddleware, EnterpriseAccessMiddleware
from .platform.startup_validation import collect_startup_failures, is_production_like

# Set up logging
logger = setup_logging()

# ---------------------------------------------------------------------------
# Production safety: fail-fast if SECRET_KEY is the insecure default
# ---------------------------------------------------------------------------
_is_production = is_production_like(settings)
_startup_failures = collect_startup_failures(settings)
if _startup_failures:
    raise RuntimeError(_startup_failures[0])

# ---------------------------------------------------------------------------
# Disable interactive API docs in production (information disclosure)
# ---------------------------------------------------------------------------
_docs_url = None if _is_production else "/api/docs"
_openapi_url = None if _is_production else "/api/openapi.json"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Startup
    logger.info("%s API started | env=%s", BRAND_NAME, "production" if settings.SENTRY_DSN else "development")
    # Wire candidate_graph SQLAlchemy listeners (no-op when Graphiti is unset).
    try:
        from .candidate_graph.listeners import register_listeners

        register_listeners()
    except Exception:  # pragma: no cover — listener install must never block boot
        logger.exception("Failed to register candidate_graph listeners")
    # Kick off Graphiti init in a background thread so Neo4j async resources
    # are created on the shared background event loop before the first real
    # request arrives. The healthcheck returns "initializing" until ready.
    try:
        from .candidate_graph import client as _gc
        if _gc.is_configured():
            import threading as _t
            _t.Thread(target=_gc.get_graphiti, name="graphiti-init", daemon=True).start()
    except Exception:
        logger.exception("Failed to start Graphiti background init")
    yield
    # Shutdown — close Graphiti's driver and stop its background loop.
    try:
        from .candidate_graph.client import close

        close()
    except Exception:  # pragma: no cover — defensive
        logger.exception("Failed to close Graphiti on shutdown")


app = FastAPI(
    title=f"{BRAND_NAME} API",
    description=BRAND_APP_DESCRIPTION,
    version="1.0.0",
    docs_url=_docs_url,
    openapi_url=_openapi_url,
    lifespan=_lifespan,
)

_val_logger = _logging.getLogger("taali.validation")


def _is_configured_secret(value: str | None) -> bool:
    cleaned = (value or "").strip().lower()
    return cleaned not in {"", "skip", "changeme"}


def _sanitize_errors(errors: list) -> list:
    """Ensure validation error details are JSON-serializable."""

    def _json_safe(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        if isinstance(value, BaseException):
            return str(value)
        return str(value)

    return [_json_safe(err) for err in errors]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log validation errors with detail so we can diagnose 422s."""
    _val_logger.warning(
        "Validation error on %s %s: %s | body=%s",
        request.method,
        request.url.path,
        exc.errors(),
        exc.body,
    )
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_errors(exc.errors())},
    )


# ---------------------------------------------------------------------------
# Friendly API error messages (rewrite raw codes for frontend)
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, str) and detail in _API_ERROR_MESSAGES:
        detail = _API_ERROR_MESSAGES[detail]
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-hardening HTTP headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response: StarletteResponse = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if _is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)

def _normalize_origin(origin: str | None) -> str | None:
    cleaned = (origin or "").strip().rstrip("/")
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return cleaned


def _frontend_origins(frontend_url: str | None) -> list[str]:
    primary = _normalize_origin(frontend_url)
    if not primary:
        return []

    origins = [primary]
    parsed = urlparse(primary)
    host = parsed.hostname or ""
    if host.startswith("www."):
        port = f":{parsed.port}" if parsed.port else ""
        origins.append(f"{parsed.scheme}://{host[4:]}{port}")
    return origins


def _build_cors_origins(frontend_url: str | None, extra_origins: str | None) -> list[str]:
    origins = [
        *_frontend_origins(frontend_url),
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    if extra_origins:
        origins.extend(_normalize_origin(origin) for origin in extra_origins.split(","))

    deduped = []
    seen = set()
    for origin in origins:
        if not origin or origin in seen:
            continue
        seen.add(origin)
        deduped.append(origin)
    return deduped


# CORS: frontend URL + localhost + any extra origins (e.g. Vercel production URL)
_cors_origins = _build_cors_origins(
    settings.FRONTEND_URL,
    getattr(settings, "CORS_EXTRA_ORIGINS", None),
)
_cors_origin_regex = settings.CORS_ALLOW_ORIGIN_REGEX
# If frontend is on Vercel, allow preview/production subdomains by default.
if not _cors_origin_regex and "vercel.app" in (settings.FRONTEND_URL or ""):
    _cors_origin_regex = r"https://.*\.vercel\.app"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in _cors_origins if o],
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # Include tracing headers used by browser SDKs (Sentry/OpenTelemetry) so
    # preflight requests do not block API calls such as /applications.
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Assessment-Token",
        "X-Requested-With",
        "Baggage",
        "Sentry-Trace",
        "Traceparent",
        "Tracestate",
    ],
)

# Rate limiting (auth and assessment endpoints)
app.add_middleware(RateLimitMiddleware)

# Enterprise access controls (SSO enforcement on password-auth endpoints)
app.add_middleware(EnterpriseAccessMiddleware)

# Request logging
app.add_middleware(RequestLoggingMiddleware)

# Sentry (optional)
if settings.SENTRY_DSN and settings.SENTRY_DSN.startswith("https://"):
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
    )

# Include routers
from .api.v1.users_fastapi import (
    UserRead,
    UserCreate,
    UserUpdate,
    auth_backend,
    fastapi_users,
)
from .api.v1.assessments import router as assessments_router
from .api.v1.organizations import router as organizations_router
from .api.v1.webhooks import router as webhooks_router
from .api.v1.tasks import router as tasks_router
from .api.v1.analytics import router as analytics_router
from .api.v1.billing import router as billing_router
from .api.v1.candidates import router as candidates_router
from .api.v1.roles import router as roles_router
from .api.v1.scoring import router as scoring_router
from .api.v1.users import router as users_router
from .api.v1.workable import router as workable_router
from .api.v1.auth import router as auth_router

# FastAPI-Users auth routers
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/api/v1/auth/jwt",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/v1/users",
    tags=["users"],
)

app.include_router(assessments_router, prefix="/api/v1")
app.include_router(organizations_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(candidates_router, prefix="/api/v1")
app.include_router(roles_router, prefix="/api/v1")
app.include_router(scoring_router, prefix="/api/v1")
app.include_router(workable_router, prefix="/api/v1")

# cv_match_v3.0 admin + override surface (gated server-side; flag controls runner)
from .cv_matching.routes import (
    admin_router as cv_match_admin_router,
    override_router as cv_match_override_router,
)
app.include_router(cv_match_admin_router, prefix="/api/v1")
app.include_router(cv_match_override_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    db_ok = False
    redis_ok = False
    try:
        from sqlalchemy import text
        from .platform.database import SessionLocal
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        db_ok = False
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        redis_ok = bool(r.ping())
    except Exception:
        redis_ok = False

    integrations = {
        "e2b_configured": _is_configured_secret(settings.E2B_API_KEY),
        "claude_configured": _is_configured_secret(settings.ANTHROPIC_API_KEY),
        "workable_configured": _is_configured_secret(settings.WORKABLE_CLIENT_ID) and _is_configured_secret(settings.WORKABLE_CLIENT_SECRET),
        "stripe_configured": _is_configured_secret(settings.STRIPE_API_KEY),
    }

    try:
        from .services.s3_service import s3_status
        s3_health = s3_status()
    except Exception:
        s3_health = {"available": False, "reason": "probe_error"}

    # S3 down doesn't degrade the API: cv_text persists in Postgres
    # regardless. Surface the state for ops visibility but don't change
    # the top-level status_str unless DB or Redis is actually broken.
    status_str = "healthy" if db_ok and redis_ok else "degraded"
    return {
        "status": status_str,
        "service": "taali-api",
        "database": db_ok,
        "redis": redis_ok,
        "s3": s3_health,
        "integrations": integrations,
    }


@app.get("/healthz/graphiti")
def graphiti_health():
    """Per-component health probe used by the Railway setup verification step.

    Returns ``{status: ok|unconfigured|error}``. ``unconfigured`` means
    NEO4J_URI or VOYAGE_API_KEY is empty — graph features are disabled
    by design, not a fault.
    """
    from .candidate_graph.client import healthcheck

    return healthcheck()


@app.get("/admin/graphiti/stats")
def graphiti_stats(request: Request):
    """Return CV + graph sync counts for operational visibility."""
    from .platform.config import settings
    from .platform.database import SessionLocal
    from .models.candidate import Candidate
    from .models.graph_sync_state import GraphSyncState
    from sqlalchemy import func

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    db = SessionLocal()
    try:
        total_candidates = db.query(func.count(Candidate.id)).filter(Candidate.deleted_at.is_(None)).scalar()
        with_cv = db.query(func.count(Candidate.id)).filter(
            Candidate.deleted_at.is_(None),
            Candidate.cv_text.isnot(None),
            Candidate.cv_text != "",
        ).scalar()
        synced_to_graph = db.query(func.count(GraphSyncState.candidate_id)).scalar()
    finally:
        db.close()

    from .candidate_graph import client as graph_client
    neo4j_ok = False
    neo4j_node_count = None
    if graph_client.is_configured():
        try:
            graphiti = graph_client.get_graphiti()
            result = graph_client.run_async(
                graphiti.driver.execute_query("MATCH (n) RETURN count(n) AS c"),
                timeout=10.0,
            )
            # neo4j driver returns EagerResult; records[0]["c"] gives the count
            neo4j_node_count = result.records[0]["c"] if result and result.records else None
            neo4j_ok = True
        except Exception as exc:
            neo4j_node_count = f"error: {exc}"

    sample_companies = []
    sample_facts = []
    if neo4j_ok:
        try:
            r2 = graph_client.run_async(
                graphiti.driver.execute_query(
                    "MATCH (n:Entity) WHERE n.name IS NOT NULL RETURN DISTINCT n.name AS name LIMIT 30"
                ),
                timeout=10.0,
            )
            sample_companies = [rec["name"] for rec in (r2.records or [])]
            r3 = graph_client.run_async(
                graphiti.driver.execute_query(
                    "MATCH ()-[e:RELATES_TO]->() WHERE e.fact IS NOT NULL RETURN e.fact AS fact LIMIT 10"
                ),
                timeout=10.0,
            )
            sample_facts = [rec["fact"] for rec in (r3.records or [])]
        except Exception as exc:
            sample_companies = [f"error: {exc}"]

    return {
        "candidates": {"total": total_candidates, "with_cv_text": with_cv},
        "graph_sync_state": {"synced_candidates": synced_to_graph},
        "neo4j": {"ok": neo4j_ok, "total_nodes": neo4j_node_count, "sample_entities": sample_companies, "sample_facts": sample_facts},
    }


@app.post("/admin/graphiti/backfill")
def graphiti_backfill_all(request: Request):
    """Trigger a full Graphiti backfill for all organisations.

    Optional query param: ``since_year=2026`` limits to candidates created
    on or after 1 Jan of that year. Returns 202 immediately; backfill runs
    as a background thread. Check Railway logs for progress and final summary.
    """
    from .platform.config import settings
    from .platform.database import SessionLocal
    from .candidate_graph.sync import sync_all_organizations
    import threading

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    since_year_str = request.query_params.get("since_year")
    since_year = int(since_year_str) if since_year_str and since_year_str.isdigit() else None
    cv_only = request.query_params.get("cv_only", "false").lower() == "true"

    def _run():
        import logging
        log = logging.getLogger("taali.candidate_graph.backfill")
        db = SessionLocal()
        try:
            result = sync_all_organizations(db, since_year=since_year, cv_only=cv_only)
            log.info("Graphiti backfill complete: %s", result)
        except Exception as _exc:
            log.exception("Graphiti backfill failed: %s: %s", type(_exc).__name__, _exc)
        finally:
            db.close()

    threading.Thread(target=_run, name="graphiti-backfill", daemon=True).start()
    return {
        "status": "started",
        "since_year": since_year,
        "cv_only": cv_only,
        "message": "Backfill running in background — check Railway logs for progress",
    }


@app.post("/admin/cv-score/batch-all")
def admin_batch_score_all(request: Request):
    """Admin trigger for batch-score-all across every role in every org.

    Query params:
      applied_after  ISO date string (e.g. 2026-01-01) to filter by Workable application date
      include_scored bool (default false) — rescore already-scored apps
      org_id         int — limit to a single org (optional)

    Uses X-Admin-Secret header for auth (no user JWT required).
    """
    from .platform.config import settings
    from .platform.database import SessionLocal
    from .models.role import Role
    from .models.candidate_application import CandidateApplication
    from .models.candidate import Candidate
    from datetime import datetime, timezone

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    applied_after = request.query_params.get("applied_after")
    include_scored = request.query_params.get("include_scored", "false").lower() == "true"
    org_id_str = request.query_params.get("org_id")
    org_id_filter = int(org_id_str) if org_id_str and org_id_str.isdigit() else None

    db = SessionLocal()
    try:
        roles_q = db.query(Role).filter(Role.deleted_at.is_(None))
        if org_id_filter:
            roles_q = roles_q.filter(Role.organization_id == org_id_filter)
        roles = roles_q.all()

        dispatched = []
        skipped = []
        for role in roles:
            if not (role.job_spec_text or "").strip():
                skipped.append({"role_id": role.id, "reason": "no_job_spec"})
                continue

            count_q = db.query(CandidateApplication).filter(
                CandidateApplication.role_id == role.id,
                CandidateApplication.organization_id == role.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
            if not include_scored:
                count_q = count_q.filter(CandidateApplication.cv_match_score.is_(None))
            if applied_after:
                try:
                    cutoff = datetime.fromisoformat(applied_after)
                    if cutoff.tzinfo is None:
                        cutoff = cutoff.replace(tzinfo=timezone.utc)
                    count_q = (
                        count_q
                        .join(Candidate, CandidateApplication.candidate_id == Candidate.id)
                        .filter(Candidate.workable_created_at >= cutoff)
                    )
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid applied_after: {applied_after}")

            target = count_q.count()
            if target == 0:
                skipped.append({"role_id": role.id, "reason": "nothing_to_score"})
                continue

            from .tasks.scoring_tasks import batch_score_role as _celery_batch_score_role
            _celery_batch_score_role.delay(
                role.id,
                include_scored=include_scored,
                applied_after=applied_after,
            )
            dispatched.append({"role_id": role.id, "org_id": role.organization_id, "target": target})

        return {
            "status": "dispatched",
            "dispatched": len(dispatched),
            "skipped": len(skipped),
            "total_target": sum(d["target"] for d in dispatched),
            "roles": dispatched,
            "applied_after": applied_after,
            "include_scored": include_scored,
        }
    finally:
        db.close()


@app.get("/admin/graphiti/search-debug")
def graphiti_search_debug(request: Request):
    """Raw Graphiti search result shape for debugging the graph view."""
    from .platform.config import settings
    from .candidate_graph import client as graph_client

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    if not admin_secret or request.headers.get("X-Admin-Secret", "") != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    query = request.query_params.get("q", "full stack developer")
    org_id = int(request.query_params.get("org_id", "0"))
    group_id = graph_client.group_id_for_org(org_id) if org_id else None

    graphiti = graph_client.get_graphiti()
    try:
        results = graph_client.run_async(
            graphiti.search(
                query=query,
                group_ids=[group_id] if group_id else None,
                num_results=5,
            ),
            timeout=15.0,
        )
    except Exception as exc:
        return {"error": str(exc)}

    if results is None:
        return {"results": None, "count": 0}

    items = results if isinstance(results, (list, tuple)) else getattr(results, "edges", results) or []
    out = []
    for item in list(items)[:5]:
        source = getattr(item, "source_node", None)
        target = getattr(item, "target_node", None)
        out.append({
            "type": type(item).__name__,
            "uuid": getattr(item, "uuid", None),
            "fact": getattr(item, "fact", None),
            "has_source_node": source is not None,
            "has_target_node": target is not None,
            "source_uuid": getattr(source, "uuid", None) if source else None,
            "source_name": getattr(source, "name", None) if source else None,
            "target_uuid": getattr(target, "uuid", None) if target else None,
            "target_name": getattr(target, "name", None) if target else None,
            "group_id": getattr(item, "group_id", None),
        })
    return {"query": query, "group_id": group_id, "count": len(list(items)), "results": out}


@app.get("/admin/graphiti/cypher-debug")
def graphiti_cypher_debug(request: Request):
    """Run the actual Cypher subgraph query and show raw records."""
    from .platform.config import settings
    from .candidate_graph import client as graph_client

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    if not admin_secret or request.headers.get("X-Admin-Secret", "") != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    query = request.query_params.get("q", "full stack developer")
    org_id = int(request.query_params.get("org_id", "2"))
    group_id = f"org-{org_id}"

    graphiti = graph_client.get_graphiti()
    out = {"group_id": group_id, "query": query}

    def safe_records(r):
        rows = []
        for rec in (r.records or []):
            row = {}
            for k in rec.keys():
                v = rec[k]
                row[k] = str(v) if v is not None else None
            rows.append(row)
        return rows

    # What relationship types exist?
    try:
        r = graph_client.run_async(
            graphiti.driver.execute_query("MATCH ()-[e]->() RETURN DISTINCT type(e) AS t LIMIT 10"),
            timeout=10.0,
        )
        out["rel_types"] = [str(rec["t"]) for rec in (r.records or [])]
    except Exception as exc:
        out["rel_types_error"] = str(exc)

    # Sample edges for this org — any relationship type, no params
    safe_gid = group_id.replace("'", "")  # sanitise (group_id is always "org-N")
    try:
        r = graph_client.run_async(
            graphiti.driver.execute_query(
                f"MATCH (s)-[e]->(t) WHERE e.group_id = '{safe_gid}' "
                f"RETURN type(e) AS rel, e.fact AS fact, s.name AS s, t.name AS t LIMIT 5"
            ), timeout=10.0,
        )
        out["org_edges_sample"] = safe_records(r)
    except Exception as exc:
        out["org_edges_error"] = str(exc)

    # Try the actual subgraph Cypher without params
    safe_q = query.lower().replace("'", "")
    try:
        r = graph_client.run_async(
            graphiti.driver.execute_query(
                f"MATCH (s:Entity)-[e:RELATES_TO]->(t:Entity) "
                f"WHERE e.group_id = '{safe_gid}' "
                f"AND toLower(e.fact) CONTAINS '{safe_q}' "
                f"RETURN s.uuid AS s_uuid, s.name AS s, t.uuid AS t_uuid, t.name AS t, "
                f"e.name AS e_name, e.fact AS fact LIMIT 10"
            ), timeout=10.0,
        )
        out["cypher_matches"] = safe_records(r)
    except Exception as exc:
        out["cypher_error"] = str(exc)

    return out


@app.post("/admin/graphiti/test-episode")
def graphiti_test_episode(request: Request):
    """Send one synthetic episode to Graphiti and return success or error detail.

    Used to verify the add_episode pipeline end-to-end after setup.
    """
    from .platform.config import settings
    from .candidate_graph import client as graph_client
    from .candidate_graph.episodes import Episode, dispatch

    admin_secret = getattr(settings, "ADMIN_SECRET", "") or ""
    provided = request.headers.get("X-Admin-Secret", "")
    if not admin_secret or provided != admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    import traceback
    from datetime import datetime, timezone
    from graphiti_core.nodes import EpisodeType  # type: ignore[import-not-found]

    ep = Episode(
        name="test-episode-debug",
        body="Subject candidate: Test Person (taali_id=0)\nThis is a test episode for connectivity verification.",
        source_description="admin.test",
        reference_time=datetime.now(timezone.utc),
        group_id="org-0",
    )
    try:
        graphiti = graph_client.get_graphiti()
        graph_client.run_async(
            graphiti.add_episode(
                name=ep.name,
                episode_body=ep.body,
                source=EpisodeType.text,
                source_description=ep.source_description,
                reference_time=ep.reference_time,
                group_id=ep.group_id,
            ),
            timeout=120.0,
        )
        return {"status": "ok", "episodes_sent": 1}
    except Exception as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }

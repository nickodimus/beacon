"""FastAPI app: one authenticated write, one open health check.

  POST /beacon/trigger   Bearer token required. Body {"source": "signal",
                         "device": "all"}. Publishes {source, ts} to
                         beacon/<device>/trigger, QoS 1, not retained.
  GET  /beacon/health    No auth, no secrets — liveness only.

Paths carry the /beacon prefix so Apache can `ProxyPass /beacon` straight
through without rewriting.

EVERYTHING FAILS CLOSED. No token configured → 503, every trigger refused.
Bad/absent token → 401. Malformed body → 422. Rate ceiling hit → 429 (a
real per-process counter, not a request to the caller). Broker unreachable
or refusing → 502.
"""

import hmac
import time
from collections import deque

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.status import (HTTP_401_UNAUTHORIZED, HTTP_429_TOO_MANY_REQUESTS,
                              HTTP_502_BAD_GATEWAY, HTTP_503_SERVICE_UNAVAILABLE)

from .config import load
from .log import get_logger
from .publish import PublishError, trigger

log = get_logger("web")

cfg = load()
app = FastAPI(title="beacon", docs_url=None, redoc_url=None, openapi_url=None)

log.info("beacon up — bind %s:%d, broker %s:%d as %s, token %s, cap %d/min",
         cfg.bind_host, cfg.bind_port, cfg.mqtt_host, cfg.mqtt_port,
         cfg.mqtt_user or "<anonymous>",
         "configured" if cfg.has_token else "NOT SET (triggers refused)",
         cfg.max_per_min)
if not cfg.has_token:
    log.error("api_token is empty — every trigger is refused with 503 until it "
              "is set by hand in the config. It must never enter the repo or a log.")


# ------------------------------------------------------------------ auth
def require_token(request: Request):
    """Bearer auth. Constant-time compare; no part of the token is logged."""
    if not cfg.has_token:
        log.error("refused %s %s — api_token is not configured",
                  request.method, request.url.path)
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail="api token is not configured on this server")
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        log.warning("refused %s %s — no bearer credentials",
                    request.method, request.url.path)
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED,
                            detail="bearer token required",
                            headers={"WWW-Authenticate": "Bearer"})
    if not hmac.compare_digest(presented.strip(), cfg.api_token):
        log.warning("refused %s %s — bad bearer token",
                    request.method, request.url.path)
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED,
                            detail="invalid token",
                            headers={"WWW-Authenticate": "Bearer"})
    return True


# ------------------------------------------------------------ rate limit
_fired_at = deque()  # monotonic timestamps of accepted triggers, newest last


def check_rate():
    """Enforced per-process ceiling. Prunes anything older than 60s, then
    refuses if the window is already full. A phone automation stuck in a
    loop gets 429s, not a strobe."""
    now = time.monotonic()
    while _fired_at and now - _fired_at[0] > 60:
        _fired_at.popleft()
    if len(_fired_at) >= cfg.max_per_min:
        log.warning("rate ceiling hit — %d triggers in the last minute, refusing",
                    len(_fired_at))
        raise HTTPException(status_code=HTTP_429_TOO_MANY_REQUESTS,
                            detail=f"rate limit is {cfg.max_per_min}/min")
    _fired_at.append(now)


# --------------------------------------------------------------- shapes
# `device` becomes a topic segment, so it is held to characters that can
# never be an MQTT wildcard or separator. `source` is only payload, but is
# capped the same way so a log line stays one line.
class TriggerIn(BaseModel):
    source: str = Field(min_length=1, max_length=32,
                        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    device: str = Field(default="all", min_length=1, max_length=32,
                        pattern=r"^[a-z0-9][a-z0-9-]*$")


# -------------------------------------------------------------- handlers
@app.get("/beacon/health")
def health():
    return {"status": "ok"}


@app.post("/beacon/trigger")
def beacon_trigger(payload: TriggerIn, _auth: bool = Depends(require_token)):
    check_rate()  # only counts against the ceiling once past auth + validation
    try:
        topic, ts = trigger(cfg, payload.device, payload.source)
    except PublishError as exc:
        log.error("publish failed for device=%s source=%s: %s",
                  payload.device, payload.source, exc)
        raise HTTPException(status_code=HTTP_502_BAD_GATEWAY,
                            detail=f"broker publish failed: {exc}")
    log.info("published %s source=%s ts=%d", topic, payload.source, ts)
    return JSONResponse({"status": "published", "topic": topic,
                         "source": payload.source, "ts": ts})

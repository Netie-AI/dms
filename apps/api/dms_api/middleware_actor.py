"""The actor trust boundary. Identity comes from server configuration, never a request.

DR-0004 Option A: the first install is single-tenant, reachable only over VPN or an
air-gapped network, and the limitation is named in the SOW - anyone who can reach the
host can act as the configured steward. There is no identity provider, so there is
nothing a request could carry that would be worth believing.

This used to be ``DevActorMiddleware``, which read ``x-dms-tenant-id``,
``x-dms-actor-id`` and ``x-dms-role`` off the request and stashed them on
``request.state``. Nothing ever read them back, so the header path was inert - but it
was one honest-looking edit away from being live, and an unverified ``x-dms-role``
honoured by a route is privilege escalation. DR-0004 records that wiring it through was
considered and rejected precisely because it would have arrived wearing the appearance
of progress.

Deleting the middleware would have left the same hole open to whoever added the headers
back. Refusing is enforcement: a request that tries to name its own identity is turned
away, and the refusal says why. That also makes the boundary testable, which the
Confirmation section of DR-0004 requires before it may move to ``accepted``.

When DR-0004 is revisited and Option B is chosen, this middleware is replaced by a
credential verifier - not relaxed.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Headers a client might use to assert who it is. Refused, not ignored.
IDENTITY_HEADERS = ("x-dms-tenant-id", "x-dms-actor-id", "x-dms-role")


class RejectIdentityHeadersMiddleware(BaseHTTPMiddleware):
    """Refuse any request that tries to name its own tenant, actor or role."""

    async def dispatch(self, request: Request, call_next) -> Response:
        offered = [h for h in IDENTITY_HEADERS if h in request.headers]
        if offered:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "identity_header_not_accepted",
                    "message": (
                        "This deployment resolves identity from server configuration, "
                        "not from the request. Remove "
                        + ", ".join(sorted(offered))
                        + " and retry."
                    ),
                    "headers": sorted(offered),
                    "decision_record": "DR-0004",
                },
            )
        return await call_next(request)

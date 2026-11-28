"""HTTP GET and PUT that does not follow redirects and stops at a byte cap."""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping

from dms_executor.drivers import DriverError
from dms_executor.drivers.limits import BYTE_CAP, CONNECT_TIMEOUT_S


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def exchange(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: bytes = b"",
    *,
    timeout: int = CONNECT_TIMEOUT_S,
    cap: int = BYTE_CAP,
) -> tuple[int, dict[str, str], bytes]:
    """Return status, headers, and at most ``cap + 1`` body bytes.

    A redirect is ``network``. The body is not decoded here.
    """
    outgoing = dict(headers)
    data = None if method.upper() == "GET" else payload
    # urllib stamps application/x-www-form-urlencoded on any body, including
    # an empty one. Azure signs Content-Type, so an empty body must not grow
    # a type the signer did not see.
    if data == b"" and not any(key.lower() == "content-type" for key in outgoing):
        outgoing["Content-Type"] = ""
    request = urllib.request.Request(url, data=data, headers=outgoing, method=method.upper())
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            header_map = {key.lower(): value for key, value in response.headers.items()}
            body = response.read(cap + 1)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        header_map = {key.lower(): value for key, value in exc.headers.items()}
        body = exc.read(cap + 1)
    except DriverError:
        raise
    except Exception:
        raise DriverError("network") from None
    if status in {301, 302, 303, 307, 308}:
        raise DriverError("network")
    return status, header_map, body

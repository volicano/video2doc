"""Narrow compatibility fixes for yt-dlp extractors.

yt-dlp's Douyin extractor started receiving HTTP 403 from the web detail API
after Douyin began requiring a signed ``x-secsdk-web-signature`` query
parameter.  Upstream fixed this in yt-dlp PR #17826, but as of the latest
release (2026.10.21) the fix is not available on PyPI yet.  The request-signing
logic below is adapted from that PR (yt-dlp is released under the Unlicense).

Keep this shim as small as possible and skip it automatically once a released
yt-dlp starts exposing ``DouyinIE._SEC_SALT``.  Remove it when the upstream
release containing the fix is our minimum supported version.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import uuid
from typing import Any

_SEC_SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"
_REQUIRED_COOKIES = ["passport_csrf_token", "ttwid"]
_AID = 6383


def ensure_douyin_compat() -> bool:
    """Add the missing Douyin request signing to yt-dlp's extractor.

    Returns ``True`` when the shim was installed, ``False`` when yt-dlp already
    has the fix or the shim was installed before.
    """
    from yt_dlp.extractor.tiktok import DouyinIE
    from yt_dlp.utils import ExtractorError, traverse_obj

    if getattr(DouyinIE, "_v2doc_douyin_compat", False):
        return False
    if getattr(DouyinIE, "_SEC_SALT", None):
        return False

    def get_ttwid(self: Any) -> str | None:
        self._request_webpage(
            f"{self._WEBPAGE_HOST}ttwid/register",
            None,
            data=json.dumps({"aid": _AID, "service": "www.douyin.com"}).encode(),
            headers={"content-type": "application/json"},
        )
        cookie = self._get_cookies(self._WEBPAGE_HOST).get("ttwid")
        return cookie.value if cookie else None

    def check_and_get_required_cookies(self: Any) -> None:
        for name in _REQUIRED_COOKIES:
            if self._get_cookies(self._WEBPAGE_HOST).get(name):
                continue
            cookie = get_ttwid(self) if name == "ttwid" else uuid.uuid4().hex
            if not cookie:
                raise ExtractorError("Unable to generate required cookies")
            self._set_cookie(".douyin.com", name, cookie)

    def get_uifid(self: Any) -> str | None:
        cookie_names = ("uifid", "uifid_temp", "uifidtemp", "UIFID", "UIFID_TEMP", "UIFIDTEMP")
        uifid = traverse_obj(
            self._get_cookies(self._WEBPAGE_HOST), (cookie_names, "value"), get_all=False
        )
        if uifid:
            return uifid
        response = self._request_webpage(self._WEBPAGE_HOST, None)
        return response.headers.get("x-tt-trace-host")

    def sign_query(self: Any, query: dict[str, Any]) -> dict[str, Any]:
        uifid = get_uifid(self)
        timestamp = str(int(time.time()))
        query = {**query, "uifid": uifid, "timestamp": timestamp}
        encoded_query = urllib.parse.urlencode(query)
        signature = hashlib.md5(  # noqa: S324 - protocol requires MD5
            f"{uifid}_{timestamp}_{_SEC_SALT}_{encoded_query}".encode()
        ).hexdigest()
        return {**query, "x-secsdk-web-signature": signature}

    def real_extract(self: Any, url: str) -> dict[str, Any]:
        video_id = self._match_id(url)
        check_and_get_required_cookies(self)
        detail = traverse_obj(
            self._download_json(
                "https://www.douyin.com/aweme/v1/web/aweme/detail/",
                video_id,
                "Downloading web detail JSON",
                "Failed to download web detail JSON",
                query=sign_query(self, {"aweme_id": video_id, "aid": _AID}),
                fatal=False,
            ),
            ("aweme_detail", {dict}),
        )
        if not detail:
            raise ExtractorError(
                "Fresh cookies (not necessarily logged in) are needed",
                expected=not self._get_cookies(self._WEBPAGE_HOST).get("s_v_web_id"),
            )
        return {
            **self._parse_aweme_video_app(detail),
            "http_headers": {"Referer": self._WEBPAGE_HOST},
        }

    DouyinIE._SEC_SALT = _SEC_SALT
    DouyinIE._REQUIRED_COOKIES = _REQUIRED_COOKIES
    DouyinIE._AID = _AID
    DouyinIE._CSRF = uuid.uuid4().hex
    DouyinIE._get_ttwid = get_ttwid
    DouyinIE.check_and_get_required_cookies = check_and_get_required_cookies
    DouyinIE._get_uifid = get_uifid
    DouyinIE._sign_query = sign_query
    DouyinIE._real_extract = real_extract
    DouyinIE._v2doc_douyin_compat = True
    return True

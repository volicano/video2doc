from __future__ import annotations

from video2doc.utils.ytdlp_compat import ensure_douyin_compat


def test_douyin_compat_is_idempotent() -> None:
    from yt_dlp.extractor.tiktok import DouyinIE

    ensure_douyin_compat()
    assert getattr(DouyinIE, "_SEC_SALT", None)

    # A second call must not replace the extractor's methods or rotate its CSRF value.
    assert ensure_douyin_compat() is False

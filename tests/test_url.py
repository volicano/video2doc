from __future__ import annotations

from video2doc.utils.url import (
    canonicalize_douyin_url,
    douyin_video_id,
    extract_url,
    is_douyin_short_url,
    is_douyin_url,
)


def test_extract_url_from_share_text() -> None:
    text = "7.84 复制打开抖音，看看【某某的作品】 https://v.douyin.com/iRNBho6u/ 复制此链接"
    assert extract_url(text) == "https://v.douyin.com/iRNBho6u/"


def test_extract_url_strips_chinese_punctuation_and_text() -> None:
    text = "视频在这里：https://www.douyin.com/video/7298145681699622182，欢迎观看。"
    assert extract_url(text) == "https://www.douyin.com/video/7298145681699622182"
    assert extract_url("https://v.douyin.com/iRNBho6u/复制此链接") == (
        "https://v.douyin.com/iRNBho6u/"
    )
    assert extract_url("https://v.douyin.com/iRNBho6u/😄复制") == "https://v.douyin.com/iRNBho6u/"


def test_extract_url_returns_none_without_url() -> None:
    assert extract_url("只有一段普通文字") is None


def test_douyin_video_id_from_canonical_and_share_urls() -> None:
    video_id = "7298145681699622182"
    assert douyin_video_id(f"https://www.douyin.com/video/{video_id}") == video_id
    assert douyin_video_id(f"https://www.iesdouyin.com/share/video/{video_id}/") == video_id


def test_douyin_video_id_from_modal_query() -> None:
    url = "https://www.douyin.com/discover?modal_id=7298145681699622182"
    assert douyin_video_id(url) == "7298145681699622182"


def test_short_link_has_no_video_id_and_is_recognized() -> None:
    url = "https://v.douyin.com/iRNBho6u/"
    assert is_douyin_url(url)
    assert is_douyin_short_url(url)
    assert douyin_video_id(url) is None


def test_canonicalize_share_page_and_long_link() -> None:
    expected = "https://www.douyin.com/video/7298145681699622182"
    share_url = "https://www.iesdouyin.com/share/video/7298145681699622182/"
    assert canonicalize_douyin_url(share_url) == expected
    assert canonicalize_douyin_url(
        "https://www.douyin.com/video/7298145681699622182?previous_page=web_code_link"
    ) == expected

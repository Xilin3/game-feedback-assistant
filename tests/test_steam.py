from urllib.parse import parse_qs, urlparse

import pytest

from src.steam import app_id, fetch_reviews, steam_batch


def row(rid="1", text="真实接口格式测试，人工构造"):
    return {"recommendationid": rid, "review": text, "language": "schinese", "voted_up": False,
            "timestamp_created": 1700000000, "author": {"steamid": "76561198000000000"}}


def test_pagination_dedup_and_cache(tmp_path):
    urls = []
    pages = iter([{"success": 1, "reviews": [row(), row()], "cursor": "next+/="},
                  {"success": 1, "reviews": [row("2")], "cursor": "last"}])
    def request(url):
        urls.append(url)
        return next(pages)
    data = fetch_reviews("https://store.steampowered.com/app/123/title/", count=2,
                         cache_dir=tmp_path, request_json=request, pause=lambda _: None)
    assert len(data["reviews"]) == 2
    assert data["source_metadata"]["duplicates_skipped"] == 1
    assert parse_qs(urlparse(urls[1]).query)["cursor"] == ["next+/="]
    assert data["reviews"][0]["source_url"].endswith('/recommended/123/')
    assert steam_batch(data)["source_metadata"]["actual_count"] == 2
    cached = fetch_reviews(123, count=2, cache_dir=tmp_path, request_json=lambda _: pytest.fail("cache miss"))
    assert cached["source_metadata"]["cache_hit"]


def test_exhausted_and_invalid_rows(tmp_path):
    pages = iter([{"success":1,"reviews":[row(),row("2", " ")],"cursor":"next"},
                  {"success":1,"reviews":[],"cursor":"end"}])
    data = fetch_reviews(123, cache_dir=tmp_path, request_json=lambda _:next(pages), pause=lambda _:None)
    assert data["source_metadata"]["stop_reason"] == "exhausted"
    assert data["source_metadata"]["invalid_skipped"] == 1
    assert len(data["reviews"]) == 1


@pytest.mark.parametrize("value", [None,True,0,-1,"https://evil.test/app/123/","1/../2","abc",4294967296])
def test_bad_id(value):
    with pytest.raises(ValueError):
        app_id(value)


def test_api_error_not_cached(tmp_path):
    with pytest.raises(ValueError):
        fetch_reviews(123, cache_dir=tmp_path, request_json=lambda _: {"success":2})
    assert not list(tmp_path.glob('*.json'))


def test_cursor_stalled(tmp_path):
    data = fetch_reviews(123, cache_dir=tmp_path,
                         request_json=lambda _: {"success":1,"reviews":[row()],"cursor":"*"})
    assert data["source_metadata"]["stop_reason"] == "cursor_stalled"
    assert data["source_metadata"]["actual_count"] == 1

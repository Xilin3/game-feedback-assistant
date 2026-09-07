"""Fetch public Steam reviews via the documented cursor API; no login needed."""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from .domain import now, parse_import

LANGUAGES = {"schinese": "简体中文", "tchinese": "繁体中文", "english": "英语", "all": "全部语言"}


def app_id(value):
    if type(value) not in (str, int):
        raise ValueError("请输入 Steam App ID 或商店链接")
    text = str(value).strip()
    if text.startswith("https://"):
        url = urlparse(text)
        if url.hostname != "store.steampowered.com":
            raise ValueError("请使用 store.steampowered.com 的游戏链接")
        match = re.match(r"^/app/([0-9]+)(?:/|$)", url.path)
        text = match[1] if match else ""
    if not re.fullmatch(r"[0-9]{1,10}", text) or not 0 < int(text) <= 4294967295:
        raise ValueError("无效 Steam App ID，请输入正整数或游戏商店链接")
    return str(int(text))


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "GameFeedbackAssistant/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("Steam 返回内容过大")
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Steam HTTP {exc.code}，请稍后重试") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("无法连接 Steam，请检查网络后重试") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Steam 未返回有效 JSON，请稍后重试") from None


def normalize(row, aid):
    if not isinstance(row, dict):
        raise ValueError("无效评论")
    rid, text = row.get("recommendationid"), row.get("review")
    if not isinstance(rid, str) or not rid.isascii() or not rid.isdigit():
        raise ValueError("无效评论 ID")
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError("空评论或评论过长")
    timestamp = row.get("timestamp_created")
    if type(timestamp) is not int or timestamp < 0 or type(row.get("voted_up")) is not bool:
        raise ValueError("评论元数据无效")
    language = row.get("language")
    if not isinstance(language, str) or len(language) > 40:
        raise ValueError("评论语言无效")
    author = row.get("author") or {}
    steamid = author.get("steamid") if isinstance(author, dict) else None
    link = f"https://steamcommunity.com/profiles/{steamid}/recommended/{aid}/" if isinstance(steamid, str) and re.fullmatch(r"[0-9]{17}", steamid) else f"https://steamcommunity.com/app/{aid}/reviews/"
    return {"review_id": f"steam-{rid}", "text": text, "source_url": link,
            "created_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            "language": language, "recommended": row["voted_up"]}


def fetch_reviews(value, count=300, language="schinese", review_type="all", game_name=None,
                  refresh=False, cache_dir="data/raw/steam", request_json=None, pause=time.sleep):
    aid = app_id(value)
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("评论数量必须为 1–1000 的整数")
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError("不支持的评论语言")
    if review_type not in ("all", "positive", "negative"):
        raise ValueError("无效的好评／差评筛选")
    if type(refresh) is not bool:
        raise ValueError("refresh 必须为布尔值")
    if game_name is not None and (not isinstance(game_name, str) or len(game_name) > 200):
        raise ValueError("游戏名称需为不超过 200 字符的文本")
    options = {"appid": aid, "count": count, "language": language, "filter": "recent",
               "review_type": review_type, "purchase_type": "all", "filter_offtopic_activity": 1}
    key = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()
    cache = Path(cache_dir) / f"{key}.json"
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < 3600:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            data["source_metadata"]["cache_hit"] = True
            if game_name and game_name.strip():
                data["game_name"] = game_name.strip()
            return data
        except (ValueError, KeyError, TypeError):
            pass
    get = request_json or fetch_json
    cursor, seen_cursors, seen_ids = "*", set(), set()
    reviews, duplicates, invalid, pages = [], 0, 0, 0
    stopped = "page_limit"
    started = now()
    for _ in range(20):
        params = {k: v for k, v in options.items() if k not in ("appid", "count")}
        params.update(json=1, num_per_page=min(100, count - len(reviews)), cursor=cursor)
        result = get(f"https://store.steampowered.com/appreviews/{aid}?{urlencode(params)}")
        pages += 1
        if not isinstance(result, dict) or result.get("success") != 1 or not isinstance(result.get("reviews"), list):
            raise ValueError("Steam 评论接口返回失败，请检查 App ID 或稍后重试")
        rows = result["reviews"]
        if not rows:
            stopped = "exhausted"
            break
        for row in rows:
            try:
                review = normalize(row, aid)
            except (ValueError, OverflowError, OSError):
                invalid += 1
                continue
            if review["review_id"] in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(review["review_id"])
            reviews.append(review)
            if len(reviews) == count:
                break
        if len(reviews) == count:
            stopped = "requested_count"
            break
        next_cursor = result.get("cursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            stopped = "cursor_stalled"
            break
        seen_cursors.add(cursor)
        cursor = next_cursor
        pause(0.5)
    if not reviews:
        raise ValueError("没有获取到有效评论，请检查游戏、语言或好差评筛选")
    title = (game_name or "").strip() or f"Steam App {aid}"
    description = (f"Steam 公开评论；App ID {aid}；{LANGUAGES[language]}；按创建时间从新到旧；"
                   f"好差评筛选 {review_type}；全部购买来源；排除 Steam 标记的离题评论；"
                   f"请求 {count} 条，获取 {len(reviews)} 条；抓取于 {started}。非随机样本。")
    data = {"game_name": title, "source_description": description, "reviews": reviews,
            "source_metadata": {"provider": "steam", "parameters": options, "started_at": started,
                                "finished_at": now(), "pages": pages, "actual_count": len(reviews),
                                "duplicates_skipped": duplicates, "invalid_skipped": invalid,
                                "stop_reason": stopped, "cache_hit": False,
                                "store_url": f"https://store.steampowered.com/app/{aid}/"}}
    cache.parent.mkdir(parents=True, exist_ok=True)
    temp = cache.with_suffix(f".{uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, cache)
    return data


def steam_batch(data):
    batch = parse_import({"data": data})
    batch["source_metadata"] = data["source_metadata"]
    return batch


def main():
    parser = argparse.ArgumentParser(description="获取 Steam 真实公开评论并保存 JSON")
    parser.add_argument("appid", help="Steam App ID 或商店 URL")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--language", choices=LANGUAGES, default="schinese")
    parser.add_argument("--review-type", choices=["all", "positive", "negative"], default="all")
    parser.add_argument("--game-name")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--import-db", help="同时导入指定 SQLite 数据库")
    args = parser.parse_args()
    try:
        data = fetch_reviews(args.appid, args.count, args.language, args.review_type, args.game_name, args.refresh)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.import_db:
            from .storage import Store
            batch = Store(args.import_db).create(steam_batch(data))
            print(f"Batch: {batch['batch_id']}")
        print(json.dumps({"count": len(data["reviews"]), "output": str(path.resolve()),
                          "metadata": data["source_metadata"]}, ensure_ascii=False))
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()

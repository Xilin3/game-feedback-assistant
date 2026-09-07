"""Input and model-output validation. Evidence must remain traceable."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

CATEGORIES = ["Bug", "性能", "操作体验", "玩法建议", "其他"]
PROMPT_VERSION = "2026-09-08.1"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid(prefix):
    return f"{prefix}-{uuid4().hex[:12]}"


def required(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} 必须是非空文本，且不超过 {limit} 字符")
    return value.strip()


def parse_import(payload):
    if not isinstance(payload, dict):
        raise ValueError("请求必须为 JSON 对象")
    raw = payload.get("data")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 格式错误：第 {exc.lineno} 行") from exc
    if raw is None:
        content = required(payload.get("text"), "评论文本", 1_000_000)
        raw = [{"review_id": f"text-{hashlib.sha256(t.strip().encode()).hexdigest()[:16]}",
                "text": t.strip()} for t in content.splitlines() if t.strip()]
    meta = raw if isinstance(raw, dict) else {}
    reviews = meta.get("reviews") if isinstance(raw, dict) else raw
    if not isinstance(reviews, list) or not 1 <= len(reviews) <= 1000:
        raise ValueError("每批需要 1–1000 条评论")
    game = required(payload.get("game_name") or meta.get("game_name"), "游戏名称")
    source = required(payload.get("source_description") or meta.get("source_description"), "样本来源", 2000)
    seen, result, duplicates = {}, [], 0
    for index, row in enumerate(reviews, 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 条评论必须为对象")
        rid = required(row.get("review_id"), f"第 {index} 条 review_id")
        # Preserve the exact original text, including surrounding whitespace.
        required(row.get("text"), f"第 {index} 条 text", 20000)
        if rid in seen:
            if seen[rid] != row["text"]:
                raise ValueError(f"评论 ID {rid} 重复且原文不同")
            duplicates += 1
            continue
        seen[rid] = row["text"]
        url = row.get("source_url")
        if url is not None and (not isinstance(url, str) or len(url) > 2000 or
                                urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc):
            raise ValueError(f"{rid} 的 source_url 必须是 HTTP(S) 链接或 null")
        date = row.get("created_at")
        if date is not None:
            try:
                datetime.fromisoformat(date.replace("Z", "+00:00"))
            except (ValueError, AttributeError, TypeError) as exc:
                raise ValueError(f"{rid} 的 created_at 必须是 ISO 日期或 null") from exc
        if row.get("recommended") is not None and type(row["recommended"]) is not bool:
            raise ValueError(f"{rid} 的 recommended 必须为布尔值或 null")
        if row.get("language") is not None and (not isinstance(row["language"], str) or len(row["language"]) > 40):
            raise ValueError(f"{rid} 的 language 无效")
        result.append({"review_id": rid, "text": row["text"], "source_url": url,
                       "created_at": date, "language": row.get("language"),
                       "recommended": row.get("recommended"), "status": "pending",
                       "attempts": 0, "error": None})
    fingerprint = hashlib.sha256(json.dumps([game, source, sorted(result, key=lambda r: r["review_id"])], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"batch_id": uid("batch"), "import_fingerprint": fingerprint, "external_batch_id": meta.get("batch_id"),
            "game_name": game, "source_description": source, "imported_at": now(),
            "reviews": result, "issues": [], "groups": [], "audit": [], "runs": [],
            "status": "idle", "duplicates_skipped": duplicates}


def validate_analysis(raw, review, groups):
    if not isinstance(raw, dict) or not isinstance(raw.get("issues"), list) or len(raw["issues"]) > 30:
        raise ValueError("模型必须返回含 issues 数组的对象（最多 30 项）")
    allowed = {g["id"]: g for g in groups if g["state"] == "pending"}
    result = []
    for item in raw["issues"]:
        if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
            raise ValueError("模型返回了无效问题分类")
        summary = required(item.get("summary"), "问题摘要", 500)
        evidence = required(item.get("evidence"), "原文证据", 20000)
        if evidence not in review["text"]:
            raise ValueError("模型引用的证据不在对应评论原文中")
        extra = {}
        for name in ("trigger_context", "attempted_actions", "missing_information"):
            value = item.get(name)
            if not isinstance(value, list) or len(value) > 20 or any(not isinstance(x, str) or not x.strip() or len(x) > 1000 for x in value):
                raise ValueError(f"{name} 必须为文本数组，未知时为空数组")
            if name != "missing_information" and any(x not in review["text"] for x in value):
                raise ValueError(f"{name} 必须引用对应原文，不能新增事实")
            extra[name] = value
        gid = item.get("existing_group_id")
        if gid is not None and (not isinstance(gid, str) or gid not in allowed or allowed[gid]["category"] != item["category"]):
            raise ValueError("模型返回了无效或分类不一致的分组 ID")
        result.append({"id": uid("issue"), "review_id": review["review_id"],
                       "category": item["category"], "summary": summary, "evidence": evidence,
                       "group_id": gid, **extra})
    return result

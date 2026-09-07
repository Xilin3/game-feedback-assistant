"""Local model settings. Secrets are never included in public configuration."""
import json
import os
from urllib.parse import urlsplit


def validate_base(value):
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
        raise ValueError("API 地址无效")
    value = value.rstrip("/")
    try:
        url = urlsplit(value)
        _ = url.port
        valid = url.hostname and (url.scheme == "https" or
                                  (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")))
        if not valid or url.username or url.password or url.query or url.fragment:
            raise ValueError()
    except ValueError:
        raise ValueError("请输入 HTTPS API 基址（本机服务可用 HTTP），不要包含密钥、查询参数或片段") from None
    if url.path.endswith("/chat/completions"):
        raise ValueError("请填写 API 基址，例如 https://服务地址/v1，不要包含 /chat/completions")
    return value


class ModelSettings:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def effective(self):
        with self.store.connect() as db:
            row = db.execute("SELECT body FROM settings WHERE name='model'").fetchone()
        if row:
            return json.loads(row[0])
        return {"api_base": os.getenv("FEEDBACK_API_BASE", "").strip().rstrip("/"),
                "api_key": os.getenv("FEEDBACK_API_KEY", "").strip(),
                "model": os.getenv("FEEDBACK_MODEL", "").strip(), "source": "environment"}

    def public(self):
        config = self.effective()
        configured = all(config.get(k) for k in ("api_base", "api_key", "model"))
        return {"api_base": config["api_base"], "model": config["model"],
                "has_api_key": bool(config["api_key"]), "configured": configured,
                "source": config["source"],
                "default_provider": "compatible" if config["source"] == "local" and configured else os.getenv("FEEDBACK_PROVIDER", "demo")}

    def save(self, data):
        base = validate_base(data.get("api_base"))
        model, key = data.get("model"), data.get("api_key", "")
        if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(c) < 32 for c in model):
            raise ValueError("模型名称需为 1–200 字符的文本")
        if not isinstance(key, str) or len(key) > 8192 or any(c.isspace() for c in key):
            raise ValueError("API 密钥无效，不能包含空白字符")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM settings WHERE name='model'").fetchone()
            old = json.loads(row[0]) if row else self.effective()
            if not key:
                if base != old["api_base"]:
                    raise ValueError("更换 API 地址时请同时输入该服务的密钥")
                key = old["api_key"]
            if not key:
                raise ValueError("请输入 API 密钥")
            config = {"api_base": base, "api_key": key, "model": model.strip(), "source": "local"}
            db.execute("INSERT OR REPLACE INTO settings VALUES ('model', ?)", (json.dumps(config),))
        return self.public()

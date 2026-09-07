import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, render_template, request, Response, session
from werkzeug.exceptions import HTTPException

from .domain import parse_import
from .providers import get_provider
from .report import render_report
from .service import (analyze_batch, merge_groups, prepare_run, recover, split_group,
                      update_group, view_batch)
from .storage import Store
from .steam import fetch_reviews, steam_batch
from .settings import ModelSettings


def create_app(database=None, testing=False):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=4 * 1024 * 1024,
                      TESTING=testing, SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True)
    store = Store(database or os.getenv("FEEDBACK_DATABASE", "data/feedback.sqlite3"))
    recover(store)
    settings = ModelSettings(store)
    executor = ThreadPoolExecutor(max_workers=2)
    app.extensions.update(store=store, executor=executor)

    @app.before_request
    def csrf():
        if request.method in ("POST", "PATCH", "DELETE"):
            token = session.get("csrf")
            if not token or not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), token):
                return jsonify(error="页面会话已过期，请刷新后重试"), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(LookupError)
    def missing(exc):
        return jsonify(error=str(exc)), 404

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error="上传内容过大（上限 4 MB）" if exc.code == 413 else exc.description), exc.code

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError("请求必须为 JSON 对象")
        return value

    @app.get("/")
    def index():
        session.setdefault("csrf", secrets.token_hex(32))
        return render_template("index.html", csrf=session["csrf"])

    @app.get("/api/config")
    def config():
        return jsonify(settings.public())

    @app.post("/api/config")
    def save_config():
        return jsonify(settings.save(body()))

    @app.get("/api/batches")
    def batches():
        return jsonify([{k: b[k] for k in ("batch_id", "game_name", "source_description", "imported_at", "status")}
                        | {"count": len(b["reviews"])} for b in store.list()])

    @app.post("/api/batches")
    def create_batch():
        data = body()
        if type(data.get("force_new", False)) is not bool:
            raise ValueError("force_new 必须为布尔值")
        batch = parse_import(data)
        batch = store.create(batch, force_new=data.get("force_new", False))
        return jsonify(view_batch(batch)), 201

    @app.get("/api/batches/<bid>")
    def batch(bid):
        return jsonify(view_batch(store.get(bid)))

    @app.post("/api/steam/import")
    def import_steam():
        data = body()
        source = fetch_reviews(data.get("appid"), data.get("count", 300),
                               data.get("language", "schinese"), data.get("review_type", "all"),
                               data.get("game_name"), data.get("refresh", False))
        batch = store.create(steam_batch(source))
        return jsonify(view_batch(batch)), 201

    @app.get("/api/batches/<bid>/source")
    def source_export(bid):
        import json
        b = store.get(bid)
        fields = ("review_id", "text", "source_url", "created_at", "language", "recommended")
        data = {"game_name": b["game_name"], "source_description": b["source_description"],
                "source_metadata": b.get("source_metadata"),
                "reviews": [{k: r[k] for k in fields} for r in b["reviews"]]}
        return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{bid}-source.json"'})

    @app.post("/api/batches/<bid>/analyze")
    def analyze(bid):
        provider = get_provider(body().get("provider", settings.public()["default_provider"]), settings.effective())
        store.mutate(bid, lambda b: prepare_run(b, provider))
        executor.submit(analyze_batch, store, bid, provider)
        return jsonify(status="running"), 202

    @app.patch("/api/batches/<bid>/groups/<gid>")
    def edit(bid, gid):
        data = body()
        return jsonify(view_batch(store.mutate(bid, lambda b: update_group(b, gid, data))))

    @app.post("/api/batches/<bid>/merge")
    def merge(bid):
        data = body()
        return jsonify(view_batch(store.mutate(bid, lambda b: merge_groups(b, data.get("group_ids")))))

    @app.post("/api/batches/<bid>/groups/<gid>/split")
    def split(bid, gid):
        data = body()
        return jsonify(view_batch(store.mutate(bid, lambda b: split_group(b, gid, data))))

    @app.get("/api/batches/<bid>/report")
    def report(bid):
        return Response(render_report(store.get(bid)), mimetype="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{bid}-report.md"'})

    @app.get("/api/example")
    def example():
        return Response((Path(__file__).parent.parent / "examples/feedback.synthetic.json").read_text(encoding="utf-8"), mimetype="application/json")

    return app


def main():
    app = create_app()
    app.run(host=os.getenv("FEEDBACK_HOST", "127.0.0.1"), port=int(os.getenv("FEEDBACK_PORT", "5000")), debug=False)


if __name__ == "__main__":
    main()

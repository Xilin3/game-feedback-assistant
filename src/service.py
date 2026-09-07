import time
from .domain import CATEGORIES, now, required, uid, validate_analysis


def audit(batch, action, detail):
    batch["audit"].append({"at": now(), "action": action, "detail": detail})


def recover(store):
    for batch in store.list():
        if batch["status"] == "running":
            def reset(b):
                b["status"] = "idle"
                for r in b["reviews"]:
                    if r["status"] == "running":
                        r["status"], r["error"] = "failed", "服务中断，请重试"
                for run in b["runs"]:
                    if not run.get("finished_at"):
                        run["finished_at"], run["interrupted"] = now(), True
            store.mutate(batch["batch_id"], reset)


def prepare_run(batch, provider):
    if batch["status"] == "running":
        raise ValueError("本批次正在分析，请等待完成")
    if not any(r["status"] in ("pending", "failed") for r in batch["reviews"]):
        raise ValueError("没有需要分析或重试的评论")
    batch["status"] = "running"
    batch["runs"].append({"id": uid("run"), "provider": provider.name, "model": provider.model,
                          "started_at": now(), "finished_at": None})


def analyze_batch(store, bid, provider):
    started = time.monotonic()
    try:
        targets = [r["review_id"] for r in store.get(bid)["reviews"] if r["status"] in ("pending", "failed")]
        for rid in targets:
            def begin(b):
                r = next(r for r in b["reviews"] if r["review_id"] == rid)
                r["status"], r["error"] = "running", None
                r["attempts"] += 1
                r.setdefault("history", []).append({"at": now(), "provider": provider.name, "model": provider.model})
            current = store.mutate(bid, begin)
            review = next(r for r in current["reviews"] if r["review_id"] == rid)
            metrics = None
            try:
                raw, metrics = provider.analyze(review, current["groups"])
                issues = validate_analysis(raw, review, current["groups"])

                def success(b):
                    for issue in issues:
                        if issue["group_id"] is None:
                            # Exact duplicate summaries can share a new group within a single response.
                            group = next((g for g in b["groups"] if g["title"] == issue["summary"] and
                                          g["category"] == issue["category"] and g["state"] == "pending"), None)
                            if group is None:
                                group = {"id": uid("group"), "title": issue["summary"], "category": issue["category"],
                                         "state": "pending", "notes": "", "missing_information": []}
                                b["groups"].append(group)
                            issue["group_id"] = group["id"]
                        b["issues"].append(issue)
                    r = next(r for r in b["reviews"] if r["review_id"] == rid)
                    r["status"], r["analysis"] = "done", metrics
                    r["history"][-1].update({"status": "done", **metrics})
                store.mutate(bid, success)
            except Exception as exc:
                message = str(exc) if isinstance(exc, ValueError) else "分析失败，请检查服务配置后重试"
                def failure(b):
                    r = next(r for r in b["reviews"] if r["review_id"] == rid)
                    r["status"], r["error"] = "failed", message
                    r["history"][-1].update({"status": "failed", "error": message, **(metrics or {})})
                store.mutate(bid, failure)
    finally:
        def finish(b):
            b["status"] = "idle"
            b["runs"][-1].update({"finished_at": now(), "elapsed_seconds": round(time.monotonic() - started, 3)})
        store.mutate(bid, finish)


def editable(batch):
    if batch["status"] == "running":
        raise ValueError("分析进行中，完成后可人工修正")


def get_group(batch, gid):
    group = next((g for g in batch["groups"] if g["id"] == gid), None)
    if not group:
        raise LookupError("分组不存在")
    return group


def update_group(batch, gid, data):
    editable(batch)
    g = get_group(batch, gid)
    before = dict(g)
    if "title" in data:
        g["title"] = required(data["title"], "问题标题", 500)
    if "category" in data:
        if data["category"] not in CATEGORIES:
            raise ValueError("无效分类")
        g["category"] = data["category"]
        for issue in batch["issues"]:
            if issue["group_id"] == gid:
                issue["category"] = data["category"]
    if "state" in data:
        if data["state"] not in ("pending", "confirmed", "insufficient", "excluded"):
            raise ValueError("无效复核状态")
        g["state"] = data["state"]
    if "notes" in data:
        if not isinstance(data["notes"], str) or len(data["notes"]) > 5000:
            raise ValueError("备注需为不超过 5000 字符的文本")
        g["notes"] = data["notes"]
    audit(batch, "修改分组", {"before": before, "after": dict(g)})


def merge_groups(batch, ids):
    editable(batch)
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(set(ids)) < 2:
        raise ValueError("请选择至少两个不同分组")
    groups = [get_group(batch, gid) for gid in dict.fromkeys(ids)]
    target = groups[0]
    snapshot = [dict(g) for g in groups]
    target["notes"] = "\n".join(g["notes"] for g in groups if g["notes"])
    target["state"] = "pending"
    for issue in batch["issues"]:
        if issue["group_id"] in ids:
            issue["group_id"], issue["category"] = target["id"], target["category"]
    batch["groups"] = [g for g in batch["groups"] if g["id"] not in ids or g["id"] == target["id"]]
    audit(batch, "合并分组", {"before": snapshot, "target": target["id"], "category": target["category"]})


def split_group(batch, gid, data):
    editable(batch)
    source = get_group(batch, gid)
    ids = data.get("issue_ids")
    members = [i for i in batch["issues"] if i["group_id"] == gid]
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or not ids or not set(ids) < {i["id"] for i in members}:
        raise ValueError("请选择部分问题拆分，原分组至少保留一项")
    title = required(data.get("title"), "新分组标题", 500)
    new = {**source, "id": uid("group"), "title": title, "state": "pending"}
    source["state"] = "pending"
    batch["groups"].append(new)
    for issue in members:
        if issue["id"] in ids:
            issue["group_id"] = new["id"]
    audit(batch, "拆分分组", {"source": gid, "target": new["id"], "issue_ids": ids})


def view_batch(batch):
    # Computed statistics are never taken from model output.
    for g in batch["groups"]:
        members = [i for i in batch["issues"] if i["group_id"] == g["id"]]
        g["review_count"] = len({i["review_id"] for i in members})
        g["issue_count"] = len(members)
        g["missing_information"] = sorted({s for i in members for s in i["missing_information"]})
    batch["stats"] = {state: sum(r["status"] == state for r in batch["reviews"])
                      for state in ("pending", "running", "done", "failed")}
    batch["stats"].update({"total": len(batch["reviews"]),
                            "groups": sum(g["state"] != "excluded" for g in batch["groups"]),
                            "confirmed": sum(g["state"] == "confirmed" for g in batch["groups"]),
                            "requests": sum(r["attempts"] for r in batch["reviews"])})
    return batch

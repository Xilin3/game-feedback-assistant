import html
import re
from urllib.parse import quote
from .domain import now
from .service import view_batch

STATES = {"pending": "待复核", "confirmed": "已确认", "insufficient": "信息不足", "excluded": "已排除"}


def safe(value):
    value = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value).replace("\r", "").replace("\n", " ")


def render_report(batch):
    b = view_batch(batch)
    s = b["stats"]
    dates = sorted(r["created_at"][:10] for r in b["reviews"] if r["created_at"])
    lines = [f"# {safe(b['game_name'])} · 玩家反馈报告", "", f"- 样本来源：{safe(b['source_description'])}",
             f"- 导入时间：{b['imported_at']}", f"- 导出时间：{now()}",
             f"- 评论时间范围：{dates[0] + ' 至 ' + dates[-1] if dates else '未知'}（仅含有日期记录）",
             f"- 有效评论：{s['total']}；分析完成：{s['done']}；失败：{s['failed']}；待处理：{s['pending'] + s['running']}",
             "", "> 数量和占比只描述本次导入样本，不代表所有玩家。一条评论可包含多个问题，各组数量之和可能超过评论总数。",
             "> 已排除的问题不列入下表；已确认仅表示人工复核状态，不等同于技术根因已证实。", ""]
    providers = sorted({r.get("analysis", {}).get("provider", "") for r in b["reviews"]} - {""})
    if "demo" in providers:
        lines += ["> 本报告包含离线规则演示结果，不能用于评估大模型质量或真实玩家偏好。", ""]
    groups = sorted((g for g in b["groups"] if g["state"] != "excluded"), key=lambda g: -g["review_count"])
    lines += ["## 问题清单", "", "| 问题 | 分类 | 评论数 | 样本占比 | 复核状态 |", "| --- | --- | ---: | ---: | --- |"]
    for g in groups:
        lines.append(f"| {safe(g['title'])} | {g['category']} | {g['review_count']} | {g['review_count']/s['total']:.1%} | {STATES[g['state']]} |")
    if not groups:
        lines += ["", "暂无问题分组；请结合分析完成数量判断，不能据此认定没有问题。"]
    reviews = {r["review_id"]: r for r in b["reviews"]}
    for g in groups:
        lines += ["", f"## {safe(g['title'])}", "", f"- 分类：{g['category']}；状态：{STATES[g['state']]}"]
        for issue in (i for i in b["issues"] if i["group_id"] == g["id"]):
            r = reviews[issue["review_id"]]
            link = f" · [来源]({quote(r['source_url'], safe=':/?=&%')})" if r["source_url"] else ""
            lines += [f"- 评论 `{safe(r['review_id'])}`{link}：{safe(issue['evidence'])}"]
            for key, label in (("trigger_context", "触发条件"), ("attempted_actions", "已尝试操作")):
                if issue[key]:
                    lines += [f"  - {label}：{safe('；'.join(issue[key]))}"]
        lines += [f"- 待核实：{safe('；'.join(g['missing_information'])) if g['missing_information'] else '未列出，仍需人工判断'}",
                  f"- 人工备注：{safe(g['notes']) or '无'}",
                  f"- 建议跟进：{'进一步收集信息' if g['state'] == 'insufficient' else '技术排查' if g['category'] in ('Bug', '性能') else '产品与玩法评估'}"]
    lines += ["", "## 运行记录与限制", ""]
    for run in b["runs"]:
        lines += [f"- {safe(run['provider'])} / {safe(run['model'])}：{run['started_at']}，耗时 {run.get('elapsed_seconds', '未记录')} 秒"]
    histories = [h for r in b["reviews"] for h in r.get("history", [])]
    versions = sorted({h["prompt_version"] for h in histories if h.get("prompt_version")})
    lines += [f"- 提示词版本：{', '.join(versions) or '未记录'}"]
    known = [h["estimated_cost"] for h in histories if h.get("estimated_cost") is not None]
    lines += [f"- 请求尝试：{s['requests']}；重试：{sum(max(0,r['attempts']-1) for r in b['reviews'])}",
              f"- 已记录 token：输入 {sum(h.get('input_tokens') or 0 for h in histories)} / 输出 {sum(h.get('output_tokens') or 0 for h in histories)}（缺失用量未计入）",
              f"- 已知估算成本：{sum(known):.6f}（按配置单价；{len(histories)-len(known)} 次请求成本未知；实际账单以服务商为准）",
              "- 结构和引用校验不保证语义准确，可能遗漏、误提取或误归组。没有开展独立人工质量评估。"]
    for r in b["reviews"]:
        if r["status"] == "failed":
            lines += [f"- 失败评论 {safe(r['review_id'])}：{safe(r['error'])}"]
    lines += ["", "## 人工修正记录", ""]
    for event in b["audit"]:
        lines += [f"- {event['at']} · {safe(event['action'])}"]
    if not b["audit"]:
        lines.append("尚无人工修正记录。")
    return "\n".join(lines) + "\n"

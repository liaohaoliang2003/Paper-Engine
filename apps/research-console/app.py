from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from services import (
    DEFAULT_REC5_URL,
    ExternalDependencyUnavailable,
    PaperCandidate,
    ReadingService,
    RecommenderService,
    choose_filename,
    default_aminer_skill_dir,
    default_paper_skill_dir,
    download_pdf,
    filter_candidates,
    parse_topics,
    persist_env_var,
    read_env_var,
    resolve_pdf_url,
    select_top_k,
    inspect_external_dependencies,
    today_output_dir,
    write_today_recommendations,
)
from task_engine import TaskEngine


st.set_page_config(
    page_title="Research Console",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ACTION_LABELS = {
    "recommend": "获取推荐",
    "download": "下载入库",
    "upload": "本地上传",
    "read": "生成报告",
    "validate": "自动校验",
    "system": "系统事件",
}
DEFAULT_TOPICS = (
    "AI for Research, autonomous research agents, literature review automation, "
    "hypothesis generation, experiment planning, tool-use for scientific workflows"
)
MODEL_PRESETS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "o4-mini",
]
LEGACY_AMINER_REC5_URL = "https://api.aminer.cn/api/v2/open/ai/rec5"
PERSIST_CONFIG_KEYS = [
    "topics",
    "top_k",
    "year_range",
    "focus_questions",
    "integration_mode",
    "aminer_skill_dir",
    "paper_skill_dir",
    "aminer_rec5_url",
    "output_dir",
    "aminer_api_key",
    "llm_base_url",
    "llm_api_key",
    "llm_model",
]


def _normalize_integration_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in {"builtin", "external"}:
        return "builtin"
    return mode


st.markdown(
    """
<style>
:root {
  --bg:#eef2f6; --panel:#fff; --line:#d9e1ec; --muted:#58677d; --ink:#0f1a2b;
  --soft:#f7f9fc; --accent:#0a7a66;
}
html, body, [class*="css"] {
  font-family: "Avenir Next", "PingFang SC", "Noto Sans SC", "Segoe UI", sans-serif;
  color: var(--ink);
}
.stApp {
  background:
    radial-gradient(1050px 460px at 5% -20%, #dfe8f1 0, transparent 44%),
    radial-gradient(900px 420px at 94% -20%, #e6efea 0, transparent 42%),
    var(--bg);
}
.rc-top { border:1px solid var(--line); border-radius:14px; padding:10px 14px; margin-bottom:14px;
  background:linear-gradient(96deg, rgba(15,76,129,.08), rgba(10,122,102,.07) 58%, rgba(255,255,255,.94));}
.chip { display:inline-flex; border:1px solid var(--line); border-radius:999px; padding:3px 10px; margin:4px 6px 0 0; font-size:12px; color:var(--muted); background:var(--soft);}
.hero-wrap { margin: 0 0 8px 0; }
.hero-title { margin: 0; font-size: 34px; line-height: 1.15; font-weight: 760; letter-spacing: .2px; color: var(--ink); }
.title { margin:0 0 8px 0; font-size:17px; font-weight:700;}
.muted { color:var(--muted); font-size:13px; line-height:1.45;}
.log { border:1px solid var(--line); border-radius:10px; background:#111827; color:#c9d4e4; padding:8px;
  max-height:350px; overflow:auto; font-size:12px; line-height:1.45; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.path { border-bottom:1px dashed var(--line); }
.workbench-grid { display:grid; grid-template-columns:1fr; gap:12px; }
.workbench-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
.section-tag { font-size:11px; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:2px 8px; }
.tab-note { margin: 0 0 10px 0; color: var(--muted); font-size: 13px; }
.kv-line { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px; }
.kv-label { color: var(--muted); font-size: 14px; }
.kv-chip { display:inline-flex; align-items:center; border-radius:999px; padding:2px 10px; font-size:12px; border:1px solid var(--line); }
.kv-chip.ok { background:#edf7f3; color:#0f6b4a; border-color:#b8dbc9; }
.kv-chip.warn { background:#fff6f4; color:#9b3f30; border-color:#efc9c0; }
.st-key-adv_fab_zone { position: sticky; top: 8px; z-index: 20; margin-bottom: 8px; }
.st-key-adv_fab_zone .stButton { text-align: right; }
.st-key-adv_fab_zone .stButton > button {
  width: auto;
  min-height: 32px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: #f7f9fc;
  color: var(--muted);
  padding: 0.2rem 0.7rem;
  font-size: 12px;
  font-weight: 600;
}
.st-key-adv_fab_zone .stButton > button:hover { background:#eef3f8; color:#44546b; }
@media (max-width: 1024px) { .workbench-grid { gap:10px; } }
</style>
""",
    unsafe_allow_html=True,
)


def _env(name: str, fallback: str = "") -> str:
    value = read_env_var(name)
    return value if value else fallback


def _config_file_path() -> Path:
    return Path.home() / ".paper-engine" / "research-console-config.json"


def _default_config_values() -> dict[str, Any]:
    return {
        "topics": DEFAULT_TOPICS,
        "top_k": 3,
        "year_range": (2025, 2026),
        "focus_questions": "",
        "integration_mode": _normalize_integration_mode(_env("PDR_INTEGRATION_MODE", "builtin")),
        "aminer_skill_dir": str(default_aminer_skill_dir()),
        "paper_skill_dir": str(default_paper_skill_dir()),
        "aminer_rec5_url": _env("AMINER_REC5_URL", DEFAULT_REC5_URL),
        "output_dir": str(today_output_dir()),
        "aminer_api_key": _env("AMINER_API_KEY"),
        "llm_base_url": _env("PDR_LLM_BASE_URL", "https://api.openai.com/v1"),
        "llm_api_key": _env("PDR_LLM_API_KEY"),
        "llm_model": _env("PDR_LLM_MODEL", "gpt-4.1"),
    }


def _load_persisted_config() -> dict[str, Any]:
    path = _config_file_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    config: dict[str, Any] = {}
    for key in PERSIST_CONFIG_KEYS:
        if key in payload:
            config[key] = payload[key]
    return config


def _save_persisted_config(config: dict[str, Any]) -> tuple[bool, str]:
    path = _config_file_path()
    payload = {key: config.get(key) for key in PERSIST_CONFIG_KEYS}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, f"本地配置已保存：{path}"
    except Exception as exc:
        return False, f"保存本地配置失败：{exc}"


def _apply_config_to_session(config: dict[str, Any]) -> None:
    st.session_state.topics = str(config.get("topics", DEFAULT_TOPICS))
    try:
        st.session_state.top_k = int(config.get("top_k", 3))
    except Exception:
        st.session_state.top_k = 3

    year_raw = config.get("year_range", (2025, 2026))
    if isinstance(year_raw, (list, tuple)) and len(year_raw) >= 2:
        try:
            y0 = int(year_raw[0])
            y1 = int(year_raw[1])
            st.session_state.year_range = (y0, y1)
        except Exception:
            st.session_state.year_range = (2025, 2026)
    else:
        st.session_state.year_range = (2025, 2026)

    st.session_state.focus_questions = str(config.get("focus_questions", ""))
    st.session_state.integration_mode = _normalize_integration_mode(config.get("integration_mode", "builtin"))
    st.session_state.aminer_skill_dir = str(config.get("aminer_skill_dir", str(default_aminer_skill_dir())))
    st.session_state.paper_skill_dir = str(config.get("paper_skill_dir", str(default_paper_skill_dir())))
    st.session_state.aminer_rec5_url = str(config.get("aminer_rec5_url", DEFAULT_REC5_URL))
    st.session_state.output_dir = str(config.get("output_dir", str(today_output_dir())))
    st.session_state.aminer_api_key = str(config.get("aminer_api_key", ""))
    st.session_state.llm_base_url = str(config.get("llm_base_url", "https://api.openai.com/v1"))
    st.session_state.llm_api_key = str(config.get("llm_api_key", ""))
    st.session_state.llm_model = str(config.get("llm_model", "gpt-4.1"))


def _init_config_state() -> None:
    if st.session_state.get("config_initialized", False):
        return
    merged = _default_config_values()
    persisted = _load_persisted_config()
    merged.update({k: v for k, v in persisted.items() if v is not None})
    status_messages: list[str] = []
    notices: list[str] = []

    merged["integration_mode"] = _normalize_integration_mode(merged.get("integration_mode", "builtin"))
    if merged["integration_mode"] == "external":
        ok, reason = inspect_external_dependencies(
            Path(str(merged.get("aminer_skill_dir") or default_aminer_skill_dir())),
            Path(str(merged.get("paper_skill_dir") or default_paper_skill_dir())),
        )
        if not ok:
            merged["integration_mode"] = "builtin"
            status_messages.append(f"检测到 external 依赖不可用，已自动迁移到 builtin：{reason}")
            notices.append(f"已从 external 自动回退到 builtin（原因：{reason}）")

    rec5_url = str(merged.get("aminer_rec5_url") or "").strip()
    if rec5_url == LEGACY_AMINER_REC5_URL:
        merged["aminer_rec5_url"] = DEFAULT_REC5_URL
        migration_msg = f"已将 AMiner 推荐地址从旧版 v2 自动迁移到新版 endpoint：{DEFAULT_REC5_URL}"
        status_messages.append(migration_msg)
        notices.append(migration_msg)

    if status_messages:
        merged["persist_save_status"] = "；".join(status_messages)
    if notices:
        merged["integration_notice"] = "；".join(notices)
    _apply_config_to_session(merged)
    st.session_state.config_initialized = True
    if "persist_save_status" not in st.session_state:
        st.session_state.persist_save_status = ""
    if merged.get("persist_save_status"):
        st.session_state.persist_save_status = str(merged["persist_save_status"])
        _save_persisted_config(_config_snapshot())
    if "integration_notice" not in st.session_state:
        st.session_state.integration_notice = ""
    if merged.get("integration_notice"):
        st.session_state.integration_notice = str(merged["integration_notice"])


def _set_integration_notice(message: str) -> None:
    st.session_state.integration_notice = message
    if message:
        _action_log("system", message)


def _fallback_to_builtin(config: dict[str, Any], *, reason: str) -> None:
    notice = f"已从 external 自动回退到 builtin（原因：{reason}）"
    st.session_state.integration_mode = "builtin"
    os.environ["PDR_INTEGRATION_MODE"] = "builtin"
    config["integration_mode"] = "builtin"
    ok, msg = _save_persisted_config(_config_snapshot())
    if ok:
        st.session_state.persist_save_status = msg
    _set_integration_notice(notice)


def _truncate_middle(text: str, limit: int = 66) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    keep = max(8, (limit - 3) // 2)
    return f"{raw[:keep]}...{raw[-keep:]}"


def _candidate_from_row(row: dict[str, Any]) -> PaperCandidate:
    return PaperCandidate(**row)


def _action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)


def _action_log(action: str, message: str) -> None:
    engine: TaskEngine = st.session_state.engine
    engine.log(f"[action:{action}] {message}")


def _logs_by_action(action: str, limit: int = 200) -> list[str]:
    logs = st.session_state.engine.logs
    if action == "all":
        return logs[-limit:] if limit > 0 else logs
    token = f"[action:{action}]"
    rows = [line for line in logs if token in line]
    if limit <= 0:
        return rows
    return rows[-limit:]


def _append_action_error(action: str, message: str) -> None:
    bucket = st.session_state.action_errors
    if action not in bucket:
        bucket[action] = []
    bucket[action].append(message)


def _clear_action_error(action: str) -> None:
    st.session_state.action_errors[action] = []


def _append_artifact(path: Path, kind: str, uid: str = "") -> None:
    resolved = str(path.resolve())
    for item in st.session_state.artifacts:
        if item["path"] == resolved and item["kind"] == kind and item.get("uid", "") == uid:
            return
    st.session_state.artifacts.append({"path": resolved, "kind": kind, "uid": uid})


def _paper_title(uid: str) -> str:
    for row in st.session_state.recommend_rows:
        if row.get("uid") == uid:
            return row.get("title") or uid
    path = Path(str(uid))
    if path.suffix.lower() == ".pdf":
        return path.stem
    return uid


def _refresh_recommend_selected() -> None:
    rows = st.session_state.recommend_rows
    selected = []
    for row in rows:
        uid = row["uid"]
        if st.session_state.get(f"rec_pick_{uid}", False):
            selected.append(uid)
    st.session_state.recommend_selected_uids = selected


def _set_recommend_selected_uids(uids: list[str], sync_pick_keys: bool = True) -> None:
    rows = st.session_state.recommend_rows
    st.session_state.recommend_selected_uids = list(uids)
    if not sync_pick_keys:
        return
    selected = set(uids)
    for row in rows:
        uid = row["uid"]
        st.session_state[f"rec_pick_{uid}"] = uid in selected


def _queue_recommend_pick_sync(uids: list[str]) -> None:
    st.session_state.pending_recommend_pick_uids = list(uids)


def _apply_pending_recommend_pick_sync(rows: list[dict[str, Any]]) -> None:
    pending = st.session_state.get("pending_recommend_pick_uids")
    if pending is None:
        return
    selected = set(pending)
    for row in rows:
        uid = row["uid"]
        st.session_state[f"rec_pick_{uid}"] = uid in selected
    st.session_state.pending_recommend_pick_uids = None


def _selected_recommend_candidates() -> list[PaperCandidate]:
    selected = set(st.session_state.recommend_selected_uids)
    chosen: list[PaperCandidate] = []
    for row in st.session_state.recommend_rows:
        if row["uid"] in selected:
            chosen.append(_candidate_from_row(row))
    return chosen


def _run_action(action: str, task_name: str, fn) -> bool:
    engine: TaskEngine = st.session_state.engine
    _clear_action_error(action)
    task_id = engine.queue(task_name, metadata={"action": action})
    engine.start(task_id)
    _action_log(action, f"开始: {task_name}")
    try:
        fn(engine)
    except Exception as exc:
        detail = str(exc)
        engine.fail(task_id, detail)
        _append_action_error(action, detail)
        _action_log(action, f"失败: {detail}")
        st.error(f"{_action_label(action)}失败：{detail}")
        return False
    engine.success(task_id)
    _action_log(action, "完成")
    return True


def _recommend(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        topics = parse_topics(config["topics"])
        if not topics:
            raise RuntimeError("请至少填写一个主题")
        def _run(mode: str) -> list[PaperCandidate]:
            service = RecommenderService(
                Path(config["aminer_skill_dir"]),
                mode=mode,
                aminer_api_key=config.get("aminer_api_key", ""),
                aminer_rec5_url=config.get("aminer_rec5_url", ""),
            )
            return service.recommend(topics)

        mode = _normalize_integration_mode(config.get("integration_mode", "builtin"))
        try:
            raw = _run(mode)
        except ExternalDependencyUnavailable as exc:
            if mode != "external":
                raise
            _fallback_to_builtin(config, reason=str(exc))
            raw = _run("builtin")
        filtered = filter_candidates(raw, config["year_range"][0], config["year_range"][1])
        st.session_state.candidates = [x.to_dict() for x in raw]
        st.session_state.recommend_rows = [x.to_dict() for x in filtered]
        st.session_state.recommend_selected_uids = []
        st.session_state.download_queue_uids = []
        st.session_state.resolved_urls = {}
        st.session_state.pending_recommend_pick_uids = None
        for row in st.session_state.recommend_rows:
            st.session_state[f"rec_pick_{row['uid']}"] = False
        _action_log("recommend", f"候选: raw={len(raw)} filtered={len(filtered)}")
        if not filtered:
            raise RuntimeError("推荐为空，请调整主题或年份范围")

    return _run_action("recommend", "获取推荐", _impl)


def _scan_kb_pdfs(output_dir: Path) -> list[dict[str, Any]]:
    if not output_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for pdf in output_dir.rglob("*.pdf"):
        try:
            stat = pdf.stat()
        except Exception:
            continue
        rows.append(
            {
                "uid": str(pdf.resolve()),
                "name": pdf.name,
                "path": str(pdf.resolve()),
                "size_kb": round(stat.st_size / 1024.0, 1),
                "mtime": stat.st_mtime,
            }
        )
    rows.sort(key=lambda x: x["mtime"], reverse=True)
    return rows


def _refresh_kb_files(config: dict[str, Any]) -> None:
    kb_root = Path(config["output_dir"])
    try:
        kb_root.mkdir(parents=True, exist_ok=True)
        st.session_state.kb_files = _scan_kb_pdfs(kb_root)
    except Exception as exc:
        st.session_state.kb_files = []
        _append_action_error("system", f"知识库目录不可用: {exc}")
        _action_log("system", f"知识库目录不可用: {exc}")


def _resolve_row_by_uid(uid: str) -> dict[str, Any] | None:
    for row in st.session_state.recommend_rows:
        if row.get("uid") == uid:
            return row
    return None


def _download_recommend_one(uid: str, rank: int, config: dict[str, Any]) -> tuple[bool, str]:
    row = _resolve_row_by_uid(uid)
    if row is None:
        return False, "候选条目不存在"
    target = _candidate_from_row(row)

    mapping = st.session_state.resolved_urls
    url = mapping.get(uid, "")
    if not url:
        resolved, source = resolve_pdf_url(target)
        if resolved:
            mapping[uid] = resolved
            url = resolved
            _action_log("download", f"解析URL成功: {target.title} <- {source}")
    if not url:
        return False, "未解析到可用 PDF"

    out_dir = Path(config["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / choose_filename(rank, target.title)
    download_pdf(url, pdf_path)
    st.session_state.downloaded[str(uid)] = str(pdf_path.resolve())
    st.session_state.active_paper_uid = str(pdf_path.resolve())
    _append_artifact(pdf_path, "pdf", uid)
    return True, str(pdf_path)


def _add_selection_to_download_queue(config: dict[str, Any]) -> bool:
    rows = st.session_state.recommend_rows
    _refresh_recommend_selected()
    selected = list(st.session_state.recommend_selected_uids)
    if not selected:
        fallback = select_top_k([_candidate_from_row(row) for row in rows], int(config["top_k"]))
        selected = [x.uid for x in fallback]
    if not selected:
        st.warning("没有可加入下载队列的候选")
        return False
    selected_set = set(selected)
    ordered = [row["uid"] for row in rows if row["uid"] in selected_set]
    ordered = ordered[: int(config["top_k"])]
    st.session_state.download_queue_uids = ordered
    _set_recommend_selected_uids(ordered, sync_pick_keys=False)
    _queue_recommend_pick_sync(ordered)
    _action_log("download", f"下载队列已更新: {len(ordered)} 项")
    return True


def _download_from_queue(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        queue = list(st.session_state.download_queue_uids)
        if not queue:
            raise RuntimeError("下载队列为空，请先在推荐工作台选择候选")
        chosen = select_top_k(
            [_candidate_from_row(_resolve_row_by_uid(uid)) for uid in queue if _resolve_row_by_uid(uid)],
            int(config["top_k"]),
        )
        if not chosen:
            raise RuntimeError("下载队列中没有可用候选")
        success = 0
        for idx, item in enumerate(chosen, start=1):
            ok, detail = _download_recommend_one(item.uid, idx, config)
            if ok:
                success += 1
                _action_log("download", f"下载成功: {item.title}")
            else:
                _append_action_error("download", f"{item.title}: {detail}")
                _action_log("download", f"下载失败: {item.title} | {detail}")
        write_today_recommendations(Path(config["output_dir"]), chosen, st.session_state.resolved_urls)
        _append_artifact(Path(config["output_dir"]) / "today_recommendations.md", "recommendation")
        _append_artifact(Path(config["output_dir"]) / "today_recommendations.json", "recommendation")
        _refresh_kb_files(config)
        if success == 0:
            raise RuntimeError("没有任何论文下载成功")

    return _run_action("download", "下载推荐 PDF", _impl)


def _filename_from_url(url: str, idx: int) -> str:
    clean = str(url or "").strip().split("?")[0].split("#")[0]
    name = Path(clean).name or f"manual_{idx:02d}.pdf"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    return safe


def _download_manual_urls(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        lines = [line.strip() for line in str(st.session_state.kb_manual_urls or "").splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("请至少输入一个 PDF URL")
        out_dir = Path(config["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        success = 0
        for idx, url in enumerate(lines, start=1):
            try:
                path = out_dir / _filename_from_url(url, idx)
                download_pdf(url, path)
                success += 1
                st.session_state.downloaded[str(path.resolve())] = str(path.resolve())
                st.session_state.active_paper_uid = str(path.resolve())
                _append_artifact(path, "pdf", str(path.resolve()))
                _action_log("download", f"手动URL下载成功: {path.name}")
            except Exception as exc:
                detail = str(exc)
                _append_action_error("download", f"{url}: {detail}")
                _action_log("download", f"手动URL下载失败: {url} | {detail}")
        _refresh_kb_files(config)
        if success == 0:
            raise RuntimeError("手动 URL 下载全部失败")

    return _run_action("download", "手动 URL 下载", _impl)


def _upload_local_pdfs(files: list[Any], config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        if not files:
            raise RuntimeError("请先选择要上传的 PDF 文件")
        out_dir = Path(config["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        success = 0
        for idx, item in enumerate(files, start=1):
            try:
                raw_name = Path(str(getattr(item, "name", f"upload_{idx}.pdf"))).name
                if not raw_name.lower().endswith(".pdf"):
                    continue
                target = out_dir / raw_name
                suffix = 1
                while target.exists():
                    target = out_dir / f"{Path(raw_name).stem}_{suffix}.pdf"
                    suffix += 1
                target.write_bytes(item.getvalue())
                st.session_state.downloaded[str(target.resolve())] = str(target.resolve())
                st.session_state.active_paper_uid = str(target.resolve())
                _append_artifact(target, "pdf", str(target.resolve()))
                _action_log("upload", f"上传成功: {target.name}")
                success += 1
            except Exception as exc:
                detail = str(exc)
                _append_action_error("upload", f"{getattr(item, 'name', 'unknown')}: {detail}")
                _action_log("upload", f"上传失败: {getattr(item, 'name', 'unknown')} | {detail}")
        _refresh_kb_files(config)
        if success == 0:
            raise RuntimeError("本地上传全部失败")

    return _run_action("upload", "本地上传入库", _impl)


def _read_one_pdf(pdf_path: Path, config: dict[str, Any]) -> tuple[bool, str, bool]:
    if not pdf_path.exists():
        return False, "目标 PDF 不存在", False
    base_url = (config["llm_base_url"] or "").strip()
    api_key = (config["llm_api_key"] or "").strip()
    model = (config["llm_model"] or "").strip()
    if not base_url or not api_key or not model:
        return False, "LLM 配置不完整", False

    def _run(mode: str) -> Path:
        service = ReadingService(Path(config["paper_skill_dir"]), mode=mode)
        full_text = service.extract_text(pdf_path)
        fulltext_path = pdf_path.with_name(f"{pdf_path.stem}_fulltext.txt")
        fulltext_path.write_text(full_text, encoding="utf-8")
        uid = str(pdf_path.resolve())
        _append_artifact(fulltext_path, "fulltext", uid)
        md_path = service.generate_report_md(
            paper_pdf=pdf_path,
            full_text=full_text,
            llm_base_url=base_url,
            llm_api_key=api_key,
            llm_model=model,
            focus_questions=config["focus_questions"],
        )
        _append_artifact(md_path, "report_md", uid)
        html_path = service.render_html(pdf_path)
        _append_artifact(html_path, "report_html", uid)
        validation_path = service.validate(pdf_path)
        _append_artifact(validation_path, "validation", uid)
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
        if not payload.get("summary", {}).get("passed", False):
            return validation_path
        return md_path

    mode = _normalize_integration_mode(config.get("integration_mode", "builtin"))
    try:
        result_path = _run(mode)
    except ExternalDependencyUnavailable as exc:
        if mode != "external":
            raise
        _fallback_to_builtin(config, reason=str(exc))
        result_path = _run("builtin")
    uid = str(pdf_path.resolve())
    st.session_state.downloaded[uid] = uid
    st.session_state.active_paper_uid = uid
    if result_path.suffix.lower() == ".json":
        return False, "hard checks 未通过", True
    return True, str(result_path), True


def _read_selected_pdfs(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        targets = [Path(p) for p in st.session_state.reading_targets if Path(p).exists()]
        if not targets:
            raise RuntimeError("请先在研读工作台选择至少一篇知识库 PDF")
        success = 0
        validate_failed = 0
        for pdf_path in targets:
            uid = str(pdf_path.resolve())
            title = pdf_path.stem
            try:
                ok, detail, validated = _read_one_pdf(pdf_path, config)
            except Exception as exc:
                ok, detail, validated = False, str(exc), False
            if ok:
                success += 1
                _action_log("read", f"生成成功: {title}")
                _action_log("validate", f"校验通过: {title}")
            else:
                if validated:
                    validate_failed += 1
                    _append_action_error("validate", f"{title}: {detail}")
                    _action_log("validate", f"校验失败: {title} | {detail}")
                else:
                    _append_action_error("read", f"{title}: {detail}")
                    _action_log("read", f"生成失败: {title} | {detail}")
        if validate_failed > 0:
            raise RuntimeError(f"{validate_failed} 篇论文校验未通过")
        if success == 0:
            raise RuntimeError("报告生成全部失败")

    return _run_action("read", "研读并自动校验", _impl)


def _run_auto(config: dict[str, Any]) -> None:
    ok = _recommend(config)
    if not ok:
        return
    _add_selection_to_download_queue(config)
    ok = _download_from_queue(config)
    if not ok:
        return
    _refresh_kb_files(config)
    st.session_state.reading_targets = [item["path"] for item in st.session_state.kb_files[: int(config["top_k"])]]
    _read_selected_pdfs(config)


@st.dialog("高级设置", width="large")
def _render_advanced_settings_dialog() -> None:
    mode_options = ["builtin", "external"]
    mode_labels = {
        "builtin": "builtin（内置实现，推荐）",
        "external": "external（外部 skill 兼容）",
    }
    current_mode = _normalize_integration_mode(st.session_state.integration_mode)
    mode_index = mode_options.index(current_mode) if current_mode in mode_options else 0
    st.session_state.integration_mode = st.selectbox(
        "实现模式",
        options=mode_options,
        index=mode_index,
        format_func=lambda m: mode_labels[m],
    )

    external_mode = st.session_state.integration_mode == "external"
    if not external_mode:
        st.caption("当前为 builtin 模式（线上推荐）：下方 skill 路径仅作兼容配置保存，不参与执行。")
    else:
        st.caption("当前为 external 模式（仅本地兼容）：若依赖缺失将自动回退 builtin。")

    st.session_state.aminer_skill_dir = st.text_input(
        "AMiner skill 目录",
        value=st.session_state.aminer_skill_dir,
        disabled=not external_mode,
    )
    st.session_state.paper_skill_dir = st.text_input(
        "paper-deep-reading skill 目录",
        value=st.session_state.paper_skill_dir,
        disabled=not external_mode,
    )
    st.session_state.aminer_rec5_url = st.text_input("AMINER_REC5_URL", value=st.session_state.aminer_rec5_url)
    st.session_state.output_dir = st.text_input("输出目录", value=st.session_state.output_dir)
    st.session_state.llm_base_url = st.text_input("PDR_LLM_BASE_URL", value=st.session_state.llm_base_url)
    st.session_state.aminer_api_key = st.text_input("AMINER_API_KEY", value=st.session_state.aminer_api_key, type="password")
    st.session_state.llm_api_key = st.text_input("PDR_LLM_API_KEY", value=st.session_state.llm_api_key, type="password")

    current_model = (st.session_state.llm_model or "").strip()
    model_options = MODEL_PRESETS + ["自定义"]
    default_choice = "自定义" if current_model not in MODEL_PRESETS else current_model
    model_choice = st.selectbox("模型选择", options=model_options, index=model_options.index(default_choice))
    if model_choice == "自定义":
        custom_default = current_model if current_model and current_model not in MODEL_PRESETS else ""
        custom_model = st.text_input("自定义模型名", value=custom_default, placeholder="例如: qwen-plus / deepseek-chat")
        st.session_state.llm_model = custom_model.strip() if custom_model.strip() else current_model
    else:
        st.session_state.llm_model = model_choice

    c1, c2 = st.columns(2)
    with c1:
        if st.button("显示 AMiner Key", key="adv_show_aminer_key", use_container_width=True):
            st.session_state.show_aminer_key_confirm = True
    with c2:
        if st.button("显示 LLM Key", key="adv_show_llm_key", use_container_width=True):
            st.session_state.show_llm_key_confirm = True
    if st.session_state.show_aminer_key_confirm and st.button("确认显示 AMiner 明文", key="adv_confirm_show_aminer_key", use_container_width=True):
        st.info(st.session_state.aminer_api_key or "未设置")
        st.session_state.show_aminer_key_confirm = False
    if st.session_state.show_llm_key_confirm and st.button("确认显示 LLM 明文", key="adv_confirm_show_llm_key", use_container_width=True):
        st.info(st.session_state.llm_api_key or "未设置")
        st.session_state.show_llm_key_confirm = False

    c3, c4 = st.columns(2)
    with c3:
        if st.button("保存配置到环境变量", key="adv_save_env", use_container_width=True):
            pairs = [
                ("AMINER_API_KEY", st.session_state.aminer_api_key),
                ("AMINER_REC5_URL", st.session_state.aminer_rec5_url),
                ("PDR_LLM_BASE_URL", st.session_state.llm_base_url),
                ("PDR_LLM_API_KEY", st.session_state.llm_api_key),
                ("PDR_LLM_MODEL", st.session_state.llm_model),
                ("PDR_INTEGRATION_MODE", st.session_state.integration_mode),
            ]
            rows = []
            for key, value in pairs:
                if key == "AMINER_REC5_URL" and not str(value or "").strip():
                    rows.append(f"{key}: SKIP 未设置，使用默认 {DEFAULT_REC5_URL}")
                    continue
                ok, msg = persist_env_var(key, value)
                if value:
                    os.environ[key] = value
                rows.append(f"{key}: {'OK' if ok else 'WARN'} {msg}")
            st.success("\n".join(rows))
    with c4:
        if st.button("保存配置到本地", key="adv_save_local", use_container_width=True):
            ok, msg = _save_persisted_config(_config_snapshot())
            st.session_state.persist_save_status = msg
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    if st.button("关闭", key="adv_close_dialog", use_container_width=True):
        st.rerun()


def _render_config_drawer() -> dict[str, Any]:
    _init_config_state()
    if "config_open" not in st.session_state:
        st.session_state.config_open = False
    if "show_aminer_key_confirm" not in st.session_state:
        st.session_state.show_aminer_key_confirm = False
    if "show_llm_key_confirm" not in st.session_state:
        st.session_state.show_llm_key_confirm = False

    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<h3 class='title'>配置抽屉</h3>", unsafe_allow_html=True)
    if st.button("展开配置" if not st.session_state.config_open else "收起配置", use_container_width=True):
        st.session_state.config_open = not st.session_state.config_open

    if st.session_state.config_open:
        st.session_state.topics = st.text_area("推荐主题（逗号/换行）", value=st.session_state.topics, height=120)
        st.session_state.top_k = st.slider("Top-K", min_value=1, max_value=5, value=int(st.session_state.top_k))
        st.session_state.year_range = st.slider("年份范围", min_value=2020, max_value=2026, value=tuple(st.session_state.year_range))
        st.session_state.focus_questions = st.text_area("关注问题（可选）", value=st.session_state.focus_questions, height=92)

        a, b = st.columns(2)
        with a:
            if st.button("高级设置", key="cfg_open_advanced", use_container_width=True):
                _render_advanced_settings_dialog()
        with b:
            if st.button("保存配置到本地", key="cfg_save_local", use_container_width=True):
                ok, msg = _save_persisted_config(_config_snapshot())
                st.session_state.persist_save_status = msg

        status_text = st.session_state.get("persist_save_status", "")
        if status_text:
            if status_text.startswith("本地配置已保存"):
                st.success(status_text)
            else:
                st.error(status_text)

        st.caption("一键自动流水线作为次级入口保留。")
        if st.button("运行一键自动流水线（可选）", use_container_width=True):
            _run_auto(_config_snapshot())
    else:
        st.markdown(
            f"<div class='muted'>主题数：{len(parse_topics(st.session_state.topics))} | Top-K：{st.session_state.top_k} | 年份：{st.session_state.year_range[0]}-{st.session_state.year_range[1]}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='muted'>实现模式：{st.session_state.integration_mode}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='muted'>输出目录：<span class='path' title='{st.session_state.output_dir}'>{_truncate_middle(st.session_state.output_dir, 52)}</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='muted'>模型：{st.session_state.llm_model or '未设置'}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)
    return _config_snapshot()


def _config_snapshot() -> dict[str, Any]:
    return {
        "topics": st.session_state.topics,
        "top_k": int(st.session_state.top_k),
        "year_range": tuple(st.session_state.year_range),
        "focus_questions": st.session_state.focus_questions,
        "integration_mode": st.session_state.integration_mode,
        "aminer_skill_dir": st.session_state.aminer_skill_dir,
        "paper_skill_dir": st.session_state.paper_skill_dir,
        "aminer_rec5_url": st.session_state.aminer_rec5_url,
        "output_dir": st.session_state.output_dir,
        "aminer_api_key": st.session_state.aminer_api_key,
        "llm_base_url": st.session_state.llm_base_url,
        "llm_api_key": st.session_state.llm_api_key,
        "llm_model": st.session_state.llm_model,
    }


def _render_topbar(config: dict[str, Any]) -> None:
    chips = [
        f"<span class='chip'>日期：{date.today()}</span>",
        "<span class='chip'>交互：工作台模式</span>",
        f"<span class='chip'>模式：{config['integration_mode']}</span>",
        f"<span class='chip'>KB：{len(st.session_state.kb_files)} 篇 PDF</span>",
        f"<span class='chip'>AMiner Key：{'已配置' if config['aminer_api_key'] else '未配置'}</span>",
        f"<span class='chip'>LLM Key：{'已配置' if config['llm_api_key'] else '未配置'}</span>",
        f"<span class='chip'>模型：{config['llm_model'] or 'N/A'}</span>",
    ]
    st.markdown(f"<div class='rc-top'>{''.join(chips)}</div>", unsafe_allow_html=True)
    notice = (st.session_state.get("integration_notice") or "").strip()
    if notice:
        st.info(notice)


def _render_main_title() -> None:
    st.markdown("<div class='hero-wrap'><h1 class='hero-title'>Paper Engine 论文工作台</h1></div>", unsafe_allow_html=True)


def _render_global_advanced_entry() -> None:
    with st.container(key="adv_fab_zone"):
        col1, col2 = st.columns([9, 1])
        with col2:
            if st.button("⚙ 高级设置", key="global_open_advanced", help="打开高级设置"):
                _render_advanced_settings_dialog()


def _render_recommend_config_inputs() -> None:
    st.markdown("<div class='tab-note'>在此配置推荐主题、Top-K、年份和关注问题。</div>", unsafe_allow_html=True)
    st.text_area(
        "推荐主题（逗号/换行）",
        height=96,
        key="topics",
    )
    c1, c2 = st.columns(2)
    with c1:
        st.slider(
            "Top-K",
            min_value=1,
            max_value=5,
            key="top_k",
        )
    with c2:
        st.slider(
            "年份范围",
            min_value=2020,
            max_value=2026,
            key="year_range",
        )
    st.text_area(
        "关注问题（可选）",
        height=72,
        key="focus_questions",
    )

    a, b = st.columns([1, 1])
    with a:
        if st.button("保存推荐配置到本地", key="rec_save_local_cfg", use_container_width=True):
            ok, msg = _save_persisted_config(_config_snapshot())
            st.session_state.persist_save_status = msg
            if ok:
                st.success(msg)
            else:
                st.error(msg)
    with b:
        if st.button("一键自动流水线（可选）", key="rec_run_auto", use_container_width=True):
            _run_auto(_config_snapshot())


def _render_recommend_workbench(config: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(
            "<div class='workbench-head'><h3 class='title'>推荐工作台</h3><span class='section-tag'>随时获取候选并加入下载队列</span></div>",
            unsafe_allow_html=True,
        )
        _render_recommend_config_inputs()
        st.markdown("---")
        c1, c2 = st.columns([2, 1])
        with c1:
            if st.button("获取推荐", type="primary", use_container_width=True):
                _recommend(config)
        with c2:
            if st.button("清空推荐结果", key="rec_clear_results", use_container_width=True):
                st.session_state.recommend_rows = []
                st.session_state.recommend_selected_uids = []
                st.session_state.download_queue_uids = []

        rows = st.session_state.recommend_rows
        if not rows:
            st.info("暂无推荐结果。你可以随时点击“获取推荐”。")
            return

        _apply_pending_recommend_pick_sync(rows)
        b1, b2, b3 = st.columns(3)
        if b1.button("全选候选", key="rec_pick_all", use_container_width=True):
            _set_recommend_selected_uids([row["uid"] for row in rows])
        if b2.button("清空选择", key="rec_pick_none", use_container_width=True):
            _set_recommend_selected_uids([])
        if b3.button("仅近两年", key="rec_pick_recent", use_container_width=True):
            threshold = date.today().year - 1
            _set_recommend_selected_uids([row["uid"] for row in rows if int(row.get("year") or 0) >= threshold])

        st.markdown("---")
        for row in rows:
            uid = row["uid"]
            title = row.get("title") or uid
            year_text = row.get("year") or "N/A"
            st.checkbox(f"{title} ({year_text})", value=uid in st.session_state.recommend_selected_uids, key=f"rec_pick_{uid}")
        _refresh_recommend_selected()

        q1, q2 = st.columns([2, 1])
        with q1:
            if st.button("加入下载队列（按 Top-K 截断）", key="rec_add_queue", use_container_width=True):
                if _add_selection_to_download_queue(config):
                    st.success(f"下载队列已更新：{len(st.session_state.download_queue_uids)} 项")
        with q2:
            st.markdown(f"<div class='muted'>当前队列：{len(st.session_state.download_queue_uids)} 项</div>", unsafe_allow_html=True)

        preview = [{"标题": r["title"], "年份": r.get("year") or "N/A"} for r in rows[:20]]
        st.dataframe(preview, use_container_width=True, hide_index=True)


def _render_kb_workbench(config: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(
            "<div class='workbench-head'><h3 class='title'>知识库工作台</h3><span class='section-tag'>推荐下载 + 手动 URL + 本地上传</span></div>",
            unsafe_allow_html=True,
        )
        if st.button("刷新知识库索引", key="kb_refresh", use_container_width=True):
            _refresh_kb_files(config)

        queue = list(st.session_state.download_queue_uids)
        if queue:
            st.markdown("##### 推荐队列下载")
            st.caption(f"待下载 {len(queue)} 项（来源：推荐工作台）")
            if st.button("下载队列论文到知识库", key="kb_download_queue", type="primary", use_container_width=True):
                _download_from_queue(config)
        else:
            st.info("推荐下载队列为空，可先在“推荐工作台”勾选候选并加入队列。")

        st.markdown("---")
        st.markdown("##### 手动 URL 入库")
        st.session_state.kb_manual_urls = st.text_area(
            "每行一个 PDF URL",
            value=st.session_state.kb_manual_urls,
            height=96,
            placeholder="https://arxiv.org/pdf/xxxx.xxxxx.pdf",
            key="kb_manual_urls_input",
        )
        if st.button("下载手动 URL 到知识库", key="kb_download_manual", use_container_width=True):
            _download_manual_urls(config)

        st.markdown("---")
        st.markdown("##### 本地 PDF 上传")
        uploaded = st.file_uploader(
            "选择本地 PDF 文件（可多选）",
            type=["pdf"],
            accept_multiple_files=True,
            key="kb_upload_files",
        )
        if st.button("上传文件到知识库", key="kb_upload_local", use_container_width=True):
            _upload_local_pdfs(uploaded or [], config)

        st.markdown("---")
        st.markdown("##### 当前知识库 PDF")
        kb_rows = st.session_state.kb_files
        if not kb_rows:
            st.info("知识库为空，请先下载或上传 PDF。")
        else:
            view = [
                {
                    "文件名": row["name"],
                    "大小(KB)": row["size_kb"],
                    "更新时间(时间戳)": int(row["mtime"]),
                }
                for row in kb_rows[:200]
            ]
            st.dataframe(view, use_container_width=True, hide_index=True)


def _render_read_workbench(config: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(
            "<div class='workbench-head'><h3 class='title'>研读工作台</h3><span class='section-tag'>从知识库选择并自动校验</span></div>",
            unsafe_allow_html=True,
        )
        kb_rows = st.session_state.kb_files
        if not kb_rows:
            st.info("知识库暂无 PDF，请先在“知识库工作台”入库。")
            return

        options: list[str] = []
        label_to_path: dict[str, str] = {}
        for row in kb_rows:
            path = row["path"]
            label = f"{row['name']} ({row['size_kb']} KB)"
            options.append(label)
            label_to_path[label] = path

        current_selected = set(st.session_state.reading_targets)
        default_labels = [label for label in options if label_to_path[label] in current_selected]
        chosen_labels = st.multiselect(
            "选择要研读的论文 PDF（可多选）",
            options=options,
            default=default_labels,
            key="read_targets_multiselect",
        )
        st.session_state.reading_targets = [label_to_path[label] for label in chosen_labels]

        r1, r2 = st.columns([2, 1])
        with r1:
            if st.button("开始研读并自动校验", key="read_start", type="primary", use_container_width=True):
                _read_selected_pdfs(config)
        with r2:
            if st.button("选择最新 Top-K", key="read_pick_latest", use_container_width=True):
                latest = [row["path"] for row in kb_rows[: int(config["top_k"])]]
                st.session_state.reading_targets = latest
                st.rerun()

        if st.session_state.reading_targets:
            st.markdown("##### 当前研读目标")
            for path in st.session_state.reading_targets:
                st.markdown(f"- `{_truncate_middle(path, 100)}`")


def _render_workbench(config: dict[str, Any]) -> None:
    t1, t2, t3 = st.tabs(["推荐工作台", "知识库工作台", "研读工作台"])
    with t1:
        _render_recommend_workbench(config)
    with t2:
        _render_kb_workbench(config)
    with t3:
        _render_read_workbench(config)


def _render_context(config: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown("<h3 class='title'>上下文侧栏</h3>", unsafe_allow_html=True)
        aminer_ready = bool(str(config.get("aminer_api_key", "")).strip())
        llm_ready = bool(str(config.get("llm_api_key", "")).strip())
        aminer_chip = "ok" if aminer_ready else "warn"
        llm_chip = "ok" if llm_ready else "warn"
        aminer_text = "已配置" if aminer_ready else "未配置"
        llm_text = "已配置" if llm_ready else "未配置"
        st.markdown(
            f"<div class='kv-line'><span class='kv-label'>AMiner Key</span><span class='kv-chip {aminer_chip}'>{aminer_text}</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='kv-line'><span class='kv-label'>LLM Key</span><span class='kv-chip {llm_chip}'>{llm_text}</span></div>",
            unsafe_allow_html=True,
        )

        options = ["(自动)"]
        uid_map = {"(自动)": ""}
        for row in st.session_state.kb_files:
            uid = row["path"]
            label = f"{_truncate_middle(Path(uid).stem, 22)} [{_truncate_middle(uid, 18)}]"
            options.append(label)
            uid_map[label] = uid
        selected_label = st.selectbox("当前论文上下文", options=options, key="ctx_uid_select")
        selected_uid = uid_map[selected_label]
        if selected_uid:
            st.session_state.active_paper_uid = selected_uid

        action_options = ["all", "recommend", "download", "upload", "read", "validate", "system"]
        action = st.selectbox("操作日志过滤", options=action_options, format_func=lambda x: "全部" if x == "all" else _action_label(x))
        logs = _logs_by_action(action, limit=220) or st.session_state.engine.logs[-120:]
        safe = "\n".join(logs).replace("<", "&lt;").replace(">", "&gt;")
        st.markdown("<div class='muted' style='margin-top:8px'>操作日志</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='log'>{safe.replace(chr(10), '<br/>')}</div>", unsafe_allow_html=True)

        st.markdown("<div class='muted' style='margin-top:10px'>产物索引</div>", unsafe_allow_html=True)
        shown = 0
        focus_uid = st.session_state.active_paper_uid
        visible_artifacts = []
        for item in st.session_state.artifacts:
            uid = item.get("uid", "")
            if focus_uid and uid and uid != focus_uid:
                continue
            shown += 1
            visible_artifacts.append(item)
            st.markdown(
                f"- `{item['kind']}` <span class='path' title='{item['path']}'>{_truncate_middle(item['path'], 54)}</span>",
                unsafe_allow_html=True,
            )
        if shown == 0:
            st.info("当前上下文暂无产物")
        else:
            options = [f"{idx + 1}. {item['kind']}" for idx, item in enumerate(visible_artifacts)]
            selected = st.selectbox("完整路径（可复制）", options=options)
            selected_idx = options.index(selected)
            st.code(visible_artifacts[selected_idx]["path"], language=None)

        errors: list[str] = []
        if action == "all":
            for key in ["recommend", "download", "upload", "read", "validate"]:
                errors.extend(st.session_state.action_errors.get(key, []))
        else:
            errors = st.session_state.action_errors.get(action, [])
        st.markdown("<div class='muted' style='margin-top:10px'>错误定位卡</div>", unsafe_allow_html=True)
        if errors:
            for err in errors[-8:]:
                st.error(err)
        else:
            st.success("当前操作无错误")


def _init_session() -> None:
    if "engine" not in st.session_state:
        st.session_state.engine = TaskEngine()
    if "show_aminer_key_confirm" not in st.session_state:
        st.session_state.show_aminer_key_confirm = False
    if "show_llm_key_confirm" not in st.session_state:
        st.session_state.show_llm_key_confirm = False
    if "active_paper_uid" not in st.session_state:
        st.session_state.active_paper_uid = ""
    if "action_errors" not in st.session_state:
        st.session_state.action_errors = {key: [] for key in ACTION_LABELS}
    if "candidates" not in st.session_state:
        st.session_state.candidates = []
    if "recommend_rows" not in st.session_state:
        st.session_state.recommend_rows = []
    if "recommend_selected_uids" not in st.session_state:
        st.session_state.recommend_selected_uids = []
    if "download_queue_uids" not in st.session_state:
        st.session_state.download_queue_uids = []
    if "kb_manual_urls" not in st.session_state:
        st.session_state.kb_manual_urls = ""
    if "kb_files" not in st.session_state:
        st.session_state.kb_files = []
    if "reading_targets" not in st.session_state:
        st.session_state.reading_targets = []
    if "resolved_urls" not in st.session_state:
        st.session_state.resolved_urls = {}
    if "downloaded" not in st.session_state:
        st.session_state.downloaded = {}
    if "artifacts" not in st.session_state:
        st.session_state.artifacts = []
    if "pending_recommend_pick_uids" not in st.session_state:
        st.session_state.pending_recommend_pick_uids = None
    if "integration_notice" not in st.session_state:
        st.session_state.integration_notice = ""


def main() -> None:
    _init_session()
    _init_config_state()
    config = _config_snapshot()
    _refresh_kb_files(config)
    _render_main_title()
    _render_global_advanced_entry()
    _render_topbar(config)
    center, right = st.columns([8, 3], gap="medium")
    with center:
        _render_workbench(config)
    with right:
        _render_context(config)


if __name__ == "__main__":
    main()

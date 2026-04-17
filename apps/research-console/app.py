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
    mask_secret,
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

STAGES = [
    ("recommend", "获取推荐"),
    ("lock", "锁定 Top-K"),
    ("download", "下载 PDF"),
    ("read", "生成报告"),
    ("validate", "校验完成"),
]
STAGE_INDEX = {key: idx for idx, (key, _) in enumerate(STAGES)}
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
.panel { border:1px solid var(--line); border-radius:14px; background:var(--panel); padding:12px;}
.title { margin:0 0 8px 0; font-size:17px; font-weight:700;}
.muted { color:var(--muted); font-size:13px; line-height:1.45;}
.stepper { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; margin:10px 0 14px;}
.step { border:1px solid var(--line); background:var(--soft); border-radius:10px; padding:8px; min-height:56px;}
.step.current { border-color:#7db4a8; background:#eff8f5;}
.step.success { border-color:#9fcfbe; background:#f3fbf8;}
.step.failed { border-color:#f2b9af; background:#fff5f4;}
.idx { font-size:11px; color:var(--muted);}
.label { margin-top:2px; font-size:13px; font-weight:600;}
.state { margin-top:4px; font-size:11px; color:var(--muted);}
.log { border:1px solid var(--line); border-radius:10px; background:#111827; color:#c9d4e4; padding:8px;
  max-height:350px; overflow:auto; font-size:12px; line-height:1.45; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.path { border-bottom:1px dashed var(--line); }
@media (max-width: 1024px) { .stepper { grid-template-columns: 1fr; } }
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
        _stage_log(st.session_state.current_stage, message)


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


def _stage_label(stage: str) -> str:
    for key, label in STAGES:
        if key == stage:
            return label
    return stage


def _next_stage(stage: str) -> str:
    idx = STAGE_INDEX.get(stage, 0)
    if idx >= len(STAGES) - 1:
        return stage
    return STAGES[idx + 1][0]


def _stage_log(stage: str, message: str) -> None:
    engine: TaskEngine = st.session_state.engine
    engine.log(f"[stage:{stage}] {message}")


def _set_stage_status(stage: str, status: str) -> None:
    st.session_state.stage_status_map[stage] = status


def _set_stage(stage: str) -> None:
    st.session_state.current_stage = stage


def _append_stage_error(stage: str, message: str) -> None:
    bucket = st.session_state.step_errors
    if stage not in bucket:
        bucket[stage] = []
    bucket[stage].append(message)


def _clear_stage_error(stage: str) -> None:
    st.session_state.step_errors[stage] = []


def _append_artifact(path: Path, kind: str, uid: str = "") -> None:
    resolved = str(path.resolve())
    for item in st.session_state.artifacts:
        if item["path"] == resolved and item["kind"] == kind and item.get("uid", "") == uid:
            return
    st.session_state.artifacts.append({"path": resolved, "kind": kind, "uid": uid})


def _paper_title(uid: str) -> str:
    for row in st.session_state.filtered_candidates:
        if row.get("uid") == uid:
            return row.get("title") or uid
    return uid


def _refresh_selected_uids() -> None:
    selected = []
    for row in st.session_state.filtered_candidates:
        uid = row["uid"]
        if st.session_state.get(f"pick_{uid}", False):
            selected.append(uid)
    st.session_state.selected_uids = selected


def _set_selected_uids(uids: list[str], sync_pick_keys: bool = True) -> None:
    st.session_state.selected_uids = list(uids)
    if not sync_pick_keys:
        return
    selected = set(uids)
    for row in st.session_state.filtered_candidates:
        uid = row["uid"]
        st.session_state[f"pick_{uid}"] = uid in selected


def _queue_pick_sync(uids: list[str]) -> None:
    st.session_state.pending_pick_uids = list(uids)


def _apply_pending_pick_sync(rows: list[dict[str, Any]]) -> None:
    pending = st.session_state.get("pending_pick_uids")
    if pending is None:
        return
    selected = set(pending)
    for row in rows:
        uid = row["uid"]
        st.session_state[f"pick_{uid}"] = uid in selected
    st.session_state.pending_pick_uids = None


def _selected_candidates() -> list[PaperCandidate]:
    selected = set(st.session_state.selected_uids)
    chosen: list[PaperCandidate] = []
    for row in st.session_state.filtered_candidates:
        if row["uid"] in selected:
            chosen.append(_candidate_from_row(row))
    return chosen


def _run_stage(stage: str, task_name: str, fn) -> bool:
    engine: TaskEngine = st.session_state.engine
    _clear_stage_error(stage)
    _set_stage_status(stage, "running")
    task_id = engine.queue(task_name, metadata={"stage": stage})
    engine.start(task_id)
    _stage_log(stage, f"开始: {task_name}")
    try:
        fn(engine)
    except Exception as exc:
        detail = str(exc)
        if stage == "lock" and "cannot be modified after the widget with key" in detail:
            detail = f"{detail}（检测到状态冲突；已启用延迟同步策略，请重试当前操作）"
        engine.fail(task_id, detail)
        _set_stage_status(stage, "failed")
        _append_stage_error(stage, detail)
        _stage_log(stage, f"失败: {detail}")
        st.error(f"{_stage_label(stage)}失败：{detail}")
        return False
    engine.success(task_id)
    _set_stage_status(stage, "success")
    _stage_log(stage, "完成")
    if stage != "validate":
        _set_stage(_next_stage(stage))
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
        st.session_state.filtered_candidates = [x.to_dict() for x in filtered]
        st.session_state.selected_uids = []
        st.session_state.resolved_urls = {}
        st.session_state.downloaded = {}
        st.session_state.artifacts = []
        st.session_state.pending_pick_uids = None
        for row in st.session_state.filtered_candidates:
            st.session_state[f"pick_{row['uid']}"] = False
        _stage_log("recommend", f"候选: raw={len(raw)} filtered={len(filtered)}")
        if not filtered:
            raise RuntimeError("推荐为空，请调整主题或年份范围")

    return _run_stage("recommend", "获取推荐", _impl)


def _lock_top_k(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        _refresh_selected_uids()
        selected = list(st.session_state.selected_uids)
        if not selected:
            fallback = select_top_k(
                [_candidate_from_row(row) for row in st.session_state.filtered_candidates],
                int(config["top_k"]),
            )
            selected = [x.uid for x in fallback]
        if not selected:
            raise RuntimeError("没有可锁定的论文")
        selected_set = set(selected)
        ordered = [row["uid"] for row in st.session_state.filtered_candidates if row["uid"] in selected_set]
        ordered = ordered[: int(config["top_k"])]
        _set_selected_uids(ordered, sync_pick_keys=False)
        _queue_pick_sync(ordered)
        st.session_state.active_paper_uid = ordered[0]
        _stage_log("lock", f"锁定论文数: {len(ordered)}")

    return _run_stage("lock", "锁定 Top-K", _impl)


def _download_one(uid: str, rank: int, config: dict[str, Any]) -> tuple[bool, str]:
    target = None
    for row in st.session_state.filtered_candidates:
        if row["uid"] == uid:
            target = _candidate_from_row(row)
            break
    if target is None:
        return False, "候选条目不存在"

    mapping = st.session_state.resolved_urls
    url = mapping.get(uid, "")
    if not url:
        resolved, source = resolve_pdf_url(target)
        if resolved:
            mapping[uid] = resolved
            url = resolved
            _stage_log("download", f"解析URL成功: {target.title} <- {source}")
    if not url:
        return False, "未解析到可用 PDF"

    out_dir = Path(config["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / choose_filename(rank, target.title)
    download_pdf(url, pdf_path)
    st.session_state.downloaded[uid] = str(pdf_path.resolve())
    st.session_state.active_paper_uid = uid
    _append_artifact(pdf_path, "pdf", uid)
    return True, str(pdf_path)


def _download(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        chosen = select_top_k(_selected_candidates(), int(config["top_k"]))
        if not chosen:
            raise RuntimeError("请先在步骤2锁定论文")
        success = 0
        for idx, item in enumerate(chosen, start=1):
            ok, detail = _download_one(item.uid, idx, config)
            if ok:
                success += 1
                _stage_log("download", f"下载成功: {item.title}")
            else:
                _append_stage_error("download", f"{item.title}: {detail}")
                _stage_log("download", f"下载失败: {item.title} | {detail}")
        write_today_recommendations(Path(config["output_dir"]), chosen, st.session_state.resolved_urls)
        _append_artifact(Path(config["output_dir"]) / "today_recommendations.md", "recommendation")
        _append_artifact(Path(config["output_dir"]) / "today_recommendations.json", "recommendation")
        if success == 0:
            raise RuntimeError("没有任何论文下载成功")

    return _run_stage("download", "下载 PDF", _impl)


def _read_one(uid: str, config: dict[str, Any]) -> tuple[bool, str]:
    pdf_raw = st.session_state.downloaded.get(uid, "")
    if not pdf_raw:
        return False, "找不到已下载 PDF 路径"
    base_url = (config["llm_base_url"] or "").strip()
    api_key = (config["llm_api_key"] or "").strip()
    model = (config["llm_model"] or "").strip()
    if not base_url or not api_key or not model:
        return False, "LLM 配置不完整"
    pdf_path = Path(pdf_raw)
    def _run(mode: str) -> Path:
        service = ReadingService(Path(config["paper_skill_dir"]), mode=mode)
        full_text = service.extract_text(pdf_path)
        fulltext_path = pdf_path.with_name(f"{pdf_path.stem}_fulltext.txt")
        fulltext_path.write_text(full_text, encoding="utf-8")
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
        return md_path

    mode = _normalize_integration_mode(config.get("integration_mode", "builtin"))
    try:
        md_path = _run(mode)
    except ExternalDependencyUnavailable as exc:
        if mode != "external":
            raise
        _fallback_to_builtin(config, reason=str(exc))
        md_path = _run("builtin")

    st.session_state.active_paper_uid = uid
    return True, str(md_path)


def _read(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        if not st.session_state.downloaded:
            raise RuntimeError("请先完成下载")
        success = 0
        for uid in list(st.session_state.downloaded.keys()):
            title = _paper_title(uid)
            try:
                ok, detail = _read_one(uid, config)
            except Exception as exc:
                ok, detail = False, str(exc)
            if ok:
                success += 1
                _stage_log("read", f"生成成功: {title}")
            else:
                _append_stage_error("read", f"{title}: {detail}")
                _stage_log("read", f"生成失败: {title} | {detail}")
        if success == 0:
            raise RuntimeError("报告生成全部失败")

    return _run_stage("read", "生成报告", _impl)


def _validate_one(uid: str, config: dict[str, Any]) -> tuple[bool, str]:
    pdf_raw = st.session_state.downloaded.get(uid, "")
    if not pdf_raw:
        return False, "缺少 PDF 路径"
    pdf_path = Path(pdf_raw)
    mode = _normalize_integration_mode(config.get("integration_mode", "builtin"))

    def _run(run_mode: str) -> Path:
        service = ReadingService(Path(config["paper_skill_dir"]), mode=run_mode)
        return service.validate(pdf_path)

    try:
        validation_path = _run(mode)
    except ExternalDependencyUnavailable as exc:
        if mode != "external":
            raise
        _fallback_to_builtin(config, reason=str(exc))
        validation_path = _run("builtin")

    _append_artifact(validation_path, "validation", uid)
    payload = json.loads(validation_path.read_text(encoding="utf-8"))
    if not payload.get("summary", {}).get("passed", False):
        return False, "hard checks 未通过"
    return True, str(validation_path)


def _validate(config: dict[str, Any]) -> bool:
    def _impl(engine: TaskEngine) -> None:
        if not st.session_state.downloaded:
            raise RuntimeError("请先完成前置步骤")
        failed = 0
        for uid in list(st.session_state.downloaded.keys()):
            title = _paper_title(uid)
            try:
                ok, detail = _validate_one(uid, config)
            except Exception as exc:
                ok, detail = False, str(exc)
            if ok:
                _stage_log("validate", f"校验通过: {title}")
            else:
                failed += 1
                _append_stage_error("validate", f"{title}: {detail}")
                _stage_log("validate", f"校验失败: {title} | {detail}")
        if failed:
            raise RuntimeError(f"{failed} 篇论文校验未通过")

    return _run_stage("validate", "执行校验", _impl)


def _report_uid_set() -> set[str]:
    seen: set[str] = set()
    for item in st.session_state.artifacts:
        if item.get("kind") == "report_md" and item.get("uid"):
            seen.add(item["uid"])
    return seen


def _validation_pass_uid_set() -> set[str]:
    passed: set[str] = set()
    for item in st.session_state.artifacts:
        if item.get("kind") != "validation" or not item.get("uid"):
            continue
        try:
            payload = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
            if payload.get("summary", {}).get("passed", False):
                passed.add(item["uid"])
        except Exception:
            continue
    return passed


def _render_retry_download(config: dict[str, Any]) -> None:
    failed = [uid for uid in st.session_state.selected_uids if uid not in st.session_state.downloaded]
    if not failed:
        return
    st.markdown("##### 条目重试")
    for uid in failed:
        title = _paper_title(uid)
        if st.button(f"重试下载：{title[:34]}", key=f"retry_download_{uid}", use_container_width=True):
            rank = max(1, st.session_state.selected_uids.index(uid) + 1)
            ok, detail = _download_one(uid, rank, config)
            if ok:
                st.success(f"已重试成功：{title}")
            else:
                st.error(f"重试失败：{title} | {detail}")


def _render_retry_read(config: dict[str, Any]) -> None:
    ready = set(st.session_state.downloaded.keys())
    finished = _report_uid_set()
    failed = [uid for uid in ready if uid not in finished]
    if not failed:
        return
    st.markdown("##### 条目重试")
    for uid in failed:
        title = _paper_title(uid)
        if st.button(f"重试研读：{title[:34]}", key=f"retry_read_{uid}", use_container_width=True):
            try:
                ok, detail = _read_one(uid, config)
            except Exception as exc:
                ok, detail = False, str(exc)
            if ok:
                st.success(f"已重试成功：{title}")
            else:
                st.error(f"重试失败：{title} | {detail}")


def _render_retry_validate(config: dict[str, Any]) -> None:
    ready = set(st.session_state.downloaded.keys())
    passed = _validation_pass_uid_set()
    failed = [uid for uid in ready if uid not in passed]
    if not failed:
        return
    st.markdown("##### 条目重试")
    for uid in failed:
        title = _paper_title(uid)
        if st.button(f"重试校验：{title[:34]}", key=f"retry_validate_{uid}", use_container_width=True):
            try:
                ok, detail = _validate_one(uid, config)
            except Exception as exc:
                ok, detail = False, str(exc)
            if ok:
                st.success(f"已重试成功：{title}")
            else:
                st.error(f"重试失败：{title} | {detail}")


def _run_auto(config: dict[str, Any]) -> None:
    _set_stage("recommend")
    if not _recommend(config):
        return
    _set_stage("lock")
    if not _lock_top_k(config):
        return
    _set_stage("download")
    if not _download(config):
        return
    _set_stage("read")
    if not _read(config):
        return
    _set_stage("validate")
    _validate(config)


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

        st.caption("一键流水线作为次级入口保留。")
        if st.button("运行一键自动流水线", use_container_width=True):
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
    current = st.session_state.current_stage
    chips = [
        "<span class='chip'>Research Console V2</span>",
        f"<span class='chip'>日期：{date.today()}</span>",
        f"<span class='chip'>当前阶段：{_stage_label(current)}</span>",
        f"<span class='chip'>模式：{config['integration_mode']}</span>",
        f"<span class='chip'>AMiner Key：{'已配置' if config['aminer_api_key'] else '未配置'}</span>",
        f"<span class='chip'>LLM Key：{'已配置' if config['llm_api_key'] else '未配置'}</span>",
        f"<span class='chip'>模型：{config['llm_model'] or 'N/A'}</span>",
    ]
    st.markdown(f"<div class='rc-top'>{''.join(chips)}</div>", unsafe_allow_html=True)
    notice = (st.session_state.get("integration_notice") or "").strip()
    if notice:
        st.info(notice)


def _render_stepper() -> None:
    current = st.session_state.current_stage
    status_map = st.session_state.stage_status_map
    html = ["<div class='stepper'>"]
    for idx, (key, label) in enumerate(STAGES, start=1):
        status = status_map.get(key, "pending")
        cls = "step"
        if key == current:
            cls += " current"
        if status == "success":
            cls += " success"
        if status == "failed":
            cls += " failed"
        state_text = {"pending": "待执行", "running": "执行中", "success": "完成", "failed": "失败"}.get(status, status)
        html.append(f"<div class='{cls}'><div class='idx'>STEP {idx}</div><div class='label'>{label}</div><div class='state'>{state_text}</div></div>")
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def _render_stage(config: dict[str, Any]) -> None:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<h3 class='title'>流程主舞台</h3>", unsafe_allow_html=True)
    st.markdown("<div class='muted'>默认分步可控：每个阶段仅保留一个主操作按钮。</div>", unsafe_allow_html=True)
    _render_stepper()
    stage = st.session_state.current_stage
    if stage == "recommend":
        st.subheader("步骤1：获取推荐")
        if st.button("获取推荐", type="primary", use_container_width=True):
            _recommend(config)
        rows = st.session_state.filtered_candidates
        if rows:
            preview = [{"标题": r["title"], "年份": r.get("year") or "N/A"} for r in rows[:12]]
            st.dataframe(preview, use_container_width=True, hide_index=True)
    elif stage == "lock":
        st.subheader("步骤2：锁定 Top-K")
        rows = st.session_state.filtered_candidates
        if not rows:
            st.warning("请先执行步骤1")
        else:
            _apply_pending_pick_sync(rows)
            a, b, c = st.columns(3)
            if a.button("全选", use_container_width=True):
                _set_selected_uids([row["uid"] for row in rows])
            if b.button("清空", use_container_width=True):
                _set_selected_uids([])
            if c.button("仅近两年", use_container_width=True):
                threshold = date.today().year - 1
                _set_selected_uids([row["uid"] for row in rows if int(row.get("year") or 0) >= threshold])
            st.markdown("---")
            for row in rows:
                uid = row["uid"]
                st.checkbox(f"{row['title']} ({row.get('year') or 'N/A'})", value=uid in st.session_state.selected_uids, key=f"pick_{uid}")
            _refresh_selected_uids()
            if st.button("锁定 Top-K 并进入下载", type="primary", use_container_width=True):
                _lock_top_k(config)
    elif stage == "download":
        st.subheader("步骤3：下载 PDF")
        selected = select_top_k(_selected_candidates(), int(config["top_k"]))
        if not selected:
            st.warning("请先执行步骤2")
        else:
            for i, item in enumerate(selected, start=1):
                status = "已下载" if st.session_state.downloaded.get(item.uid) else "待下载"
                st.markdown(f"{i}. **{item.title}** · `{status}`")
            if st.button("下载 PDF", type="primary", use_container_width=True):
                _download(config)
            _render_retry_download(config)
    elif stage == "read":
        st.subheader("步骤4：生成报告")
        if not st.session_state.downloaded:
            st.warning("请先执行步骤3")
        else:
            for uid, path in st.session_state.downloaded.items():
                st.markdown(f"- **{_paper_title(uid)}**")
                st.caption(_truncate_middle(path, 84))
            if st.button("开始研读并生成报告", type="primary", use_container_width=True):
                _read(config)
            _render_retry_read(config)
    else:
        st.subheader("步骤5：校验完成")
        if not st.session_state.downloaded:
            st.warning("请先执行前置步骤")
        else:
            if st.button("执行质量校验并完成", type="primary", use_container_width=True):
                _validate(config)
            _render_retry_validate(config)
            rows = st.session_state.engine.as_table_rows()
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_context(config: dict[str, Any]) -> None:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<h3 class='title'>上下文侧栏</h3>", unsafe_allow_html=True)
    st.markdown(f"<div class='muted'>AMiner Key：`{mask_secret(config['aminer_api_key']) or '未设置'}`</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='muted'>LLM Key：`{mask_secret(config['llm_api_key']) or '未设置'}`</div>", unsafe_allow_html=True)

    options = ["(自动)"]
    uid_map = {"(自动)": ""}
    for uid in st.session_state.downloaded.keys():
        label = f"{_truncate_middle(_paper_title(uid), 22)} [{uid}]"
        options.append(label)
        uid_map[label] = uid
    selected_label = st.selectbox("当前论文上下文", options=options)
    selected_uid = uid_map[selected_label]
    if selected_uid:
        st.session_state.active_paper_uid = selected_uid

    stage = st.session_state.current_stage
    logs = st.session_state.engine.logs_by_stage(stage, limit=220) or st.session_state.engine.logs[-120:]
    safe = "\n".join(logs).replace("<", "&lt;").replace(">", "&gt;")
    st.markdown("<div class='muted' style='margin-top:8px'>当前步骤日志</div>", unsafe_allow_html=True)
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

    errors = st.session_state.step_errors.get(stage, [])
    st.markdown("<div class='muted' style='margin-top:10px'>错误定位卡</div>", unsafe_allow_html=True)
    if errors:
        for err in errors[-8:]:
            st.error(err)
    else:
        st.success("当前步骤无错误")
    st.markdown("</div>", unsafe_allow_html=True)


def _init_session() -> None:
    if "engine" not in st.session_state:
        st.session_state.engine = TaskEngine()
    if "current_stage" not in st.session_state:
        st.session_state.current_stage = "recommend"
    if "stage_status_map" not in st.session_state:
        st.session_state.stage_status_map = {key: "pending" for key, _ in STAGES}
    if "active_paper_uid" not in st.session_state:
        st.session_state.active_paper_uid = ""
    if "step_errors" not in st.session_state:
        st.session_state.step_errors = {key: [] for key, _ in STAGES}
    if "candidates" not in st.session_state:
        st.session_state.candidates = []
    if "filtered_candidates" not in st.session_state:
        st.session_state.filtered_candidates = []
    if "selected_uids" not in st.session_state:
        st.session_state.selected_uids = []
    if "resolved_urls" not in st.session_state:
        st.session_state.resolved_urls = {}
    if "downloaded" not in st.session_state:
        st.session_state.downloaded = {}
    if "artifacts" not in st.session_state:
        st.session_state.artifacts = []
    if "pending_pick_uids" not in st.session_state:
        st.session_state.pending_pick_uids = None
    if "integration_notice" not in st.session_state:
        st.session_state.integration_notice = ""


def main() -> None:
    _init_session()
    left, center, right = st.columns([3, 6, 3], gap="medium")
    with left:
        config = _render_config_drawer()
    _render_topbar(config)
    with center:
        _render_stage(config)
    with right:
        _render_context(config)


if __name__ == "__main__":
    main()

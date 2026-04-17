from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

from services import (
    PaperCandidate,
    ReadingService,
    RecommenderService,
    choose_filename,
    default_aminer_skill_dir,
    default_paper_skill_dir,
    filter_candidates,
    mask_secret,
    parse_topics,
    persist_env_var,
    read_env_var,
    resolve_pdf_url,
    select_top_k,
    today_output_dir,
    write_today_recommendations,
    download_pdf,
)
from task_engine import TaskEngine


st.set_page_config(
    page_title="Research Console",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Noto+Sans+SC:wght@400;500;700&display=swap');
:root {
  --bg:#eef1f4;
  --surface:#f9fafb;
  --panel:#ffffff;
  --ink:#101726;
  --muted:#5b667a;
  --line:#d5dce6;
  --accent:#0b6d5d;
  --accent2:#0f4c81;
  --danger:#a93232;
}
html, body, [class*="css"]  {
  font-family:'Space Grotesk','Noto Sans SC','Segoe UI',sans-serif;
  color:var(--ink);
}
.stApp {
  background: radial-gradient(1200px 500px at 10% -20%, #dfe8f1 0%, transparent 45%),
              radial-gradient(900px 420px at 90% -25%, #e7efe8 0%, transparent 40%),
              var(--bg);
}
.top-status {
  border:1px solid var(--line);
  background: linear-gradient(92deg, rgba(15,76,129,0.09) 0%, rgba(11,109,93,0.08) 55%, rgba(255,255,255,0.9) 100%);
  border-radius:14px;
  padding:10px 14px;
  margin:6px 0 14px;
}
.panel {
  border:1px solid var(--line);
  background:var(--panel);
  border-radius:14px;
  padding:12px 12px;
}
.metric-chip {
  border:1px solid var(--line);
  background:var(--surface);
  border-radius:10px;
  padding:8px 10px;
  margin-bottom:8px;
}
.hint {
  color:var(--muted);
  font-size:0.92rem;
}
.section-title {
  font-weight:700;
  letter-spacing:0.2px;
  margin-bottom:8px;
}
.badge {
  display:inline-block;
  border:1px solid var(--line);
  border-radius:999px;
  padding:2px 8px;
  font-size:0.78rem;
  color:var(--muted);
  margin-right:6px;
}
.task-table {
  border:1px solid var(--line);
  border-radius:10px;
  overflow:hidden;
}
.log-box {
  background:#0f1624;
  color:#c6d1e2;
  border-radius:10px;
  border:1px solid #1f2c41;
  padding:8px;
  min-height:220px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size:12px;
  overflow:auto;
}
.small-muted {
  font-size:12px;
  color:var(--muted);
}
</style>
""",
    unsafe_allow_html=True,
)


if "engine" not in st.session_state:
    st.session_state.engine = TaskEngine()
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


def _env_default(name: str, fallback: str = "") -> str:
    v = read_env_var(name)
    return v if v else fallback


def _append_artifact(path: Path, kind: str) -> None:
    p = str(path.resolve())
    for item in st.session_state.artifacts:
        if item["path"] == p:
            return
    st.session_state.artifacts.append({"path": p, "kind": kind})


def _run_task(name: str, fn):
    engine: TaskEngine = st.session_state.engine
    tid = engine.queue(name)
    engine.start(tid)
    try:
        result = fn(engine)
        engine.success(tid)
        return result
    except Exception as exc:
        engine.fail(tid, str(exc))
        st.error(f"{name} 失败: {exc}")
        return None


def _candidate_from_dict(d: dict[str, Any]) -> PaperCandidate:
    return PaperCandidate(**d)


def _candidate_dicts(items: list[PaperCandidate]) -> list[dict[str, Any]]:
    return [c.to_dict() for c in items]


def _refresh_selection_keys() -> None:
    chosen: list[str] = []
    for row in st.session_state.filtered_candidates:
        uid = row["uid"]
        if st.session_state.get(f"pick_{uid}", uid in st.session_state.selected_uids):
            chosen.append(uid)
    st.session_state.selected_uids = chosen


aminer_default = str(default_aminer_skill_dir())
pdr_default = str(default_paper_skill_dir())
output_default = str(today_output_dir())

aminer_key = _env_default("AMINER_API_KEY")
llm_base = _env_default("PDR_LLM_BASE_URL", "https://api.openai.com/v1")
llm_key = _env_default("PDR_LLM_API_KEY")
llm_model = _env_default("PDR_LLM_MODEL", "gpt-4.1")

st.markdown(
    f"""
<div class='top-status'>
  <span class='badge'>Research Console</span>
  <span class='badge'>日期：{date.today()}</span>
  <span class='badge'>AMiner Key：{'已配置' if aminer_key else '未配置'}</span>
  <span class='badge'>LLM Base：{llm_base}</span>
  <span class='badge'>LLM Model：{llm_model}</span>
</div>
""",
    unsafe_allow_html=True,
)

left_col, center_col, right_col = st.columns([1.05, 2.3, 1.2], gap="large")

with left_col:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>控制面板</div>", unsafe_allow_html=True)

    aminer_skill_dir = st.text_input("AMiner skill 目录", value=aminer_default)
    pdr_skill_dir = st.text_input("Paper-Deep-Reading skill 目录", value=pdr_default)
    output_dir = st.text_input("输出目录", value=output_default)

    st.markdown("<div class='small-muted'>密钥将写入用户环境变量并在当前会话立即生效。</div>", unsafe_allow_html=True)
    aminer_key_input = st.text_input("AMINER_API_KEY", value=aminer_key, type="password")
    llm_base_input = st.text_input("PDR_LLM_BASE_URL", value=llm_base)
    llm_key_input = st.text_input("PDR_LLM_API_KEY", value=llm_key, type="password")
    llm_model_input = st.text_input("PDR_LLM_MODEL", value=llm_model)

    if st.button("保存配置到环境变量", use_container_width=True):
        pairs = [
            ("AMINER_API_KEY", aminer_key_input),
            ("PDR_LLM_BASE_URL", llm_base_input),
            ("PDR_LLM_API_KEY", llm_key_input),
            ("PDR_LLM_MODEL", llm_model_input),
        ]
        messages = []
        for key, val in pairs:
            ok, msg = persist_env_var(key, val)
            messages.append(f"{key}: {'OK' if ok else 'WARN'} {msg}")
            if val:
                os.environ[key] = val
        st.success("\n".join(messages))

    topic_text = st.text_area(
        "推荐主题（逗号/换行分隔）",
        value="AI for Research, autonomous research agents, literature review automation, hypothesis generation, experiment planning, tool-use for scientific workflows",
        height=100,
    )
    top_k = st.slider("Top-K 研读数量", min_value=1, max_value=5, value=3)
    year_range = st.slider("年份优先范围", min_value=2020, max_value=2026, value=(2025, 2026))
    run_mode = st.radio("运行模式", options=["分步工作台", "一键自动流水线"], index=0)
    focus_questions = st.text_area("研读关注问题（可选）", value="", height=70)

    st.markdown("</div>", unsafe_allow_html=True)

with center_col:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    tabs = st.tabs(["推荐", "研读", "任务时间线"])

    with tabs[0]:
        topics = parse_topics(topic_text)
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            if st.button("获取推荐", use_container_width=True):
                def _do_reco(engine: TaskEngine):
                    svc = RecommenderService(Path(aminer_skill_dir))
                    items = svc.recommend(topics)
                    filtered = filter_candidates(items, year_range[0], year_range[1])
                    st.session_state.candidates = _candidate_dicts(items)
                    st.session_state.filtered_candidates = _candidate_dicts(filtered)
                    engine.log(f"推荐完成: raw={len(items)} filtered={len(filtered)}")

                _run_task("推荐论文", _do_reco)

        with c2:
            if st.button("自动锁定 Top-K", use_container_width=True):
                top = select_top_k([_candidate_from_dict(d) for d in st.session_state.filtered_candidates], top_k)
                st.session_state.selected_uids = [t.uid for t in top]
                for uid in st.session_state.selected_uids:
                    st.session_state[f"pick_{uid}"] = True

        with c3:
            if st.button("解析 PDF 链接", use_container_width=True):
                def _resolve(engine: TaskEngine):
                    chosen = [
                        _candidate_from_dict(d)
                        for d in st.session_state.filtered_candidates
                        if d["uid"] in st.session_state.selected_uids
                    ]
                    mapping: dict[str, str] = {}
                    for c in chosen:
                        url, src = resolve_pdf_url(c)
                        if url:
                            mapping[c.uid] = url
                            engine.log(f"{c.title} -> {src}")
                        else:
                            engine.log(f"{c.title} -> no pdf")
                    st.session_state.resolved_urls = mapping
                    engine.log(f"可用PDF: {len(mapping)}/{len(chosen)}")

                _run_task("解析 PDF 链接", _resolve)

        st.markdown("---")
        if not st.session_state.filtered_candidates:
            st.info("先点击“获取推荐”加载候选论文。")
        else:
            st.caption(f"候选论文：{len(st.session_state.filtered_candidates)}")
            for row in st.session_state.filtered_candidates:
                uid = row["uid"]
                checked = st.checkbox(
                    f"{row['title']} ({row['year'] or 'N/A'})",
                    value=uid in st.session_state.selected_uids,
                    key=f"pick_{uid}",
                )
                if checked:
                    st.markdown(f"<span class='hint'>ID: {uid} | 作者: {', '.join(row['authors'][:3]) if row['authors'] else 'N/A'}</span>", unsafe_allow_html=True)
                    if uid in st.session_state.resolved_urls:
                        st.success(f"PDF: {st.session_state.resolved_urls[uid]}")
                    else:
                        st.warning("尚未解析到 PDF 链接")
            _refresh_selection_keys()

    with tabs[1]:
        st.caption("下载与研读会按“已锁定 Top-K”执行，单篇失败不会阻塞其他条目。")
        d1, d2, d3 = st.columns([1, 1, 1])

        with d1:
            if st.button("下载已选 PDF", use_container_width=True):
                def _download(engine: TaskEngine):
                    out_dir = Path(output_dir)
                    out_dir.mkdir(parents=True, exist_ok=True)

                    chosen = [
                        _candidate_from_dict(d)
                        for d in st.session_state.filtered_candidates
                        if d["uid"] in st.session_state.selected_uids
                    ]
                    chosen = select_top_k(chosen, top_k)
                    if not chosen:
                        raise RuntimeError("未选中任何候选论文")

                    if not st.session_state.resolved_urls:
                        mapping = {}
                        for c in chosen:
                            url, _ = resolve_pdf_url(c)
                            if url:
                                mapping[c.uid] = url
                        st.session_state.resolved_urls = mapping

                    downloaded: dict[str, str] = st.session_state.downloaded
                    for idx, c in enumerate(chosen, start=1):
                        url = st.session_state.resolved_urls.get(c.uid, "")
                        if not url:
                            engine.log(f"跳过（无PDF）: {c.title}")
                            continue
                        fn = choose_filename(idx, c.title)
                        pdf_path = out_dir / fn
                        download_pdf(url, pdf_path)
                        downloaded[c.uid] = str(pdf_path.resolve())
                        engine.log(f"下载完成: {pdf_path.name}")
                        _append_artifact(pdf_path, "pdf")

                    write_today_recommendations(out_dir, chosen, st.session_state.resolved_urls)
                    _append_artifact(out_dir / "today_recommendations.json", "recommendation")
                    _append_artifact(out_dir / "today_recommendations.md", "recommendation")

                _run_task("下载 PDF", _download)

        with d2:
            if st.button("开始研读已下载", use_container_width=True):
                def _read(engine: TaskEngine):
                    base_url = llm_base_input.strip()
                    api_key = llm_key_input.strip()
                    model = llm_model_input.strip()
                    if not base_url or not api_key or not model:
                        raise RuntimeError("LLM 配置不完整（base_url/api_key/model）")

                    svc = ReadingService(Path(pdr_skill_dir))
                    downloaded_map: dict[str, str] = st.session_state.downloaded
                    if not downloaded_map:
                        raise RuntimeError("没有已下载论文")

                    for uid, pdf in downloaded_map.items():
                        pdf_path = Path(pdf)
                        title = uid
                        for row in st.session_state.filtered_candidates:
                            if row["uid"] == uid:
                                title = row["title"]
                                break

                        engine.log(f"研读开始: {title}")
                        full_text = svc.extract_text(pdf_path)
                        _append_artifact(pdf_path.with_name(pdf_path.stem + "_fulltext.txt"), "fulltext")

                        md = svc.generate_report_md(
                            paper_pdf=pdf_path,
                            full_text=full_text,
                            llm_base_url=base_url,
                            llm_api_key=api_key,
                            llm_model=model,
                            focus_questions=focus_questions,
                        )
                        _append_artifact(md, "report_md")

                        html = svc.render_html(pdf_path)
                        _append_artifact(html, "report_html")

                        validation = svc.validate(pdf_path)
                        _append_artifact(validation, "validation")

                        vobj = json.loads(validation.read_text(encoding="utf-8"))
                        if not vobj.get("summary", {}).get("passed", False):
                            raise RuntimeError(f"校验未通过: {pdf_path.name}")
                        engine.log(f"研读完成: {title}")

                _run_task("生成研读报告", _read)

        with d3:
            if st.button("一键自动执行", use_container_width=True):
                def _auto(engine: TaskEngine):
                    local_topics = parse_topics(topic_text)
                    if not local_topics:
                        raise RuntimeError("topics 为空")

                    reco = RecommenderService(Path(aminer_skill_dir))
                    items = reco.recommend(local_topics)
                    filtered = filter_candidates(items, year_range[0], year_range[1])
                    chosen = select_top_k(filtered, top_k)
                    if not chosen:
                        raise RuntimeError("推荐结果为空")

                    st.session_state.candidates = _candidate_dicts(items)
                    st.session_state.filtered_candidates = _candidate_dicts(filtered)
                    st.session_state.selected_uids = [c.uid for c in chosen]

                    mapping: dict[str, str] = {}
                    for c in chosen:
                        url, src = resolve_pdf_url(c)
                        if url:
                            mapping[c.uid] = url
                            engine.log(f"auto-resolve {c.title} -> {src}")
                    st.session_state.resolved_urls = mapping

                    out_dir = Path(output_dir)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    downloaded: dict[str, str] = {}
                    for idx, c in enumerate(chosen, start=1):
                        url = mapping.get(c.uid, "")
                        if not url:
                            engine.log(f"auto skip no pdf: {c.title}")
                            continue
                        pdf_path = out_dir / choose_filename(idx, c.title)
                        download_pdf(url, pdf_path)
                        downloaded[c.uid] = str(pdf_path.resolve())
                        _append_artifact(pdf_path, "pdf")
                    st.session_state.downloaded = downloaded

                    write_today_recommendations(out_dir, chosen, mapping)
                    _append_artifact(out_dir / "today_recommendations.json", "recommendation")
                    _append_artifact(out_dir / "today_recommendations.md", "recommendation")

                    if not downloaded:
                        raise RuntimeError("自动流程未下载到可用 PDF")

                    base_url = llm_base_input.strip()
                    api_key = llm_key_input.strip()
                    model = llm_model_input.strip()
                    if not base_url or not api_key or not model:
                        raise RuntimeError("LLM 配置不完整（base_url/api_key/model）")

                    reader = ReadingService(Path(pdr_skill_dir))
                    for uid, p in downloaded.items():
                        pdf_path = Path(p)
                        txt = reader.extract_text(pdf_path)
                        md = reader.generate_report_md(
                            paper_pdf=pdf_path,
                            full_text=txt,
                            llm_base_url=base_url,
                            llm_api_key=api_key,
                            llm_model=model,
                            focus_questions=focus_questions,
                        )
                        html = reader.render_html(pdf_path)
                        val = reader.validate(pdf_path)
                        _append_artifact(md, "report_md")
                        _append_artifact(html, "report_html")
                        _append_artifact(val, "validation")

                _run_task("一键自动流水线", _auto)

        st.markdown("---")
        if not st.session_state.downloaded:
            st.info("暂无已下载论文。")
        else:
            st.success(f"已下载论文：{len(st.session_state.downloaded)}")
            for uid, path in st.session_state.downloaded.items():
                st.markdown(f"- `{uid}` -> `{path}`")

    with tabs[2]:
        rows = st.session_state.engine.as_table_rows()
        if not rows:
            st.info("暂无任务记录")
        else:
            st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("</div>", unsafe_allow_html=True)

with right_col:
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>证据与日志</div>", unsafe_allow_html=True)

    aminer_mask = mask_secret(aminer_key_input)
    llm_mask = mask_secret(llm_key_input)
    st.markdown(f"<div class='metric-chip'>AMiner Key: <span class='small-muted'>{aminer_mask or '未设置'}</span></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='metric-chip'>LLM Key: <span class='small-muted'>{llm_mask or '未设置'}</span></div>", unsafe_allow_html=True)

    st.markdown("<div class='small-muted'>运行日志</div>", unsafe_allow_html=True)
    log_text = "\n".join(st.session_state.engine.logs[-300:])
    st.markdown(f"<div class='log-box'>{log_text.replace('<', '&lt;').replace('>', '&gt;').replace(chr(10), '<br/>')}</div>", unsafe_allow_html=True)

    st.markdown("<div class='small-muted' style='margin-top:10px;'>产物索引</div>", unsafe_allow_html=True)
    if not st.session_state.artifacts:
        st.info("暂无产物")
    else:
        for item in st.session_state.artifacts[-200:]:
            st.code(f"[{item['kind']}] {item['path']}")

    st.markdown("</div>", unsafe_allow_html=True)

if run_mode == "一键自动流水线":
    st.info("当前模式：一键自动流水线。建议先保存配置，再点击“研读”页中的“一键自动执行”。")

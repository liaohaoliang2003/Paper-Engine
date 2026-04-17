from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import html
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib import error as urlerror
from urllib import parse, request
import xml.etree.ElementTree as ET


DEFAULT_REC5_URL = "https://datacenter.aminer.cn/gateway/open_platform/api/v3/paper/rec5"
CANONICAL_PROMPT_MARKER = "## Canonical Prompt（单段主 Prompt）"
BUILTIN_CANONICAL_PROMPT = (
    "你是一名严谨的论文审稿人与复现工程师，请仅基于我提供的论文全文/文本（优先 PDF 全文提取）进行深度研读并生成可落盘报告；"
    "输入契约：必须提供 `paper_source_path`，可选 `focus_questions` 与 `output_language`（默认中文）；"
    "从 `paper_source_path` 推导 `paper_dir`、`paper_stem`、`pdf_filename`，并写入 `{paper_dir}/{paper_stem}_report.md` 与 `{paper_dir}/{paper_stem}_report.html`；"
    "输出必须严格按 7 个一级章节、标题原样且按序：`1、简短总结`、`2、核心贡献与新颖性`、`3、针对的问题`、`4、分段详解`、`5、局限性与风险评估`、`6、后续可能的创新点和改进点`、`7、复现计划`；"
    "第4章必须按原文一级/二级标题顺序展开（无显式二级时构造等价二级并标注），每个二级小节必须含 `小节总结`、`深入分析`、`术语解释`、`示例或类比` 四槽位，且默认 200-350 字；"
    "术语解释执行“关键术语优先”，每个二级小节至少 1 个术语，采用三段式“通俗解释-专业定义-本文作用”，术语首次出现写作 `English Term（中文翻译）`，"
    "缩写首次出现写作 `Full Name（中文翻译，ABBR）`，译名不确定时显式声明；`术语解释：` 与 `证据锚点：` 必须独立成行；"
    "模型设计类论文必须包含架构图景、端到端流程示例、模块拆解（输入/输出/功能/相互作用）；"
    "含实验论文必须细化实验设置、流程、主结果、消融或误差分析、结果解读；每个关键判断都需绑定证据锚点并区分“原文陈述”与“推断”，信息不足时标注“信息不足/不确定性”；"
    "HTML 必须含原论文链接、轻量低干扰手风琴交互与无障碍属性，禁止空面板，提取失败时保留原段落；"
    "扩展章节仅允许放在第7章之后；若路径解析或写入失败，返回失败原因、目标路径与完整可复制的 MD/HTML 正文。"
)


class ExternalDependencyUnavailable(RuntimeError):
    """Raised when external skill mode is selected but required artifacts are unavailable."""


@dataclass
class PaperCandidate:
    uid: str
    title: str
    year: int
    paper_id: str
    aminer_url: str
    abs_url: str
    pdf_url: str
    arxiv_id: str
    authors: list[str]
    summary: str
    keywords: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_aminer_skill_dir() -> Path:
    env = get_env_var("AMINER_SKILL_BASE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".codex" / "skills" / "aminer-daily-paper"


def default_paper_skill_dir() -> Path:
    env = get_env_var("PDR_SKILL_BASE_DIR")
    if env:
        return Path(env)
    return repo_root() / "skills" / "paper-deep-reading"


def get_env_var(name: str) -> str:
    import os

    return os.getenv(name, "")


def read_env_var(name: str) -> str:
    import os

    return os.getenv(name, "")


def persist_env_var(name: str, value: str) -> tuple[bool, str]:
    import os

    value = str(value or "").strip()
    if not value:
        return False, f"{name} 为空"

    os.environ[name] = value

    if sys.platform.startswith("win"):
        cmd = ["setx", name, value]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "setx failed").strip()
            return False, f"持久化失败: {detail}"
        return True, "已写入用户环境变量（下次会话生效）"

    return False, "当前平台未实现持久化，仅本次会话有效"


def _extract_json_from_stdout(stdout_text: str) -> dict[str, Any]:
    text = (stdout_text or "").strip()
    if not text:
        raise RuntimeError("脚本输出为空")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise RuntimeError("无法从脚本输出解析 JSON")


def _clean_text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _safe_stem(text: str, limit: int = 120) -> str:
    text = _clean_text(text)
    text = re.sub(r"[^A-Za-z0-9\-_.]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:limit] if len(text) > limit else text


def _powershell_bin() -> str:
    for cmd in ["pwsh", "powershell"]:
        found = subprocess.run([cmd, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"], capture_output=True, text=True)
        if found.returncode == 0:
            return cmd
    raise RuntimeError("未找到可用的 PowerShell 解释器（pwsh/powershell）")


def _parse_arxiv_pdf_from_title(title: str) -> str:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    q = f'ti:"{_clean_text(title)}"'
    url = "http://export.arxiv.org/api/query?search_query=" + parse.quote(q) + "&start=0&max_results=1"
    data = request.urlopen(url, timeout=20).read()  # nosec B310
    root = ET.fromstring(data)
    entry = root.find("a:entry", ns)
    if entry is None:
        return ""
    arxiv_id = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
    if "/abs/" in arxiv_id:
        return arxiv_id.replace("/abs/", "/pdf/") + ".pdf"
    return ""


def _normalize_rec5_paper(raw: dict[str, Any]) -> dict[str, Any]:
    paper_id = _clean_text(raw.get("paper_id") or raw.get("id"))
    links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
    aminer_url = (
        _clean_text(links.get("aminer"))
        or _clean_text(raw.get("paper_url"))
        or (f"https://www.aminer.cn/pub/{paper_id}" if paper_id else "")
    )
    arxiv_url = _clean_text(links.get("arxiv") or raw.get("arxiv_url") or raw.get("abs_url"))
    pdf_url = _clean_text(links.get("pdf") or raw.get("pdf_url"))
    arxiv_id = _clean_text(raw.get("arxiv_id") or "")

    year_raw = raw.get("year")
    try:
        year = int(year_raw) if year_raw is not None else 0
    except Exception:
        year = 0

    return {
        "paper_id": paper_id,
        "aminer_paper_url": aminer_url,
        "abs_url": arxiv_url,
        "pdf_url": pdf_url,
        "arxiv_id": arxiv_id,
        "title": _clean_text(raw.get("title")),
        "year": year,
        "authors": [_clean_text(a) for a in list(raw.get("authors") or []) if _clean_text(a)],
        "keywords": [_clean_text(k) for k in list(raw.get("keywords") or []) if _clean_text(k)],
        "summary": _clean_text(raw.get("summary") or ""),
    }


def _convert_inline_markdown(text: str) -> str:
    safe = html.escape(text)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", safe)
    return safe


def _markdown_to_html_fragment(markdown: str) -> str:
    lines = markdown.splitlines()
    html_lines: list[str] = []
    in_ul = False
    in_ol = False
    in_code = False

    def _close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            html_lines.append("</ul>")
            in_ul = False
        if in_ol:
            html_lines.append("</ol>")
            in_ol = False

    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^```", line):
            _close_lists()
            if not in_code:
                html_lines.append("<pre><code>")
                in_code = True
            else:
                html_lines.append("</code></pre>")
                in_code = False
            continue

        if in_code:
            html_lines.append(html.escape(line))
            continue

        if not line.strip():
            _close_lists()
            continue

        m = re.match(r"^###\s+(.+)$", line)
        if m:
            _close_lists()
            html_lines.append(f"<h3>{_convert_inline_markdown(m.group(1))}</h3>")
            continue
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            _close_lists()
            html_lines.append(f"<h2>{_convert_inline_markdown(m.group(1))}</h2>")
            continue
        m = re.match(r"^#\s+(.+)$", line)
        if m:
            _close_lists()
            html_lines.append(f"<h1>{_convert_inline_markdown(m.group(1))}</h1>")
            continue
        m = re.match(r"^\d+\.\s+(.+)$", line)
        if m:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            if not in_ol:
                html_lines.append("<ol>")
                in_ol = True
            html_lines.append(f"<li>{_convert_inline_markdown(m.group(1))}</li>")
            continue
        m = re.match(r"^[-*]\s+(.+)$", line)
        if m:
            if in_ol:
                html_lines.append("</ol>")
                in_ol = False
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"<li>{_convert_inline_markdown(m.group(1))}</li>")
            continue

        _close_lists()
        html_lines.append(f"<p>{_convert_inline_markdown(line)}</p>")

    if in_ul:
        html_lines.append("</ul>")
    if in_ol:
        html_lines.append("</ol>")
    if in_code:
        html_lines.append("</code></pre>")
    return "\n".join(html_lines)


def _normalize_markdown_lines(content: str) -> str:
    lines = content.splitlines()
    out: list[str] = []
    meta_re = re.compile(r"^\s*(术语解释：|证据锚点：)")
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if meta_re.match(line):
            line = line.lstrip()
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            if idx + 1 < len(lines) and lines[idx + 1].strip():
                out.append("")
        else:
            out.append(line)
        idx += 1

    collapsed: list[str] = []
    blank = 0
    for line in out:
        if not line.strip():
            blank += 1
        else:
            blank = 0
        if blank <= 2:
            collapsed.append(line)
    text = "\n".join(collapsed).rstrip()
    return text + ("\n" if text else "")


def _build_accordion_html(kind: str, body_html: str) -> str:
    label = "术语解释" if kind == "term" else "证据锚点"
    item_class = "term-item" if kind == "term" else "evidence-item"
    return (
        f'<div class="accordion-item {item_class}">\n'
        '  <button class="accordion-trigger" aria-expanded="false" type="button">\n'
        f'    <span class="meta-label">{label}</span><span class="caret">▸</span>\n'
        "  </button>\n"
        f'  <div class="accordion-panel" hidden><p>{body_html}</p></div>\n'
        "</div>"
    )


def _inject_accordion(fragment: str) -> str:
    term_re = re.compile(r"<p>术语解释：(.*?)</p>", flags=re.S)
    evidence_re = re.compile(r"<p>证据锚点：(.*?)</p>", flags=re.S)

    def _replace(kind: str):
        def _inner(match: re.Match[str]) -> str:
            body = match.group(1).strip()
            if not body:
                return match.group(0)
            return _build_accordion_html(kind, body)

        return _inner

    out = term_re.sub(_replace("term"), fragment)
    out = evidence_re.sub(_replace("evidence"), out)
    return out


def inspect_external_dependencies(aminer_skill_dir: Path, paper_skill_dir: Path) -> tuple[bool, str]:
    missing: list[str] = []
    aminer_script = aminer_skill_dir / "scripts" / "handle_trigger.py"
    if not aminer_script.exists():
        missing.append(f"缺少 AMiner 脚本: {aminer_script}")

    prompt_spec = paper_skill_dir / "references" / "output-structure-spec.md"
    if not prompt_spec.exists():
        missing.append(f"缺少协议文件: {prompt_spec}")

    render_script = paper_skill_dir / "scripts" / "render_report_html.ps1"
    if not render_script.exists():
        missing.append(f"缺少渲染脚本: {render_script}")

    validate_script = paper_skill_dir / "scripts" / "validate_report.ps1"
    if not validate_script.exists():
        missing.append(f"缺少校验脚本: {validate_script}")

    try:
        _powershell_bin()
    except Exception:
        missing.append("未找到可用 PowerShell（pwsh/powershell）")

    if missing:
        return False, "；".join(missing)
    return True, ""


class RecommenderService:
    def __init__(
        self,
        skill_dir: Path,
        python_bin: str = "python",
        mode: str = "builtin",
        aminer_api_key: str = "",
        aminer_rec5_url: str = "",
    ) -> None:
        self.skill_dir = skill_dir
        self.python_bin = python_bin
        self.mode = (mode or "builtin").strip().lower()
        self.aminer_api_key = _clean_text(aminer_api_key)
        self.aminer_rec5_url = _clean_text(aminer_rec5_url)

    def _ensure_external_ready(self) -> None:
        script = self.skill_dir / "scripts" / "handle_trigger.py"
        if not script.exists():
            raise ExternalDependencyUnavailable(f"external 模式不可用：找不到 AMiner 脚本: {script}")

    def recommend(self, topics: list[str]) -> list[PaperCandidate]:
        if not topics:
            raise RuntimeError("topics 不能为空")
        if self.mode == "external":
            return self._recommend_external(topics)
        return self._recommend_builtin(topics)

    def _recommend_external(self, topics: list[str]) -> list[PaperCandidate]:
        self._ensure_external_ready()
        script = self.skill_dir / "scripts" / "handle_trigger.py"

        trigger = "/aminer-dp topics: " + ", ".join([t for t in topics if _clean_text(t)])
        cmd = [
            self.python_bin,
            str(script),
            "--base-dir",
            str(self.skill_dir),
            "--text",
            trigger,
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "aminer call failed").strip()
            raise RuntimeError(detail)

        result = _extract_json_from_stdout(completed.stdout)
        pipeline = result.get("pipeline", {})
        summarized_path = pipeline.get("summarized_path")
        if not summarized_path:
            raise RuntimeError("推荐返回缺少 summarized_path")

        p = Path(str(summarized_path))
        if not p.exists():
            raise RuntimeError(f"推荐结果文件不存在: {p}")

        payload = json.loads(p.read_text(encoding="utf-8"))
        papers = payload.get("papers", [])
        return self._to_candidates(papers)

    def _recommend_builtin(self, topics: list[str]) -> list[PaperCandidate]:
        token = _clean_text(self.aminer_api_key or get_env_var("AMINER_API_KEY"))
        if not token:
            raise RuntimeError("builtin 推荐失败：缺少 AMINER_API_KEY")

        rec5_url = _clean_text(self.aminer_rec5_url or get_env_var("AMINER_REC5_URL")) or DEFAULT_REC5_URL
        params = {"topics": [_clean_text(t) for t in topics if _clean_text(t)], "size": 20, "offset": 0}
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            rec5_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json;charset=utf-8",
                "Authorization": token,
                "User-Agent": "paper-engine/1.0",
                "X-Platform": "openclaw",
            },
        )
        try:
            with request.urlopen(req, timeout=45) as resp:  # nosec B310
                payload = json.loads(resp.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            raise RuntimeError(f"builtin 推荐失败：AMiner API HTTP {exc.code} @ {rec5_url}") from exc
        except Exception as exc:
            raise RuntimeError(f"builtin 推荐失败：AMiner API 请求异常 {exc.__class__.__name__}") from exc

        if not payload.get("success"):
            msg = _clean_text(payload.get("msg") or payload.get("code") or "api_failed")
            raise RuntimeError(f"builtin 推荐失败：{msg}")

        data = payload.get("data")
        if isinstance(data, list) and data:
            papers = [p for p in list(data[0].get("papers") or []) if isinstance(p, dict)]
        elif isinstance(data, dict):
            papers = [p for p in list(data.get("papers") or []) if isinstance(p, dict)]
        else:
            papers = []
        if not papers:
            raise RuntimeError("builtin 推荐失败：AMiner 返回空结果")

        return self._to_candidates([_normalize_rec5_paper(p) for p in papers])

    def _to_candidates(self, papers: list[dict[str, Any]]) -> list[PaperCandidate]:
        candidates: list[PaperCandidate] = []
        for idx, paper in enumerate(papers, start=1):
            title = _clean_text(paper.get("title"))
            if not title:
                continue
            try:
                year_int = int(paper.get("year"))
            except Exception:
                year_int = 0
            raw_id = _clean_text(paper.get("paper_id"))
            uid = raw_id or f"paper-{idx}-{_safe_stem(title, limit=40)}"
            candidates.append(
                PaperCandidate(
                    uid=uid,
                    title=title,
                    year=year_int,
                    paper_id=raw_id,
                    aminer_url=_clean_text(paper.get("aminer_paper_url") or paper.get("paper_url")),
                    abs_url=_clean_text(paper.get("abs_url")),
                    pdf_url=_clean_text(paper.get("pdf_url")),
                    arxiv_id=_clean_text(paper.get("arxiv_id")),
                    authors=[_clean_text(a) for a in list(paper.get("authors") or []) if _clean_text(a)],
                    summary=_clean_text(paper.get("summary")),
                    keywords=[_clean_text(k) for k in list(paper.get("keywords") or []) if _clean_text(k)],
                )
            )
        return candidates


def filter_candidates(candidates: list[PaperCandidate], year_start: int, year_end: int) -> list[PaperCandidate]:
    dedup: dict[str, PaperCandidate] = {}
    order: list[str] = []

    for c in candidates:
        key = (c.paper_id or c.title).strip().lower()
        if key in dedup:
            continue
        dedup[key] = c
        order.append(key)

    ranked: list[PaperCandidate] = []
    for key in order:
        ranked.append(dedup[key])

    ranked.sort(key=lambda c: (0 if year_start <= (c.year or 0) <= year_end else 1, -(c.year or 0)))
    return ranked


def resolve_pdf_url(candidate: PaperCandidate) -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []

    if candidate.abs_url:
        if "arxiv.org/abs/" in candidate.abs_url:
            candidates.append((candidate.abs_url.replace("/abs/", "/pdf/") + ".pdf", "arxiv-abs"))
        elif candidate.abs_url.lower().endswith(".pdf"):
            candidates.append((candidate.abs_url, "abs-pdf"))

    if candidate.arxiv_id:
        aid = candidate.arxiv_id.replace("arXiv:", "").strip()
        candidates.append((f"https://arxiv.org/pdf/{aid}.pdf", "arxiv-id"))

    if candidate.pdf_url and candidate.pdf_url.lower().startswith("http"):
        candidates.append((candidate.pdf_url, "direct-pdf"))

    try:
        arxiv_pdf = _parse_arxiv_pdf_from_title(candidate.title)
        if arxiv_pdf:
            candidates.append((arxiv_pdf, "arxiv-title"))
    except Exception:
        pass

    seen = set()
    for url, src in candidates:
        if url in seen:
            continue
        seen.add(url)
        if url.lower().endswith(".pdf"):
            return url, src

    return "", ""


def download_pdf(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=60) as resp:  # nosec B310
        data = resp.read()
    if not data:
        raise RuntimeError("下载为空")
    output_path.write_bytes(data)


class ReadingService:
    def __init__(self, paper_skill_dir: Path, python_bin: str = "python", mode: str = "builtin") -> None:
        self.paper_skill_dir = paper_skill_dir
        self.python_bin = python_bin
        self.mode = (mode or "builtin").strip().lower()

    def _ensure_external_ready(self, *, needs_powershell: bool = False) -> None:
        spec = self.paper_skill_dir / "references" / "output-structure-spec.md"
        if not spec.exists():
            raise ExternalDependencyUnavailable(f"external 模式不可用：缺少协议文件: {spec}")
        render = self.paper_skill_dir / "scripts" / "render_report_html.ps1"
        if not render.exists():
            raise ExternalDependencyUnavailable(f"external 模式不可用：缺少渲染脚本: {render}")
        validate = self.paper_skill_dir / "scripts" / "validate_report.ps1"
        if not validate.exists():
            raise ExternalDependencyUnavailable(f"external 模式不可用：缺少校验脚本: {validate}")
        if needs_powershell:
            try:
                _powershell_bin()
            except Exception as exc:
                raise ExternalDependencyUnavailable("external 模式不可用：未找到可用 PowerShell（pwsh/powershell）") from exc

    def extract_text(self, paper_pdf: Path) -> str:
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise RuntimeError("缺少 pypdf 依赖，请先安装 pypdf") from exc

        reader = PdfReader(str(paper_pdf))
        chunks: list[str] = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        text = "\n".join(chunks)
        if len(text.strip()) < 1000:
            raise RuntimeError("PDF 提取文本过少，可能是扫描版或解析失败")
        return text

    def _canonical_prompt(self) -> str:
        if self.mode != "external":
            return BUILTIN_CANONICAL_PROMPT

        self._ensure_external_ready()
        spec_path = self.paper_skill_dir / "references" / "output-structure-spec.md"
        lines = spec_path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if line.strip() == CANONICAL_PROMPT_MARKER:
                for j in range(idx + 1, len(lines)):
                    if lines[j].strip():
                        return lines[j].strip()
        raise ExternalDependencyUnavailable("external 模式不可用：未找到 canonical prompt")

    def _chat_completion(self, *, base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            endpoint = base + "/chat/completions"
        else:
            endpoint = base + "/v1/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with request.urlopen(req, timeout=240) as resp:  # nosec B310
            body = resp.read().decode("utf-8")
        obj = json.loads(body)
        choices = obj.get("choices", [])
        if not choices:
            raise RuntimeError("LLM 返回空 choices")
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            content = "\n".join(parts)
        content = str(content or "").strip()
        if not content:
            raise RuntimeError("LLM 返回空内容")
        return content

    def generate_report_md(
        self,
        *,
        paper_pdf: Path,
        full_text: str,
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
        focus_questions: str = "",
    ) -> Path:
        prompt = self._canonical_prompt()
        paper_stem = paper_pdf.stem
        md_path = paper_pdf.with_name(f"{paper_stem}_report.md")

        user_msg = (
            f"{prompt}\n"
            f"\n输入参数：\n"
            f"paper_source_path: {paper_pdf.resolve()}\n"
            f"focus_questions: {focus_questions or '无'}\n"
            f"output_language: 中文\n"
            f"\n以下是论文全文（从 PDF 提取）:\n"
            f"<<<FULLTEXT\n{full_text}\nFULLTEXT>>>\n"
            f"\n请严格输出 Markdown 报告正文，不要输出额外解释。"
        )

        content = self._chat_completion(
            base_url=llm_base_url,
            api_key=llm_api_key,
            model=llm_model,
            messages=[
                {"role": "system", "content": "你是严格遵循格式约束的论文研读助手。"},
                {"role": "user", "content": user_msg},
            ],
        )

        md_path.write_text(content, encoding="utf-8")
        return md_path

    def _render_html_builtin(self, paper_pdf: Path) -> Path:
        md_path = paper_pdf.with_name(f"{paper_pdf.stem}_report.md")
        html_path = paper_pdf.with_name(f"{paper_pdf.stem}_report.html")
        if not md_path.exists():
            raise RuntimeError(f"找不到 Markdown 报告: {md_path}")

        md_raw = md_path.read_text(encoding="utf-8")
        md_norm = _normalize_markdown_lines(md_raw)
        md_path.write_text(md_norm, encoding="utf-8")

        fragment = _markdown_to_html_fragment(md_norm)
        fragment = _inject_accordion(fragment)

        pdf_file = paper_pdf.name
        paper_abs = html.escape(str(paper_pdf.resolve()))
        source_meta = (
            f"<p class='source-meta'>原论文链接：<a href='./{pdf_file}'>{pdf_file}</a></p>"
            f"<p class='source-meta'>原文路径：{paper_abs}</p>"
        )

        style = """
<style>
:root{--line:#d9dee8;--text:#1f2328;--muted:#667085}
*{box-sizing:border-box}
body{margin:0;background:#f7f8fa;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:var(--text);line-height:1.78}
.page{max-width:1024px;margin:20px auto;padding:0 14px}
.article{background:#fff;border:1px solid #e7ebf2;border-radius:12px;padding:20px}
h1,h2,h3{line-height:1.35}
h1{border-bottom:1px solid #eef2f7;padding-bottom:8px}
p,li{font-size:16px}
code{background:#f3f5f8;padding:1px 5px;border-radius:5px}
a{color:#0969da;text-decoration:none}
a:hover{text-decoration:underline}
.source-meta{font-size:13px;color:#5d6678;margin:2px 0}

.toolbar{display:flex;gap:8px;justify-content:flex-end;margin:4px 0 10px}
.toolbar button{font-size:11px;color:#7a8394;background:transparent;border:0;cursor:pointer;padding:1px 3px}
.toolbar button:hover{color:#344054;text-decoration:underline}

.accordion-item{margin:3px 0}
.accordion-trigger{all:unset;display:inline-flex;align-items:center;gap:5px;cursor:pointer;color:#6f7787;font-size:11px;line-height:1.35;padding:0}
.accordion-trigger:hover{color:#344054}
.accordion-trigger:focus{outline:1px dashed #a3b2c7;outline-offset:2px}
.accordion-trigger .meta-label{font-weight:600}
.accordion-trigger .caret{font-size:10px;transition:transform .15s ease}
.accordion-trigger[aria-expanded='true'] .caret{transform:rotate(90deg)}
.accordion-panel{margin-top:2px;padding:4px 6px;border-left:1px solid #e7ecf3;background:transparent}
.accordion-panel p{margin:0;color:#3b475b;font-size:13px}

@media (max-width:720px){.article{padding:14px}}
</style>
"""

        script = """
<script>
(function(){
  const items = Array.from(document.querySelectorAll('.accordion-item'));
  function setItem(item, expanded){
    const btn = item.querySelector('.accordion-trigger');
    const panel = item.querySelector('.accordion-panel');
    if(!btn || !panel) return;
    btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    panel.hidden = !expanded;
  }
  items.forEach(item => {
    const btn = item.querySelector('.accordion-trigger');
    if(!btn) return;
    btn.addEventListener('click', () => {
      const expanded = btn.getAttribute('aria-expanded') === 'true';
      setItem(item, !expanded);
    });
    btn.addEventListener('keydown', (e) => {
      if(e.key === 'Enter' || e.key === ' '){
        e.preventDefault();
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        setItem(item, !expanded);
      }
    });
  });
  const expandAll = document.getElementById('expand-all');
  const collapseAll = document.getElementById('collapse-all');
  if(expandAll) expandAll.addEventListener('click', () => items.forEach(i => setItem(i, true)));
  if(collapseAll) collapseAll.addEventListener('click', () => items.forEach(i => setItem(i, false)));
})();
</script>
"""

        controls = "<div class='toolbar'><button id='expand-all' type='button'>全部展开</button><button id='collapse-all' type='button'>全部收起</button></div>"
        title = html.escape(f"{paper_pdf.stem} 研读报告")
        html_doc = (
            "<!doctype html>\n"
            "<html lang='zh-CN'>\n"
            "<head>\n"
            "<meta charset='utf-8'/>\n"
            "<meta name='viewport' content='width=device-width, initial-scale=1'/>\n"
            f"<title>{title}</title>\n"
            f"{style}\n"
            "</head>\n"
            "<body>\n"
            "<main class='page'>\n"
            "  <section class='article'>\n"
            f"    {source_meta}\n"
            f"    {controls}\n"
            f"    {fragment}\n"
            "  </section>\n"
            "</main>\n"
            f"{script}\n"
            "</body>\n"
            "</html>\n"
        )
        html_path.write_text(html_doc, encoding="utf-8")
        return html_path

    @staticmethod
    def _check(id_: str, level: str, name: str, passed: bool, detail: str) -> dict[str, Any]:
        return {"id": id_, "level": level, "name": name, "passed": bool(passed), "detail": detail}

    def _validate_builtin(self, paper_pdf: Path) -> Path:
        md_path = paper_pdf.with_name(f"{paper_pdf.stem}_report.md")
        html_path = paper_pdf.with_name(f"{paper_pdf.stem}_report.html")
        json_path = paper_pdf.with_name(f"{paper_pdf.stem}_validation.json")

        checks: list[dict[str, Any]] = []
        md_exists = md_path.exists()
        html_exists = html_path.exists()
        checks.append(self._check("H001", "hard", "Markdown 文件存在", md_exists, str(md_path)))
        checks.append(self._check("H002", "hard", "HTML 文件存在", html_exists, str(html_path)))

        if not md_exists or not html_exists:
            payload = {
                "timestamp": date.today().isoformat(),
                "input": {
                    "paper_source_path": str(paper_pdf.resolve()),
                    "md_path": str(md_path),
                    "html_path": str(html_path),
                    "json_out": str(json_path),
                },
                "summary": {
                    "total": len(checks),
                    "hard_failed": len([c for c in checks if c["level"] == "hard" and not c["passed"]]),
                    "soft_failed": 0,
                    "passed": False,
                    "failure_ids": [c["id"] for c in checks if c["level"] == "hard" and not c["passed"]],
                },
                "checks": checks,
            }
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return json_path

        md = md_path.read_text(encoding="utf-8")
        html_text = html_path.read_text(encoding="utf-8")
        expected_sections = [
            "1、简短总结",
            "2、核心贡献与新颖性",
            "3、针对的问题",
            "4、分段详解",
            "5、局限性与风险评估",
            "6、后续可能的创新点和改进点",
            "7、复现计划",
        ]
        h1_matches = re.findall(r"(?m)^#\s+(.+)$", md)
        section_order_passed = len(h1_matches) >= 7 and h1_matches[:7] == expected_sections
        actual_preview = " | ".join(h1_matches[:7]) if h1_matches else ""
        checks.append(self._check("H101", "hard", "前7个一级章节顺序固定", section_order_passed, f"actual={actual_preview}"))

        sec4_match = re.search(r"(?ms)^#\s+4、分段详解\s*(.*?)(?=^#\s+\d+、|\Z)", md)
        sec4_body = sec4_match.group(1) if sec4_match else ""
        level2_count = len(re.findall(r"(?m)^##\s+", sec4_body))
        checks.append(self._check("H102", "hard", "第4章含二级标题结构", level2_count >= 1, f"level2_count={level2_count}"))

        slot_keywords = ["小节总结", "深入分析", "术语解释：", "示例或类比"]
        missing = [kw for kw in slot_keywords if kw not in sec4_body]
        slot_detail = "ok" if not missing else "missing=" + ",".join(missing)
        checks.append(self._check("H103", "hard", "第4章含四槽位要素", not missing, slot_detail))

        anchor_count = len(re.findall(r"(?m)^证据锚点：.+$", md))
        checks.append(self._check("H104", "hard", "证据锚点行存在", anchor_count >= 1, f"anchor_count={anchor_count}"))

        term_line_count = len(re.findall(r"(?m)^术语解释：.+$", md))
        checks.append(self._check("H105", "hard", "术语解释行存在", term_line_count >= 1, f"term_line_count={term_line_count}"))

        char_count = len(md)
        checks.append(self._check("H106", "hard", "报告最小篇幅>=4500字符", char_count >= 4500, f"char_count={char_count}"))

        term_lines = [line for line in md.splitlines() if line.startswith("术语解释：")]
        term_lines_with_english = [line for line in term_lines if re.search(r"[A-Za-z]", line)]
        invalid_bilingual = [
            line for line in term_lines_with_english if re.search(r"[A-Za-z][A-Za-z0-9\\-\\+\\./\\s]*（[^）]+）", line) is None
        ]
        bilingual_pass = (not term_lines_with_english) or (not invalid_bilingual)
        checks.append(
            self._check(
                "H107",
                "hard",
                "术语双语格式可检测通过",
                bilingual_pass,
                f"english_lines={len(term_lines_with_english)},invalid={len(invalid_bilingual)}",
            )
        )

        expect_pdf_link = f"./{paper_pdf.name}"
        checks.append(
            self._check(
                "H201",
                "hard",
                "HTML包含原论文链接",
                expect_pdf_link in html_text or re.search(r'<a\\s+href=\"\\./[^\"<>]+\\.pdf\"', html_text) is not None,
                f"expect={expect_pdf_link}",
            )
        )
        accordion_pass = "accordion-trigger" in html_text and "accordion-panel" in html_text
        checks.append(self._check("H202", "hard", "HTML包含手风琴组件", accordion_pass, "need accordion-trigger + accordion-panel"))

        a11y_pass = ("aria-expanded" in html_text) and ("e.key === 'Enter'" in html_text) and ("e.key === ' '" in html_text)
        checks.append(self._check("H203", "hard", "HTML包含可访问性交互", a11y_pass, "need aria-expanded + Enter/Space"))

        empty_panel = re.search(r'<div class=\"accordion-panel\"[^>]*>\\s*<p>\\s*</p>', html_text, flags=re.S) is not None
        checks.append(self._check("H204", "hard", "HTML手风琴无空面板", not empty_panel, f"empty_panel={empty_panel}"))

        extra_sections = max(0, len(h1_matches) - 7)
        checks.append(self._check("S301", "soft", "扩展章节位于第7章后", True, f"extra_h1_count={extra_sections}"))

        hard_failed = [c for c in checks if c["level"] == "hard" and not c["passed"]]
        soft_failed = [c for c in checks if c["level"] == "soft" and not c["passed"]]
        payload = {
            "timestamp": date.today().isoformat(),
            "input": {
                "paper_source_path": str(paper_pdf.resolve()),
                "md_path": str(md_path),
                "html_path": str(html_path),
                "json_out": str(json_path),
            },
            "summary": {
                "total": len(checks),
                "hard_failed": len(hard_failed),
                "soft_failed": len(soft_failed),
                "passed": len(hard_failed) == 0,
                "failure_ids": [c["id"] for c in hard_failed],
            },
            "checks": checks,
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return json_path

    def render_html(self, paper_pdf: Path) -> Path:
        if self.mode == "external":
            self._ensure_external_ready(needs_powershell=True)
            script = self.paper_skill_dir / "scripts" / "render_report_html.ps1"
            shell = _powershell_bin()
            cmd = [shell, "-NoProfile", "-File", str(script), "-paper_source_path", str(paper_pdf)]
            completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "render failed").strip()
                raise RuntimeError(detail)
            return paper_pdf.with_name(f"{paper_pdf.stem}_report.html")
        return self._render_html_builtin(paper_pdf)

    def validate(self, paper_pdf: Path) -> Path:
        if self.mode == "external":
            self._ensure_external_ready(needs_powershell=True)
            script = self.paper_skill_dir / "scripts" / "validate_report.ps1"
            shell = _powershell_bin()
            cmd = [shell, "-NoProfile", "-File", str(script), "-paper_source_path", str(paper_pdf)]
            completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "validate failed").strip()
                raise RuntimeError(detail)
            return paper_pdf.with_name(f"{paper_pdf.stem}_validation.json")
        return self._validate_builtin(paper_pdf)


def today_output_dir() -> Path:
    return repo_root() / "Paper" / "AI4Research" / "daily" / str(date.today())


def write_today_recommendations(output_dir: Path, chosen: list[PaperCandidate], resolved_urls: dict[str, str]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = []
    lines = [f"# 今日论文推荐清单（{date.today()}）", ""]

    for idx, c in enumerate(chosen, start=1):
        url = resolved_urls.get(c.uid, "")
        payload.append(
            {
                "rank": idx,
                "title": c.title,
                "year": c.year,
                "paper_id": c.paper_id,
                "aminer_url": c.aminer_url,
                "pdf_url": url,
            }
        )
        lines.append(f"{idx}. **{c.title}** ({c.year or 'N/A'})")
        lines.append(f"   - AMiner: {c.aminer_url or 'N/A'}")
        lines.append(f"   - PDF: {url or 'N/A'}")

    json_path = output_dir / "today_recommendations.json"
    md_path = output_dir / "today_recommendations.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_topics(topic_text: str) -> list[str]:
    pieces = re.split(r"[,，;；\n]+", topic_text or "")
    topics: list[str] = []
    for p in pieces:
        t = _clean_text(p)
        if t and t not in topics:
            topics.append(t)
    return topics


def select_top_k(candidates: list[PaperCandidate], top_k: int) -> list[PaperCandidate]:
    return candidates[: max(1, int(top_k))]


def choose_filename(rank: int, title: str) -> str:
    return f"{rank:02d}_{_safe_stem(title)}.pdf"


def mask_secret(value: str) -> str:
    v = _clean_text(value)
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * (len(v) - 8) + v[-4:]

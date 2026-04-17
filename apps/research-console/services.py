from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib import parse, request
import xml.etree.ElementTree as ET


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


class RecommenderService:
    def __init__(self, skill_dir: Path, python_bin: str = "python") -> None:
        self.skill_dir = skill_dir
        self.python_bin = python_bin

    def recommend(self, topics: list[str]) -> list[PaperCandidate]:
        if not topics:
            raise RuntimeError("topics 不能为空")

        script = self.skill_dir / "scripts" / "handle_trigger.py"
        if not script.exists():
            raise RuntimeError(f"找不到 AMiner 脚本: {script}")

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
        candidates: list[PaperCandidate] = []
        for idx, paper in enumerate(papers, start=1):
            title = _clean_text(paper.get("title"))
            if not title:
                continue
            year = paper.get("year")
            try:
                year_int = int(year)
            except Exception:
                year_int = 0

            uid = f"{paper.get('paper_id') or idx}"
            candidates.append(
                PaperCandidate(
                    uid=uid,
                    title=title,
                    year=year_int,
                    paper_id=_clean_text(paper.get("paper_id")),
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
    def __init__(self, paper_skill_dir: Path, python_bin: str = "python") -> None:
        self.paper_skill_dir = paper_skill_dir
        self.python_bin = python_bin

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
        spec_path = self.paper_skill_dir / "references" / "output-structure-spec.md"
        if not spec_path.exists():
            raise RuntimeError(f"缺少协议文件: {spec_path}")
        lines = spec_path.read_text(encoding="utf-8").splitlines()
        marker = "## Canonical Prompt（单段主 Prompt）"
        for idx, line in enumerate(lines):
            if line.strip() == marker:
                for j in range(idx + 1, len(lines)):
                    if lines[j].strip():
                        return lines[j].strip()
        raise RuntimeError("未在 output-structure-spec.md 中找到 canonical prompt")

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

    def render_html(self, paper_pdf: Path) -> Path:
        script = self.paper_skill_dir / "scripts" / "render_report_html.ps1"
        if not script.exists():
            raise RuntimeError(f"找不到渲染脚本: {script}")
        shell = _powershell_bin()
        cmd = [shell, "-NoProfile", "-File", str(script), "-paper_source_path", str(paper_pdf)]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "render failed").strip()
            raise RuntimeError(detail)
        return paper_pdf.with_name(f"{paper_pdf.stem}_report.html")

    def validate(self, paper_pdf: Path) -> Path:
        script = self.paper_skill_dir / "scripts" / "validate_report.ps1"
        if not script.exists():
            raise RuntimeError(f"找不到校验脚本: {script}")
        shell = _powershell_bin()
        cmd = [shell, "-NoProfile", "-File", str(script), "-paper_source_path", str(paper_pdf)]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "validate failed").strip()
            raise RuntimeError(detail)
        return paper_pdf.with_name(f"{paper_pdf.stem}_validation.json")


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

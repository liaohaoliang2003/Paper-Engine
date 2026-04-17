from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TaskRecord:
    task_id: str
    name: str
    status: str
    started_at: str
    finished_at: str = ""
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskEngine:
    """Simple in-memory task state machine for Streamlit session use."""

    VALID_STATUS = {"queued", "running", "success", "failed"}

    def __init__(self) -> None:
        self._seq = 0
        self.records: list[TaskRecord] = []
        self.logs: list[str] = []

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _make_task_id(self) -> str:
        self._seq += 1
        return f"T{self._seq:04d}"

    def queue(self, name: str, metadata: dict[str, Any] | None = None) -> str:
        task_id = self._make_task_id()
        record = TaskRecord(
            task_id=task_id,
            name=name,
            status="queued",
            started_at=self._now(),
            metadata=metadata or {},
        )
        self.records.append(record)
        self.log(f"[{task_id}] queued: {name}")
        return task_id

    def start(self, task_id: str) -> None:
        self._set_status(task_id, "running", detail="")
        self.log(f"[{task_id}] running")

    def success(self, task_id: str, detail: str = "") -> None:
        self._set_status(task_id, "success", detail=detail)
        self.log(f"[{task_id}] success {detail}".strip())

    def fail(self, task_id: str, detail: str) -> None:
        self._set_status(task_id, "failed", detail=detail)
        self.log(f"[{task_id}] failed: {detail}")

    def log(self, message: str) -> None:
        self.logs.append(f"{self._now()} {message}")

    def logs_by_stage(self, stage: str, limit: int = 200) -> list[str]:
        token = f"[stage:{stage}]"
        rows = [line for line in self.logs if token in line]
        if limit <= 0:
            return rows
        return rows[-limit:]

    def _set_status(self, task_id: str, status: str, detail: str = "") -> None:
        if status not in self.VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        for rec in self.records:
            if rec.task_id == task_id:
                rec.status = status
                rec.detail = detail
                if status in {"success", "failed"}:
                    rec.finished_at = self._now()
                return
        raise KeyError(f"task not found: {task_id}")

    def as_table_rows(self) -> list[dict[str, str]]:
        return [
            {
                "ID": rec.task_id,
                "任务": rec.name,
                "状态": rec.status,
                "开始时间": rec.started_at,
                "结束时间": rec.finished_at,
                "详情": rec.detail,
            }
            for rec in self.records
        ]

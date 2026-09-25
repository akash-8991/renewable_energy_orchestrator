"""Export worker: on `reo.export.requested`, builds the governed Excel
workbook (FR-EXP-001/002, TR-EXP-01) — Decisions / Signals / Reasoning /
Approvals / Acknowledgements / Export Metadata sheets — and uploads it to
tenant-scoped object storage.

Isolated from the command-processing path by design (a stuck/slow export
must never be able to delay a safety-critical dispatch) — this is a
separate container/process specifically so a large export can't share a
thread pool or event loop with anything on the OT execution path.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time
from datetime import datetime, timezone

import boto3
import openpyxl
from openpyxl.styles import Font
from reo_common.config import get_settings
from reo_common.db import SessionLocal, break_glass_cross_tenant
from reo_common.events import EventBus
from reo_common.models import Action, Approval, Command, Decision, ExportJob, Signal
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s export-worker %(message)s")
log = logging.getLogger("export-worker")
settings = get_settings()

STREAM_EXPORT_REQUESTED = "reo.export.requested"
FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _s3_client():
    return boto3.client(
        "s3", endpoint_url=settings.s3_endpoint_url, aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key, region_name=settings.s3_region,
    )


def _safe_cell(value):
    """Neutralise formula injection (TR-EXP-01): a leading =, +, -, or @ in
    a cell that flows from user/agent-controlled text (reasons, findings,
    connector names, ...) would be interpreted as a formula by Excel/Sheets
    on open. Prefixing with an apostrophe forces it to render as text."""
    if isinstance(value, str) and value and value[0] in FORMULA_TRIGGER_CHARS:
        return "'" + value
    return value


def _autosize(ws) -> None:
    for col_cells in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells)
        ws.column_dimensions[col_cells[0].column_letter].width = min(60, max(10, length + 2))


def _write_sheet(wb, title: str, headers: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title=title[:31])
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([_safe_cell(v) for v in row])
    _autosize(ws)


def build_workbook(db, tenant_id: str, filters: dict) -> tuple[bytes, int]:
    decision_stmt = select(Decision).where(Decision.tenant_id == tenant_id).order_by(Decision.created_at.desc())
    if filters.get("status"):
        decision_stmt = decision_stmt.where(Decision.status == filters["status"])
    if filters.get("limit"):
        decision_stmt = decision_stmt.limit(int(filters["limit"]))
    decisions = db.execute(decision_stmt).scalars().all()
    decision_ids = [d.id for d in decisions]

    actions = db.execute(select(Action).where(Action.decision_id.in_(decision_ids))).scalars().all() if decision_ids else []
    signals = db.execute(select(Signal).where(Signal.action_id.in_([a.id for a in actions]))).scalars().all() if actions else []
    approvals = db.execute(select(Approval).where(Approval.decision_id.in_(decision_ids))).scalars().all() if decision_ids else []
    commands = db.execute(select(Command).where(Command.signal_id.in_([s.id for s in signals]))).scalars().all() if signals else []

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _write_sheet(wb, "Decisions", ["Decision ID", "Cycle ID", "Version", "Trigger", "Status", "Autonomy Mode", "Confidence", "Binding Constraints", "Created At", "Expires At"], [
        [d.id, d.decision_cycle_id, d.version, d.trigger, d.status, d.autonomy_mode, d.confidence, ", ".join(d.binding_constraints or []), d.created_at.isoformat(), d.expires_at.isoformat() if d.expires_at else ""]
        for d in decisions
    ])

    # one row per governed Action ("ticket") — the per-decision plan JSON
    # already carried this, but nowhere summarised every action across every
    # decision in one place; latest Signal/Approval per action gives each
    # row a resolved disposition instead of just the bare Action fields.
    latest_signal_by_action: dict[str, Signal] = {}
    for s in sorted(signals, key=lambda s: s.created_at, reverse=True):
        latest_signal_by_action.setdefault(s.action_id, s)
    latest_approval_by_action: dict[str, Approval] = {}
    for ap in sorted(approvals, key=lambda a: a.created_at, reverse=True):
        if ap.action_id:
            latest_approval_by_action.setdefault(ap.action_id, ap)

    def _ticket_status(action_id: str) -> str:
        sig = latest_signal_by_action.get(action_id)
        appr = latest_approval_by_action.get(action_id)
        if appr is not None and appr.outcome in ("rejected", "expired"):
            return appr.outcome
        if sig is not None:
            if sig.state in ("acknowledged", "reconciled"):
                return "dispatched"
            if sig.state in ("rejected", "timed_out", "cancelled", "rolled_back"):
                return "failed"
            if sig.state in ("queued", "sent", "approved"):
                return "dispatching"
        if appr is not None:
            return appr.outcome
        return "pending"

    _write_sheet(wb, "Actions", ["Action ID", "Decision ID", "Asset ID", "Action Type", "Quantity", "Unit", "Risk Level", "Requires Approval", "Ticket Status", "Reason", "Start Time", "End Time"], [
        [a.id, a.decision_id, a.asset_id or "", a.action_type, a.quantity, a.unit, a.risk_level, a.requires_approval,
         _ticket_status(a.id), a.reason or "", a.start_time.isoformat(), a.end_time.isoformat()]
        for a in actions
    ])

    _write_sheet(wb, "Signals", ["Signal ID", "Correlation ID", "Asset ID", "Command Type", "Setpoint", "Unit", "State", "Idempotency Key", "Created At"], [
        [s.id, s.correlation_id, s.target_asset_id, s.command_type, s.setpoint_value, s.unit, s.state, s.idempotency_key, s.created_at.isoformat()]
        for s in signals
    ])

    reasoning_rows = []
    for d in decisions:
        explanation = (d.reasoning or {}).get("explanation") or {}
        reasoning_rows.append([d.id, explanation.get("situation", ""), explanation.get("selected_plan", ""), explanation.get("reason", ""), explanation.get("trade_offs", ""), explanation.get("uncertainty", ""), "; ".join(d.risk_flags or [])])
    _write_sheet(wb, "Reasoning", ["Decision ID", "Situation", "Selected Plan", "Reason", "Trade-offs", "Uncertainty", "Risk Flags"], reasoning_rows)

    _write_sheet(wb, "Approvals", ["Approval ID", "Decision ID", "Action ID", "Outcome", "Requires Second Approver", "Reason", "Expires At", "Created At"], [
        [a.id, a.decision_id, a.action_id, a.outcome, a.requires_second_approver, a.reason or "", a.expires_at.isoformat(), a.created_at.isoformat()]
        for a in approvals
    ])

    _write_sheet(wb, "Acknowledgements", ["Command ID", "Signal ID", "Adapter", "Prepare Status", "Commit Status", "Ack Status", "Retry Count", "Completed At"], [
        [c.id, c.signal_id, c.adapter, c.prepare_status, c.commit_status, c.ack_status, c.retry_count, c.completed_at.isoformat() if c.completed_at else ""]
        for c in commands
    ])

    _write_sheet(wb, "Export Metadata", ["Field", "Value"], [
        ["Tenant ID", tenant_id],
        ["Generated At (UTC)", datetime.now(timezone.utc).isoformat()],
        ["Filters", str(filters)],
        ["Decision Rows", len(decisions)],
        ["Action Rows", len(actions)],
        ["Signal Rows", len(signals)],
        ["Approval Rows", len(approvals)],
        ["Acknowledgement Rows", len(commands)],
        ["Note", "Formula-leading characters in text cells are escaped to prevent formula injection on open."],
    ])

    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()
    row_count = len(decisions) + len(actions) + len(signals) + len(approvals) + len(commands)
    return content, row_count


def process_export(export_job_id: str) -> None:
    db = SessionLocal()
    try:
        with break_glass_cross_tenant():
            job = db.execute(select(ExportJob).where(ExportJob.id == export_job_id)).scalar_one_or_none()
            if job is None:
                log.warning("export job %s not found", export_job_id)
                return
            job.status = "running"
            db.commit()

            content, row_count = build_workbook(db, job.tenant_id, job.filters or {})
            checksum = hashlib.sha256(content).hexdigest()
            object_key = f"{job.tenant_id}/{export_job_id}.xlsx"

            _s3_client().put_object(
                Bucket=settings.s3_bucket_exports, Key=object_key, Body=content,
                ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            job.status = "complete"
            job.object_key = object_key
            job.checksum_sha256 = checksum
            job.row_count = row_count
            db.commit()
            log.info("export %s complete: %d rows, checksum=%s", export_job_id, row_count, checksum[:12])
    except Exception:
        db.rollback()
        log.exception("export %s failed", export_job_id)
        try:
            with break_glass_cross_tenant():
                job = db.execute(select(ExportJob).where(ExportJob.id == export_job_id)).scalar_one_or_none()
                if job:
                    job.status = "failed"
                    db.commit()
        except Exception:
            log.exception("failed to mark export %s as failed", export_job_id)
    finally:
        db.close()


def main() -> None:
    bus = EventBus()
    bus.ensure_group(STREAM_EXPORT_REQUESTED, "export-worker")
    log.info("export-worker started, watching %s", STREAM_EXPORT_REQUESTED)
    while True:
        events = bus.consume(STREAM_EXPORT_REQUESTED, "export-worker", "worker-1", block_ms=5000)
        for entry_id, event in events:
            export_job_id = event.data.get("export_job_id")
            if export_job_id:
                process_export(export_job_id)
            bus.ack(STREAM_EXPORT_REQUESTED, "export-worker", entry_id)
        if not events:
            time.sleep(1)


if __name__ == "__main__":
    main()

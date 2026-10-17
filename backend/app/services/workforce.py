"""Shifts, attendance (clock in/out), tasks, handover notes and worked-hours report.

Overtime thresholds are parameters, not legal advice: the defaults (45 h/week, 11 h/day)
were not verified against current Turkish labour regulations in this project.
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.permissions import Perm, has_perm
from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import Attendance, HandoverNote, Shift, Task, User, UserBranchAccess
from app.security import Actor
from app.services import audit
from app.services.inventory import check_not_future, now
from app.services.scope import get_active_branch, get_branch

HOUR = Decimal("3600")


def _tz(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise Invalid("timestamps must include a timezone")
    return ts


def _branch_member(
    session: Session, actor: Actor, user_id: uuid.UUID, branch_id: uuid.UUID
) -> User:
    user = session.scalar(select(User).where(User.id == user_id, User.tenant_id == actor.tenant_id))
    if user is None or not user.is_active:
        raise NotFound("user not found")
    if user.role != "owner":
        member = session.scalar(
            select(UserBranchAccess.user_id).where(
                UserBranchAccess.user_id == user.id, UserBranchAccess.branch_id == branch_id
            )
        )
        if member is None:
            raise Invalid("user is not assigned to this branch")
    return user


# --- shifts ---------------------------------------------------------------------------------


def create_shift(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    user_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    note: str | None,
) -> Shift:
    actor.require(Perm.SHIFT_MANAGE)
    get_active_branch(session, actor, branch_id)
    _tz(starts_at)
    _tz(ends_at)
    if ends_at <= starts_at:
        raise Invalid("shift must end after it starts")
    if ends_at - starts_at > timedelta(hours=24):
        raise Invalid("a shift cannot exceed 24 hours")
    # Lock the user row so two concurrent plans cannot both pass the overlap check.
    user = session.scalar(
        select(User).where(User.id == user_id, User.tenant_id == actor.tenant_id).with_for_update()
    )
    if user is None:
        raise NotFound("user not found")
    _branch_member(session, actor, user.id, branch_id)
    overlap = session.scalar(
        select(Shift.id).where(
            Shift.user_id == user.id,
            Shift.cancelled_at.is_(None),
            Shift.starts_at < ends_at,
            Shift.ends_at > starts_at,
        )
    )
    if overlap is not None:
        raise Conflict("the user already has an overlapping shift")
    shift = Shift(
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        user_id=user.id,
        starts_at=starts_at,
        ends_at=ends_at,
        note=note,
        created_by=actor.user_id,
    )
    session.add(shift)
    session.flush()
    audit.record(
        session,
        actor,
        "shift.create",
        "shift",
        shift.id,
        {
            "branch_id": str(branch_id),
            "user_id": str(user.id),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        },
    )
    session.commit()
    return shift


def cancel_shift(session: Session, actor: Actor, shift_id: uuid.UUID) -> Shift:
    actor.require(Perm.SHIFT_MANAGE)
    shift = session.scalar(
        select(Shift).where(Shift.id == shift_id, Shift.tenant_id == actor.tenant_id)
    )
    if shift is None:
        raise NotFound("shift not found")
    get_branch(session, actor, shift.branch_id)
    if shift.cancelled_at is not None:
        raise Conflict("shift is already cancelled")
    shift.cancelled_at = now()
    audit.record(
        session, actor, "shift.cancel", "shift", shift.id, {"branch_id": str(shift.branch_id)}
    )
    session.commit()
    return shift


def list_shifts(
    session: Session, actor: Actor, branch_id: uuid.UUID, start: datetime, end: datetime
) -> list[Shift]:
    """Managers see the branch roster; everyone else sees only their own shifts."""
    get_branch(session, actor, branch_id)
    stmt = (
        select(Shift)
        .where(
            Shift.tenant_id == actor.tenant_id,
            Shift.branch_id == branch_id,
            Shift.starts_at < _tz(end),
            Shift.ends_at > _tz(start),
        )
        .order_by(Shift.starts_at)
    )
    if not has_perm(actor.role, Perm.SHIFT_MANAGE):
        stmt = stmt.where(Shift.user_id == actor.user_id)
    return list(session.scalars(stmt))


# --- attendance -----------------------------------------------------------------------------


def clock_in(session: Session, actor: Actor, branch_id: uuid.UUID) -> Attendance:
    actor.require(Perm.SHIFT_SELF)
    get_active_branch(session, actor, branch_id)
    row = Attendance(
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        user_id=actor.user_id,
        clock_in_at=now(),
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("already clocked in; clock out first") from exc
    audit.record(
        session, actor, "attendance.in", "attendance", row.id, {"branch_id": str(branch_id)}
    )
    session.commit()
    return row


def clock_out(session: Session, actor: Actor) -> Attendance:
    actor.require(Perm.SHIFT_SELF)
    row = session.scalar(
        select(Attendance)
        .where(Attendance.user_id == actor.user_id, Attendance.clock_out_at.is_(None))
        .with_for_update()
    )
    if row is None:
        raise Conflict("not clocked in")
    row.clock_out_at = max(now(), row.clock_in_at + timedelta(microseconds=1))
    audit.record(
        session, actor, "attendance.out", "attendance", row.id, {"branch_id": str(row.branch_id)}
    )
    session.commit()
    return row


def correct_attendance(
    session: Session,
    actor: Actor,
    attendance_id: uuid.UUID,
    clock_in_at: datetime,
    clock_out_at: datetime | None,
    reason: str,
) -> Attendance:
    """Manager correction; previous values stay in the audit trail."""
    actor.require(Perm.SHIFT_MANAGE)
    if not reason.strip():
        raise Invalid("a correction needs a reason")
    row = session.scalar(
        select(Attendance)
        .where(Attendance.id == attendance_id, Attendance.tenant_id == actor.tenant_id)
        .with_for_update()
    )
    if row is None:
        raise NotFound("attendance record not found")
    get_branch(session, actor, row.branch_id)
    if row.user_id == actor.user_id:
        raise Forbidden("you cannot correct your own attendance")
    check_not_future(_tz(clock_in_at))
    if clock_out_at is not None:
        check_not_future(_tz(clock_out_at))
        if clock_out_at <= clock_in_at:
            raise Invalid("clock-out must be after clock-in")
    before = {
        "in": row.clock_in_at.isoformat(),
        "out": row.clock_out_at.isoformat() if row.clock_out_at else None,
    }
    row.clock_in_at = clock_in_at
    row.clock_out_at = clock_out_at
    audit.record(
        session,
        actor,
        "attendance.correct",
        "attendance",
        row.id,
        {
            "branch_id": str(row.branch_id),
            "reason": reason,
            "before": before,
            "after": {
                "in": clock_in_at.isoformat(),
                "out": clock_out_at.isoformat() if clock_out_at else None,
            },
        },
    )
    session.commit()
    return row


def list_attendance(
    session: Session, actor: Actor, branch_id: uuid.UUID, start: datetime, end: datetime
) -> list[Attendance]:
    get_branch(session, actor, branch_id)
    stmt = (
        select(Attendance)
        .where(
            Attendance.tenant_id == actor.tenant_id,
            Attendance.branch_id == branch_id,
            Attendance.clock_in_at < _tz(end),
            (Attendance.clock_out_at.is_(None)) | (Attendance.clock_out_at > _tz(start)),
        )
        .order_by(Attendance.clock_in_at)
    )
    if not has_perm(actor.role, Perm.SHIFT_MANAGE):
        stmt = stmt.where(Attendance.user_id == actor.user_id)
    return list(session.scalars(stmt))


def hours_report(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    start: datetime,
    end: datetime,
    weekly_limit_hours: Decimal,
    daily_limit_hours: Decimal,
) -> dict[str, Any]:
    """Worked hours per user, clipped to the period. Open clock-ins count up to now and are
    flagged. Days/weeks above the given thresholds are listed for review."""
    actor.require(Perm.SHIFT_MANAGE)
    if _tz(end) <= _tz(start):
        raise Invalid("invalid period")
    rows = list_attendance(session, actor, branch_id, start, end)
    per_user: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    per_day: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(Decimal)
    per_week: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(Decimal)
    open_rows = []
    current = now()
    for r in rows:
        a = max(r.clock_in_at, start)
        b = min(r.clock_out_at or current, end)
        if r.clock_out_at is None:
            open_rows.append(str(r.id))
        if b <= a:
            continue
        # split at day boundaries (in the period's timezone) for daily totals
        cursor = a
        while cursor < b:
            local = cursor.astimezone(start.tzinfo)
            next_day = datetime.combine(
                local.date() + timedelta(days=1), datetime.min.time(), start.tzinfo
            )
            seg_end = min(b, next_day)
            hours = Decimal((seg_end - cursor).total_seconds()) / HOUR
            iso = local.isocalendar()
            per_user[r.user_id] += hours
            per_day[(r.user_id, local.date().isoformat())] += hours
            per_week[(r.user_id, f"{iso.year}-W{iso.week:02d}")] += hours
            cursor = seg_end
    q = Decimal("0.01")
    return {
        "branch_id": branch_id,
        "period_start": start,
        "period_end": end,
        "generated_at": current,
        "thresholds": {
            "weekly_hours": weekly_limit_hours,
            "daily_hours": daily_limit_hours,
            "note": "configurable; not a statement of legal compliance",
        },
        "users": [{"user_id": u, "hours": h.quantize(q)} for u, h in sorted(per_user.items())],
        "daily_over_threshold": [
            {"user_id": u, "date": d, "hours": h.quantize(q)}
            for (u, d), h in sorted(per_day.items())
            if h > daily_limit_hours
        ],
        "weekly_over_threshold": [
            {"user_id": u, "week": w, "hours": h.quantize(q)}
            for (u, w), h in sorted(per_week.items())
            if h > weekly_limit_hours
        ],
        "open_clock_ins": open_rows,
    }


# --- tasks and handover ---------------------------------------------------------------------


def create_task(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    title: str,
    due_at: datetime | None,
    assigned_to: uuid.UUID | None,
) -> Task:
    actor.require(Perm.SHIFT_MANAGE)
    get_active_branch(session, actor, branch_id)
    if due_at is not None:
        _tz(due_at)
    if assigned_to is not None:
        _branch_member(session, actor, assigned_to, branch_id)
    task = Task(
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        title=title,
        due_at=due_at,
        assigned_to=assigned_to,
        status="open",
        created_by=actor.user_id,
    )
    session.add(task)
    session.flush()
    audit.record(
        session,
        actor,
        "task.create",
        "task",
        task.id,
        {"branch_id": str(branch_id), "assigned_to": str(assigned_to)},
    )
    session.commit()
    return task


def complete_task(session: Session, actor: Actor, task_id: uuid.UUID) -> Task:
    actor.require(Perm.TASK_COMPLETE)
    task = session.scalar(
        select(Task).where(Task.id == task_id, Task.tenant_id == actor.tenant_id).with_for_update()
    )
    if task is None:
        raise NotFound("task not found")
    get_branch(session, actor, task.branch_id)
    if task.status != "open":
        raise Conflict(f"task is already {task.status}")
    if (
        task.assigned_to is not None
        and task.assigned_to != actor.user_id
        and not has_perm(actor.role, Perm.SHIFT_MANAGE)
    ):
        raise Forbidden("task is assigned to someone else")
    task.status = "done"
    task.completed_by = actor.user_id
    task.completed_at = now()
    audit.record(
        session, actor, "task.complete", "task", task.id, {"branch_id": str(task.branch_id)}
    )
    session.commit()
    return task


def list_tasks(
    session: Session, actor: Actor, branch_id: uuid.UUID, status: str | None
) -> list[Task]:
    get_branch(session, actor, branch_id)
    stmt = (
        select(Task)
        .where(Task.tenant_id == actor.tenant_id, Task.branch_id == branch_id)
        .order_by(Task.due_at.nulls_last(), Task.created_at)
    )
    if status:
        stmt = stmt.where(Task.status == status)
    return list(session.scalars(stmt))


def task_summary(session: Session, actor: Actor, branch_id: uuid.UUID) -> dict[str, Any]:
    tasks = list_tasks(session, actor, branch_id, None)
    current = now()
    due = [
        t for t in tasks if t.due_at is not None and t.due_at <= current and t.status != "cancelled"
    ]
    done_on_time = [
        t
        for t in due
        if t.status == "done"
        and t.completed_at is not None
        and t.due_at is not None
        and t.completed_at <= t.due_at
    ]
    return {
        "open": sum(t.status == "open" for t in tasks),
        "overdue": sum(
            t.status == "open" and t.due_at is not None and t.due_at < current for t in tasks
        ),
        "due_so_far": len(due),
        "done_on_time": len(done_on_time),
        "on_time_pct": (
            (Decimal(len(done_on_time)) / len(due) * 100).quantize(Decimal("0.01")) if due else None
        ),
    }


def add_handover(session: Session, actor: Actor, branch_id: uuid.UUID, note: str) -> HandoverNote:
    actor.require(Perm.SHIFT_SELF)
    get_active_branch(session, actor, branch_id)
    if not note.strip():
        raise Invalid("note is empty")
    row = HandoverNote(
        tenant_id=actor.tenant_id, branch_id=branch_id, author_id=actor.user_id, note=note
    )
    session.add(row)
    session.flush()
    audit.record(
        session, actor, "handover.create", "handover_note", row.id, {"branch_id": str(branch_id)}
    )
    session.commit()
    return row


def list_handovers(session: Session, actor: Actor, branch_id: uuid.UUID) -> list[HandoverNote]:
    get_branch(session, actor, branch_id)
    return list(
        session.scalars(
            select(HandoverNote)
            .where(HandoverNote.tenant_id == actor.tenant_id, HandoverNote.branch_id == branch_id)
            .order_by(HandoverNote.created_at.desc())
            .limit(100)
        )
    )

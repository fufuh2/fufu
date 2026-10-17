"""Quality control: checklist templates, immutable runs, nonconformities, branch scores."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.permissions import Perm
from app.errors import Conflict, Invalid, NotFound
from app.models import (
    CHECKLIST_KINDS,
    ChecklistAnswer,
    ChecklistItem,
    ChecklistRun,
    ChecklistTemplate,
    Nonconformity,
    User,
)
from app.security import Actor
from app.services import audit
from app.services.inventory import check_not_future, now
from app.services.scope import get_active_branch, get_branch


@dataclass(frozen=True)
class TemplateItemIn:
    label: str
    answer_type: str  # check | number
    unit: str | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    evidence_required: bool = False


def create_template(
    session: Session,
    actor: Actor,
    *,
    name: str,
    kind: str,
    frequency: str,
    items: list[TemplateItemIn],
) -> ChecklistTemplate:
    actor.require(Perm.QUALITY_MANAGE)
    if kind not in CHECKLIST_KINDS:
        raise Invalid(f"kind must be one of {list(CHECKLIST_KINDS)}")
    if frequency not in ("daily", "adhoc"):
        raise Invalid("frequency must be daily or adhoc")
    if not items:
        raise Invalid("template has no items")
    for it in items:
        if it.answer_type not in ("check", "number"):
            raise Invalid("answer_type must be check or number")
        if it.answer_type == "check" and (it.min_value is not None or it.max_value is not None):
            raise Invalid("only number items can have a range")
        if it.min_value is not None and it.max_value is not None and it.min_value > it.max_value:
            raise Invalid("min_value is greater than max_value")
    template = ChecklistTemplate(
        tenant_id=actor.tenant_id, name=name, kind=kind, frequency=frequency
    )
    session.add(template)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("template name already exists") from exc
    for pos, it in enumerate(items):
        session.add(
            ChecklistItem(
                template_id=template.id,
                position=pos,
                label=it.label,
                answer_type=it.answer_type,
                unit=it.unit,
                min_value=it.min_value,
                max_value=it.max_value,
                evidence_required=it.evidence_required,
            )
        )
    audit.record(
        session,
        actor,
        "quality.template.create",
        "checklist_template",
        template.id,
        {"name": name, "items": len(items)},
    )
    session.commit()
    return template


def get_template(
    session: Session, actor: Actor, template_id: uuid.UUID, *, active: bool = True
) -> ChecklistTemplate:
    template = session.scalar(
        select(ChecklistTemplate).where(
            ChecklistTemplate.id == template_id, ChecklistTemplate.tenant_id == actor.tenant_id
        )
    )
    if template is None or (active and not template.is_active):
        raise NotFound("checklist template not found or inactive")
    return template


def template_items(session: Session, template_id: uuid.UUID) -> list[ChecklistItem]:
    return list(
        session.scalars(
            select(ChecklistItem)
            .where(ChecklistItem.template_id == template_id)
            .order_by(ChecklistItem.position)
        )
    )


def list_templates(session: Session, actor: Actor) -> list[ChecklistTemplate]:
    return list(
        session.scalars(
            select(ChecklistTemplate)
            .where(ChecklistTemplate.tenant_id == actor.tenant_id)
            .order_by(ChecklistTemplate.name)
        )
    )


def set_template_active(
    session: Session, actor: Actor, template_id: uuid.UUID, is_active: bool
) -> ChecklistTemplate:
    actor.require(Perm.QUALITY_MANAGE)
    template = get_template(session, actor, template_id, active=False)
    template.is_active = is_active
    audit.record(
        session,
        actor,
        "quality.template.update",
        "checklist_template",
        template.id,
        {"is_active": is_active},
    )
    session.commit()
    return template


@dataclass(frozen=True)
class AnswerIn:
    item_id: int
    checked: bool | None = None
    value: Decimal | None = None
    evidence_ref: str | None = None
    note: str | None = None


def record_run(
    session: Session,
    actor: Actor,
    branch_id: uuid.UUID,
    template_id: uuid.UUID,
    answers: list[AnswerIn],
    performed_at: datetime | None,
    corrects_run_id: uuid.UUID | None = None,
    correction_reason: str | None = None,
) -> ChecklistRun:
    """Every template item must be answered. A failed item opens a nonconformity
    (not for corrections: the original run already opened one)."""
    actor.require(Perm.QUALITY_RECORD)
    get_active_branch(session, actor, branch_id)
    template = get_template(session, actor, template_id)
    items = {it.id: it for it in template_items(session, template.id)}
    given = {a.item_id: a for a in answers}
    if len(given) != len(answers):
        raise Invalid("an item is answered more than once")
    if set(given) != set(items):
        raise Invalid("every checklist item must be answered exactly once")
    when = check_not_future(performed_at or now())
    if corrects_run_id is not None:
        original = session.scalar(
            select(ChecklistRun).where(
                ChecklistRun.id == corrects_run_id,
                ChecklistRun.tenant_id == actor.tenant_id,
                ChecklistRun.branch_id == branch_id,
                ChecklistRun.template_id == template.id,
            )
        )
        if original is None:
            raise NotFound("run to correct not found")
        if not (correction_reason and correction_reason.strip()):
            raise Invalid("a correction needs a reason")
    evaluated = []
    for item_id, it in items.items():
        a = given[item_id]
        if it.answer_type == "check":
            if a.checked is None or a.value is not None:
                raise Invalid(f"item '{it.label}' needs checked=true/false")
            passed = a.checked
        else:
            if a.value is None or a.checked is not None:
                raise Invalid(f"item '{it.label}' needs a numeric value")
            passed = (it.min_value is None or a.value >= it.min_value) and (
                it.max_value is None or a.value <= it.max_value
            )
        if it.evidence_required and not a.evidence_ref:
            raise Invalid(f"item '{it.label}' requires evidence")
        evaluated.append((it, a, passed))
    run = ChecklistRun(
        tenant_id=actor.tenant_id,
        branch_id=branch_id,
        template_id=template.id,
        performed_by=actor.user_id,
        performed_at=when,
        passed=all(p for _, _, p in evaluated),
        corrects_run_id=corrects_run_id,
        correction_reason=correction_reason if corrects_run_id else None,
    )
    session.add(run)
    session.flush()
    opened = 0
    for it, a, passed in evaluated:
        answer = ChecklistAnswer(
            run_id=run.id,
            item_id=it.id,
            checked=a.checked,
            value=a.value,
            passed=passed,
            evidence_ref=a.evidence_ref,
            note=a.note,
        )
        session.add(answer)
        session.flush()
        if not passed and corrects_run_id is None:
            detail = f"{template.name}: {it.label}"
            if a.value is not None:
                detail += f" = {a.value}{' ' + it.unit if it.unit else ''}"
            session.add(
                Nonconformity(
                    tenant_id=actor.tenant_id,
                    branch_id=branch_id,
                    answer_id=answer.id,
                    description=detail[:500],
                    status="open",
                )
            )
            opened += 1
    audit.record(
        session,
        actor,
        "quality.run",
        "checklist_run",
        run.id,
        {
            "branch_id": str(branch_id),
            "template_id": str(template.id),
            "passed": run.passed,
            "nonconformities": opened,
            "corrects": str(corrects_run_id) if corrects_run_id else None,
        },
    )
    session.commit()
    return run


def run_answers(session: Session, run_id: uuid.UUID) -> list[ChecklistAnswer]:
    return list(
        session.scalars(
            select(ChecklistAnswer)
            .where(ChecklistAnswer.run_id == run_id)
            .order_by(ChecklistAnswer.item_id)
        )
    )


def list_runs(
    session: Session, actor: Actor, branch_id: uuid.UUID, start: datetime, end: datetime
) -> list[ChecklistRun]:
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    return list(
        session.scalars(
            select(ChecklistRun)
            .where(
                ChecklistRun.tenant_id == actor.tenant_id,
                ChecklistRun.branch_id == branch_id,
                ChecklistRun.performed_at >= start,
                ChecklistRun.performed_at < end,
            )
            .order_by(ChecklistRun.performed_at)
        )
    )


def _get_nc(session: Session, actor: Actor, nc_id: uuid.UUID) -> Nonconformity:
    nc = session.scalar(
        select(Nonconformity)
        .where(Nonconformity.id == nc_id, Nonconformity.tenant_id == actor.tenant_id)
        .with_for_update()
    )
    if nc is None:
        raise NotFound("nonconformity not found")
    get_branch(session, actor, nc.branch_id)
    return nc


def assign_nonconformity(
    session: Session,
    actor: Actor,
    nc_id: uuid.UUID,
    responsible_user_id: uuid.UUID,
    due_at: datetime,
) -> Nonconformity:
    actor.require(Perm.QUALITY_MANAGE)
    nc = _get_nc(session, actor, nc_id)
    if nc.status != "open":
        raise Conflict("nonconformity is already resolved")
    if due_at.tzinfo is None:
        raise Invalid("timestamps must include a timezone")
    user = session.scalar(
        select(User).where(User.id == responsible_user_id, User.tenant_id == actor.tenant_id)
    )
    if user is None or not user.is_active:
        raise NotFound("responsible user not found")
    nc.responsible_user_id = user.id
    nc.due_at = due_at
    audit.record(
        session,
        actor,
        "quality.nc.assign",
        "nonconformity",
        nc.id,
        {"branch_id": str(nc.branch_id), "responsible": str(user.id), "due_at": due_at.isoformat()},
    )
    session.commit()
    return nc


def resolve_nonconformity(
    session: Session, actor: Actor, nc_id: uuid.UUID, corrective_action: str
) -> Nonconformity:
    actor.require(Perm.QUALITY_RECORD)
    nc = _get_nc(session, actor, nc_id)
    if nc.status != "open":
        raise Conflict("nonconformity is already resolved")
    if not corrective_action.strip():
        raise Invalid("corrective action is required")
    nc.status = "resolved"
    nc.corrective_action = corrective_action
    nc.resolved_at = now()
    nc.resolved_by = actor.user_id
    audit.record(
        session,
        actor,
        "quality.nc.resolve",
        "nonconformity",
        nc.id,
        {"branch_id": str(nc.branch_id), "corrective_action": corrective_action},
    )
    session.commit()
    return nc


def list_nonconformities(
    session: Session, actor: Actor, branch_id: uuid.UUID, status: str | None
) -> list[Nonconformity]:
    actor.require(Perm.STOCK_READ)
    get_branch(session, actor, branch_id)
    stmt = (
        select(Nonconformity)
        .where(Nonconformity.tenant_id == actor.tenant_id, Nonconformity.branch_id == branch_id)
        .order_by(Nonconformity.created_at)
    )
    if status:
        stmt = stmt.where(Nonconformity.status == status)
    return list(session.scalars(stmt))


def score(
    session: Session, actor: Actor, branch_id: uuid.UUID, start: datetime, end: datetime
) -> dict[str, Any]:
    """Pass rate of answers (corrected runs replaced by their latest correction), open and
    overdue nonconformities, and days on which a daily checklist was not done."""
    actor.require(Perm.REPORT_READ)
    get_branch(session, actor, branch_id)
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise Invalid("invalid period")
    runs = list_runs(session, actor, branch_id, start, end)
    superseded = {r.corrects_run_id for r in runs if r.corrects_run_id}
    effective = [r for r in runs if r.id not in superseded]
    total = passed = 0
    for r in effective:
        for a in run_answers(session, r.id):
            total += 1
            passed += int(a.passed)
    open_nc = (
        session.scalar(
            select(func.count())
            .select_from(Nonconformity)
            .where(Nonconformity.branch_id == branch_id, Nonconformity.status == "open")
        )
        or 0
    )
    overdue = (
        session.scalar(
            select(func.count())
            .select_from(Nonconformity)
            .where(
                Nonconformity.branch_id == branch_id,
                Nonconformity.status == "open",
                Nonconformity.due_at < now(),
            )
        )
        or 0
    )
    missed: list[dict[str, Any]] = []
    for t in session.scalars(
        select(ChecklistTemplate).where(
            ChecklistTemplate.tenant_id == actor.tenant_id,
            ChecklistTemplate.is_active,
            ChecklistTemplate.frequency == "daily",
        )
    ):
        done_days = {r.performed_at.date() for r in effective if r.template_id == t.id}
        day = start.date()
        while datetime.combine(day, datetime.min.time(), start.tzinfo) < end:
            if day not in done_days and day <= now().date():
                missed.append({"template_id": t.id, "name": t.name, "date": day.isoformat()})
            day += timedelta(days=1)
    return {
        "branch_id": branch_id,
        "period_start": start,
        "period_end": end,
        "generated_at": now(),
        "answers_total": total,
        "answers_passed": passed,
        # Unknown, not 100 %, when nothing was checked.
        "pass_rate_pct": (
            (Decimal(passed) / total * 100).quantize(Decimal("0.01")) if total else None
        ),
        "open_nonconformities": open_nc,
        "overdue_nonconformities": overdue,
        "missed_daily_checklists": missed,
    }

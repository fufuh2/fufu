from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, new_tenant, setup_kitchen

NOW = datetime.now(UTC)


def _period(days: int = 1) -> dict[str, str]:
    now = datetime.now(UTC)
    return {
        "from": (now - timedelta(days=days)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
    }


def _fridge_template(api: Api) -> dict:
    res = api.post(
        "/checklist-templates",
        {
            "name": "Opening",
            "kind": "opening",
            "frequency": "daily",
            "items": [
                {"label": "Floor clean", "answer_type": "check"},
                {
                    "label": "Fridge temperature",
                    "answer_type": "number",
                    "unit": "C",
                    "min_value": "0",
                    "max_value": "4",
                },
            ],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _answers(t: dict, clean: bool, temp: str) -> list[dict]:
    a, b = t["items"]
    return [{"item_id": a["id"], "checked": clean}, {"item_id": b["id"], "value": temp}]


def test_checklist_run_opens_nonconformity_and_resolves(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    t = _fridge_template(api)
    run = api.post(
        f"/branches/{k['branch']}/checklist-runs",
        {"template_id": t["id"], "answers": _answers(t, True, "7.5")},
    )
    assert run.status_code == 201, run.text
    assert run.json()["passed"] is False
    ncs = api.get(f"/branches/{k['branch']}/nonconformities", params={"status": "open"}).json()
    assert len(ncs) == 1 and "Fridge temperature = 7.5 C" in ncs[0]["description"]
    owner_id = session.execute(text("SELECT id FROM users")).scalar()
    due = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
    assert (
        api.post(
            f"/nonconformities/{ncs[0]['id']}/assign",
            {"responsible_user_id": str(owner_id), "due_at": due},
        ).status_code
        == 200
    )
    res = api.post(
        f"/nonconformities/{ncs[0]['id']}/resolve",
        {"corrective_action": "Thermostat reset, rechecked 3 C"},
    )
    assert res.json()["status"] == "resolved"
    assert (
        api.post(f"/nonconformities/{ncs[0]['id']}/resolve", {"corrective_action": "x"}).status_code
        == 409
    )
    score = api.get(f"/branches/{k['branch']}/reports/quality", params=_period()).json()
    assert (score["answers_total"], score["answers_passed"], score["pass_rate_pct"]) == (
        2,
        1,
        "50.00",
    )


def test_runs_are_immutable_and_corrections_supersede(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    t = _fridge_template(api)
    first = api.post(
        f"/branches/{k['branch']}/checklist-runs",
        {"template_id": t["id"], "answers": _answers(t, True, "40")},
    ).json()
    for stmt in ("UPDATE checklist_runs SET passed = true", "DELETE FROM checklist_answers"):
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(text(stmt))
        session.rollback()
    no_reason = api.post(
        f"/branches/{k['branch']}/checklist-runs",
        {"template_id": t["id"], "corrects_run_id": first["id"], "answers": _answers(t, True, "4")},
    )
    assert no_reason.status_code == 422
    fix = api.post(
        f"/branches/{k['branch']}/checklist-runs",
        {
            "template_id": t["id"],
            "corrects_run_id": first["id"],
            "correction_reason": "typo: 4.0 entered as 40",
            "answers": _answers(t, True, "4"),
        },
    )
    assert fix.status_code == 201 and fix.json()["passed"] is True
    score = api.get(f"/branches/{k['branch']}/reports/quality", params=_period()).json()
    assert score["pass_rate_pct"] == "100.00"  # original superseded
    # the original run is still there for the audit trail
    runs = api.get(f"/branches/{k['branch']}/checklist-runs", params=_period()).json()
    assert len(runs) == 2


def test_checklist_validation(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    t = _fridge_template(api)
    a, b = t["items"]
    url = f"/branches/{k['branch']}/checklist-runs"
    assert (
        api.post(
            url, {"template_id": t["id"], "answers": [{"item_id": a["id"], "checked": True}]}
        ).status_code
        == 422
    )
    assert (
        api.post(
            url,
            {
                "template_id": t["id"],
                "answers": [{"item_id": a["id"], "value": "1"}, {"item_id": b["id"], "value": "1"}],
            },
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/checklist-templates",
            {
                "name": "Bad",
                "kind": "opening",
                "items": [{"label": "x", "answer_type": "check", "min_value": "1"}],
            },
        ).status_code
        == 422
    )
    ev = api.post(
        "/checklist-templates",
        {
            "name": "Oil",
            "kind": "oil",
            "items": [
                {
                    "label": "Polar %",
                    "answer_type": "number",
                    "max_value": "24",
                    "evidence_required": True,
                }
            ],
        },
    ).json()
    assert (
        api.post(
            url,
            {
                "template_id": ev["id"],
                "answers": [{"item_id": ev["items"][0]["id"], "value": "20"}],
            },
        ).status_code
        == 422
    )
    assert (
        api.post(
            url,
            {
                "template_id": ev["id"],
                "answers": [
                    {"item_id": ev["items"][0]["id"], "value": "20", "evidence_ref": "photo-123"}
                ],
            },
        ).status_code
        == 201
    )


def test_missed_daily_checklist_is_reported(client, session: Session) -> None:
    api = new_tenant(session, client)
    k = setup_kitchen(api)
    _fridge_template(api)
    score = api.get(f"/branches/{k['branch']}/reports/quality", params=_period()).json()
    assert score["pass_rate_pct"] is None  # nothing checked -> unknown, not 100
    assert len(score["missed_daily_checklists"]) >= 1


def test_shifts_overlap_and_visibility(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    uid = owner.post(
        "/users",
        {
            "email": "cook@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [k["branch"]],
        },
    ).json()["id"]
    s0 = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    url = f"/branches/{k['branch']}/shifts"
    ok = owner.post(
        url,
        {
            "user_id": uid,
            "starts_at": s0.isoformat(),
            "ends_at": (s0 + timedelta(hours=8)).isoformat(),
        },
    )
    assert ok.status_code == 201, ok.text
    clash = owner.post(
        url,
        {
            "user_id": uid,
            "starts_at": (s0 + timedelta(hours=7)).isoformat(),
            "ends_at": (s0 + timedelta(hours=10)).isoformat(),
        },
    )
    assert clash.status_code == 409
    back_to_back = owner.post(
        url,
        {
            "user_id": uid,
            "starts_at": (s0 + timedelta(hours=8)).isoformat(),
            "ends_at": (s0 + timedelta(hours=9)).isoformat(),
        },
    )
    assert back_to_back.status_code == 201
    assert (
        owner.post(
            url,
            {
                "user_id": uid,
                "starts_at": s0.isoformat(),
                "ends_at": (s0 + timedelta(hours=25)).isoformat(),
            },
        ).status_code
        == 422
    )
    other_branch = owner.post("/branches", {"name": "Other"}).json()["id"]
    assert (
        owner.post(
            f"/branches/{other_branch}/shifts",
            {
                "user_id": uid,
                "starts_at": (s0 + timedelta(days=3)).isoformat(),
                "ends_at": (s0 + timedelta(days=3, hours=1)).isoformat(),
            },
        ).status_code
        == 422
    )
    cook = Api(client, "cook@example.com")
    assert (
        cook.post(
            url, {"user_id": uid, "starts_at": s0.isoformat(), "ends_at": s0.isoformat()}
        ).status_code
        == 403
    )
    period = {"from": s0.isoformat(), "to": (s0 + timedelta(days=1)).isoformat()}
    assert len(cook.get(url, params=period).json()) == 2


def test_attendance_and_hours(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    owner.post(
        "/users",
        {
            "email": "cook@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [k["branch"]],
        },
    )
    cook = Api(client, "cook@example.com")
    a = cook.post(f"/branches/{k['branch']}/attendance/clock-in")
    assert a.status_code == 201
    assert cook.post(f"/branches/{k['branch']}/attendance/clock-in").status_code == 409
    assert cook.post("/attendance/clock-out").status_code == 200
    assert cook.post("/attendance/clock-out").status_code == 409
    # manager corrects to a 12-hour day; the cook cannot correct their own record
    start = (datetime.now(UTC) - timedelta(days=2)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    body = {
        "clock_in_at": start.isoformat(),
        "clock_out_at": (start + timedelta(hours=12)).isoformat(),
        "reason": "forgot to clock out",
    }
    assert (
        cook.client.patch(
            f"/api/v1/attendance/{a.json()['id']}", json=body, headers=cook.headers
        ).status_code
        == 403
    )
    res = owner.client.patch(
        f"/api/v1/attendance/{a.json()['id']}", json=body, headers=owner.headers
    )
    assert res.status_code == 200, res.text
    period = {
        "from": (start - timedelta(hours=6)).isoformat(),
        "to": (start + timedelta(days=1)).isoformat(),
    }
    rep = owner.get(f"/branches/{k['branch']}/reports/hours", params=period).json()
    assert rep["users"][0]["hours"] == "12.00"
    assert rep["daily_over_threshold"][0]["hours"] == "12.00"
    assert cook.get(f"/branches/{k['branch']}/reports/hours", params=period).status_code == 403
    ev = session.execute(
        text("SELECT data FROM audit_events WHERE action='attendance.correct'")
    ).scalar()
    assert ev["reason"] == "forgot to clock out" and ev["before"]["out"] is not None


def test_tasks_and_handover(client, session: Session) -> None:
    owner = new_tenant(session, client)
    k = setup_kitchen(owner)
    a = owner.post(
        "/users",
        {
            "email": "a@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [k["branch"]],
        },
    ).json()["id"]
    owner.post(
        "/users",
        {
            "email": "b@example.com",
            "password": PASSWORD,
            "role": "kitchen",
            "branch_ids": [k["branch"]],
        },
    )
    due = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    t = owner.post(
        f"/branches/{k['branch']}/tasks", {"title": "Clean fryer", "due_at": due, "assigned_to": a}
    ).json()
    assert Api(client, "b@example.com").post(f"/tasks/{t['id']}/complete").status_code == 403
    summary = owner.get(f"/branches/{k['branch']}/reports/tasks").json()
    assert (summary["open"], summary["overdue"]) == (1, 1)
    done = Api(client, "a@example.com").post(f"/tasks/{t['id']}/complete")
    assert done.status_code == 200 and done.json()["status"] == "done"
    summary = owner.get(f"/branches/{k['branch']}/reports/tasks").json()
    assert (summary["done_on_time"], summary["on_time_pct"]) == (0, "0.00")  # completed late
    h = Api(client, "a@example.com").post(
        f"/branches/{k['branch']}/handovers", {"note": "Fryer 2 leaking"}
    )
    assert h.status_code == 201
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM handover_notes"))
    session.rollback()


def test_quality_and_workforce_tenant_isolation(client, session: Session) -> None:
    a = new_tenant(session, client, "A")
    k = setup_kitchen(a)
    t = _fridge_template(a)
    run = a.post(
        f"/branches/{k['branch']}/checklist-runs",
        {"template_id": t["id"], "answers": _answers(t, False, "9")},
    )
    assert run.status_code == 201
    nc = a.get(f"/branches/{k['branch']}/nonconformities").json()[0]
    b = new_tenant(session, client, "B")
    bb = b.post("/branches", {"name": "B"}).json()["id"]
    assert b.get("/checklist-templates").json() == []
    assert (
        b.post(
            f"/branches/{bb}/checklist-runs",
            {"template_id": t["id"], "answers": _answers(t, True, "1")},
        ).status_code
        == 404
    )
    assert (
        b.post(f"/nonconformities/{nc['id']}/resolve", {"corrective_action": "x"}).status_code
        == 404
    )
    a_owner = session.execute(
        text("SELECT u.id FROM users u JOIN tenants t ON t.id=u.tenant_id WHERE t.name='A'")
    ).scalar()
    assert (
        b.post(
            f"/branches/{bb}/shifts",
            {
                "user_id": str(a_owner),
                "starts_at": NOW.isoformat(),
                "ends_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        ).status_code
        == 404
    )

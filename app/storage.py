"""Service / persistence operations with optimistic revision checks."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from . import db
from .errors import ApiError, NotFound, StaleRevision
from .instance import parse_instance
from .solver import check_facts
from .taxonomy import parse_package


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------- packages
def create_package(name: str, xsd: bytes, calc: bytes) -> dict:
    info = parse_package(xsd, calc)
    conn = db.get_conn()
    pid = _new_id("pkg")
    now = _now()
    with conn:
        conn.execute(
            "INSERT INTO packages(id, name, latest_rev, created_at, updated_at) "
            "VALUES (?,?,1,?,?)",
            (pid, name, now, now),
        )
        conn.execute(
            "INSERT INTO package_revisions(package_id, revision, xsd_raw, calc_raw, "
            "target_ns, prefix, concepts_json, equations_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, 1, xsd, calc, info["target_ns"], info["prefix"],
             json.dumps(info["concepts"]), json.dumps(info["equations"]), now),
        )
    return get_package(pid)


def revise_package(pid: str, expected_revision: int, xsd: bytes, calc: bytes) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT latest_rev FROM packages WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise NotFound(f"package '{pid}' not found")
    if row["latest_rev"] != expected_revision:
        raise StaleRevision(
            f"expected package revision {expected_revision}, server has {row['latest_rev']}"
        )
    info = parse_package(xsd, calc)
    new_rev = expected_revision + 1
    now = _now()
    with conn:
        conn.execute(
            "INSERT INTO package_revisions(package_id, revision, xsd_raw, calc_raw, "
            "target_ns, prefix, concepts_json, equations_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, new_rev, xsd, calc, info["target_ns"], info["prefix"],
             json.dumps(info["concepts"]), json.dumps(info["equations"]), now),
        )
        conn.execute(
            "UPDATE packages SET latest_rev=?, updated_at=? WHERE id=?",
            (new_rev, now, pid),
        )
    return get_package(pid)


def _package_revision_row(pid: str, revision: int) -> dict:
    row = db.get_conn().execute(
        "SELECT * FROM package_revisions WHERE package_id=? AND revision=?",
        (pid, revision),
    ).fetchone()
    if row is None:
        raise NotFound(f"package '{pid}' revision {revision} not found")
    return dict(row)


def get_package(pid: str) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise NotFound(f"package '{pid}' not found")
    rev = _package_revision_row(pid, row["latest_rev"])
    return {
        "package_id": row["id"],
        "name": row["name"],
        "latest_revision": row["latest_rev"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "latest": {
            "package_id": pid,
            "revision": rev["revision"],
            "target_ns": rev["target_ns"],
            "prefix": rev["prefix"],
            "concepts": json.loads(rev["concepts_json"]),
            "equations": json.loads(rev["equations_json"]),
            "created_at": rev["created_at"],
        },
    }


def list_packages() -> list[dict]:
    rows = db.get_conn().execute("SELECT id FROM packages ORDER BY created_at").fetchall()
    return [get_package(r["id"]) for r in rows]


# ---------------------------------------------------------------- instances
def create_instance(pid: str, raw: bytes) -> dict:
    conn = db.get_conn()
    if conn.execute("SELECT 1 FROM packages WHERE id=?", (pid,)).fetchone() is None:
        raise NotFound(f"package '{pid}' not found")
    pkg = get_package(pid)
    info = parse_instance(raw, pkg["latest"])
    iid = _new_id("inst")
    now = _now()
    with conn:
        conn.execute(
            "INSERT INTO instances(id, package_id, latest_rev, created_at, updated_at) "
            "VALUES (?,?,1,?,?)",
            (iid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO instance_revisions(instance_id, revision, instance_raw, entity, "
            "period, currency, facts_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (iid, 1, raw, info["entity"], info["period"], info["currency"],
             json.dumps(info["facts"]), now),
        )
    return get_instance(iid)


def revise_instance(iid: str, expected_package_revision: int, raw: bytes) -> dict:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT package_id, latest_rev FROM instances WHERE id=?", (iid,)
    ).fetchone()
    if row is None:
        raise NotFound(f"instance '{iid}' not found")
    pkg = get_package(row["package_id"])
    # 受理时校验两侧所见修订：实例提交必须针对当前 package 修订。
    if pkg["latest_revision"] != expected_package_revision:
        raise StaleRevision(
            f"expected package revision {expected_package_revision}, "
            f"server has {pkg['latest_revision']}"
        )
    info = parse_instance(raw, pkg["latest"])
    new_rev = row["latest_rev"] + 1
    now = _now()
    with conn:
        conn.execute(
            "INSERT INTO instance_revisions(instance_id, revision, instance_raw, entity, "
            "period, currency, facts_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (iid, new_rev, raw, info["entity"], info["period"], info["currency"],
             json.dumps(info["facts"]), now),
        )
        conn.execute(
            "UPDATE instances SET latest_rev=?, updated_at=? WHERE id=?",
            (new_rev, now, iid),
        )
    return get_instance(iid)


def _instance_revision_row(iid: str, revision: int) -> dict:
    row = db.get_conn().execute(
        "SELECT * FROM instance_revisions WHERE instance_id=? AND revision=?",
        (iid, revision),
    ).fetchone()
    if row is None:
        raise NotFound(f"instance '{iid}' revision {revision} not found")
    return dict(row)


def get_instance(iid: str) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM instances WHERE id=?", (iid,)).fetchone()
    if row is None:
        raise NotFound(f"instance '{iid}' not found")
    rev = _instance_revision_row(iid, row["latest_rev"])
    return {
        "instance_id": row["id"],
        "package_id": row["package_id"],
        "latest_revision": row["latest_rev"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "latest": {
            "instance_id": iid,
            "package_id": row["package_id"],
            "revision": rev["revision"],
            "entity": rev["entity"],
            "period": rev["period"],
            "currency": rev["currency"],
            "facts": json.loads(rev["facts_json"]),
            "created_at": rev["created_at"],
        },
    }


def list_instances(pid: str | None = None) -> list[dict]:
    conn = db.get_conn()
    if pid:
        rows = conn.execute(
            "SELECT id FROM instances WHERE package_id=? ORDER BY created_at", (pid,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT id FROM instances ORDER BY created_at").fetchall()
    return [get_instance(r["id"]) for r in rows]


# ---------------------------------------------------------------- reports
def _exact_facts_for_revision(i_rev: dict, p_rev: dict) -> list[dict]:
    """Recover exact decimal facts from the immutable raw instance snapshot.

    ``facts_json`` is a convenience cache; the original XML is the source of
    truth (and is never overwritten).  Re-parsing it for every check means a
    report is always decided from the original decimal amounts, even for
    revisions stored before exact amounts were kept in the JSON column.
    """
    try:
        package_info = parse_package(
            bytes(p_rev["xsd_raw"]), bytes(p_rev["calc_raw"])
        )
        return parse_instance(bytes(i_rev["instance_raw"]), package_info)["facts"]
    except (KeyError, TypeError, ValueError, ApiError):
        # Fall back to the cached facts if the snapshot cannot be re-parsed.
        return json.loads(i_rev["facts_json"])


def _report_out(row: dict) -> dict:
    return {
        "report_id": row["id"],
        "instance_id": row["instance_id"],
        "package_id": row["package_id"],
        "instance_revision": row["instance_revision"],
        "package_revision": row["package_revision"],
        "status": row["status"],
        "is_correction": bool(row["is_correction"]),
        "superseded": bool(row["superseded"]),
        "late": bool(row["late"]),
        "witness": json.loads(row["witness_json"]) if row["witness_json"] else None,
        "conflicts": json.loads(row["conflicts_json"]) if row["conflicts_json"] else None,
        "missing_facts": json.loads(row["missing_json"]) if row["missing_json"] else None,
        "detail": json.loads(row["detail_json"]),
        "created_at": row["created_at"],
        "has_instance_snapshot": bool(row["instance_snapshot"]),
        "has_xsd_snapshot": bool(row["xsd_snapshot"]),
        "has_calc_snapshot": bool(row["calc_snapshot"]),
    }


def run_check(iid: str, expected_instance_revision: int,
              expected_package_revision: int, late: bool = False) -> dict:
    conn = db.get_conn()
    inst_row = conn.execute("SELECT * FROM instances WHERE id=?", (iid,)).fetchone()
    if inst_row is None:
        raise NotFound(f"instance '{iid}' not found")

    pkg = get_package(inst_row["package_id"])

    # 受理时校验两侧所见修订：
    # - 客户端所见修订领先于服务器 → 过期拒绝（412）
    # - 落后于服务器：默认仍拒绝；仅当显式声明为迟到补报时受理，仅存历史。
    if expected_instance_revision > inst_row["latest_rev"]:
        raise StaleRevision(
            f"expected instance revision {expected_instance_revision} is ahead of "
            f"server latest {inst_row['latest_rev']}"
        )
    if expected_package_revision > pkg["latest_revision"]:
        raise StaleRevision(
            f"expected package revision {expected_package_revision} is ahead of "
            f"server latest {pkg['latest_revision']}"
        )
    behind = (expected_instance_revision < inst_row["latest_rev"]
              or expected_package_revision < pkg["latest_revision"])
    if behind and not late:
        raise StaleRevision(
            f"revisions ({expected_instance_revision},"
            f"{expected_package_revision}) are behind server latest "
            f"({inst_row['latest_rev']},{pkg['latest_revision']}); "
            f"re-check with late=true to file a late historical report"
        )

    i_rev = _instance_revision_row(iid, expected_instance_revision)
    p_rev = _package_revision_row(inst_row["package_id"], expected_package_revision)

    # 迟到报告：依据的修订已落后于两侧最新组合，仅存历史，不取代当前报告。
    is_late = behind
    facts = _exact_facts_for_revision(i_rev, p_rev)
    equations = json.loads(p_rev["equations_json"])

    result = check_facts(facts, equations)

    now = _now()
    report_id = _new_id("rpt")
    prior = conn.execute(
        "SELECT id FROM reports WHERE instance_id=? ORDER BY created_at, rowid", (iid,)
    ).fetchall()
    is_correction = len(prior) > 0

    detail = {
        "entity": i_rev["entity"],
        "period": i_rev["period"],
        "currency": i_rev["currency"],
        "fact_count": len(facts),
        "equation_count": len(equations),
        "solver": "exact rational simplex (fractions.Fraction)",
        "amount_representation": "exact decimal; binary floats are never used for decisions",
    }
    payload = {
        "id": report_id,
        "instance_id": iid,
        "package_id": inst_row["package_id"],
        "instance_revision": i_rev["revision"],
        "package_revision": p_rev["revision"],
        "status": result["status"],
        "is_correction": 1 if is_correction else 0,
        "late": 1 if is_late else 0,
        "witness_json": json.dumps(result["witness"]) if "witness" in result else None,
        "conflicts_json": json.dumps(result["conflicts"]) if "conflicts" in result else None,
        "missing_json": json.dumps(result["missing_facts"]) if "missing_facts" in result else None,
        "detail_json": json.dumps(detail),
        "instance_snapshot": i_rev["instance_raw"],
        "xsd_snapshot": p_rev["xsd_raw"],
        "calc_snapshot": p_rev["calc_raw"],
        "created_at": now,
    }

    superseded_ids: list[str] = []
    with conn:
        conn.execute(
            "INSERT INTO reports(id, instance_id, package_id, instance_revision, "
            "package_revision, status, is_correction, superseded, late, witness_json, "
            "conflicts_json, missing_json, detail_json, instance_snapshot, xsd_snapshot, "
            "calc_snapshot, created_at) VALUES (:id,:instance_id,:package_id,"
            ":instance_revision,:package_revision,:status,:is_correction,0,:late,"
            ":witness_json,:conflicts_json,:missing_json,:detail_json,:instance_snapshot,"
            ":xsd_snapshot,:calc_snapshot,:created_at)",
            payload,
        )
        if not is_late:
            old = conn.execute(
                "SELECT id FROM reports WHERE instance_id=? AND id!=? AND superseded=0",
                (iid, report_id),
            ).fetchall()
            superseded_ids = [r["id"] for r in old]
            conn.execute(
                "UPDATE reports SET superseded=1 WHERE instance_id=? AND id!=?",
                (iid, report_id),
            )

    out = _report_out(dict(conn.execute(
        "SELECT * FROM reports WHERE id=?", (report_id,)
    ).fetchone()))
    return {"report": out, "superseded_report_ids": superseded_ids}


def get_report(rid: str) -> dict:
    row = db.get_conn().execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise NotFound(f"report '{rid}' not found")
    return _report_out(dict(row))


def get_report_snapshot(rid: str, kind: str) -> tuple[bytes, str]:
    row = db.get_conn().execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise NotFound(f"report '{rid}' not found")
    column = {"instance": "instance_snapshot", "xsd": "xsd_snapshot",
              "calc": "calc_snapshot"}[kind]
    media = {"instance": "application/xml", "xsd": "application/xml",
             "calc": "application/xml"}[kind]
    return bytes(row[column]), media


def list_reports(iid: str) -> dict:
    conn = db.get_conn()
    if conn.execute("SELECT 1 FROM instances WHERE id=?", (iid,)).fetchone() is None:
        raise NotFound(f"instance '{iid}' not found")
    rows = conn.execute(
        "SELECT * FROM reports WHERE instance_id=? ORDER BY created_at, rowid", (iid,)
    ).fetchall()
    history = [_report_out(dict(r)) for r in rows]
    # 迟到报告仅存历史，永远不作为当前报告。
    current = next(
        (r for r in reversed(history) if not r["superseded"] and not r["late"]), None
    )
    return {"instance_id": iid, "current": current, "history": history}

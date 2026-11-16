"""End-to-end API behaviour: revisions, stale rejection, corrections, history."""
from __future__ import annotations

from conftest import check, create_instance, create_package


def test_feasible_report_returns_witness(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["feasible"])
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    assert report["status"] == "feasible"
    assert report["is_correction"] is False
    assert report["superseded"] is False
    witness = {w["concept"]: w for w in report["witness"]}
    # every witness value lies inside the closed rounding interval
    for w in report["witness"]:
        assert w["lower"] <= w["true_value"] <= w["upper"]
    # the weighted summations hold exactly on the witness
    for eq in pkg["latest"]["equations"]:
        residual = sum(coef * witness[c]["true_value"]
                       for c, coef in eq["coefficients"].items())
        assert abs(residual) < 1e-8


def test_rounding_only_difference_is_feasible(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["rounding"])
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 200
    assert r.json()["report"]["status"] == "feasible"


def test_infeasible_report_lists_contradictory_facts(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["infeasible"])
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 200
    report = r.json()["report"]
    assert report["status"] == "infeasible"
    assert report["witness"] is None
    assert len(report["conflicts"]) >= 1
    # the exact (INF) NetIncome pin is one of the contradictions
    eqs = {c["equation"]["id"] for c in report["conflicts"]}
    assert "eq3" in eqs
    for c in report["conflicts"]:
        assert c["facts"], "conflict must name the involved facts"


def test_missing_fact_makes_whole_filing_pending(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["pending"])
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 200
    report = r.json()["report"]
    assert report["status"] == "pending"
    assert "acct:Inventory" in report["missing_facts"]
    assert "acct:NetIncome" in report["missing_facts"]
    assert report["conflicts"] is None and report["witness"] is None


def test_stale_package_revision_rejected(client, samples):
    pkg = create_package(client, samples)
    # package revision 2
    r = client.post(
        f"/packages/{pkg['package_id']}/revisions",
        headers={"Expected-Revision": "1"},
        files={
            "xsd": ("acct.xsd", samples["xsd"], "application/xml"),
            "calculation": ("acct-calc.xml", samples["calc"], "application/xml"),
        },
    )
    assert r.status_code == 200
    inst = create_instance(client, pkg["package_id"], samples["feasible"])

    # stale check against package rev 1
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 412
    assert r.json()["error"]["code"] == "stale_revision"

    # ahead-of-server revisions are also rejected
    r = check(client, inst["instance_id"], 1, 99)
    assert r.status_code == 412

    # current revisions accepted
    r = check(client, inst["instance_id"], 1, 2)
    assert r.status_code == 200

def test_stale_instance_revision_and_correction_flow(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["infeasible"])

    first = check(client, inst["instance_id"], 1, 1).json()["report"]
    assert first["status"] == "infeasible"

    # correction: instance revision 2 (package still rev 1)
    r = client.post(
        f"/instances/{inst['instance_id']}/revisions",
        headers={"Expected-Package-Revision": "1"},
        files={"instance": ("i.xml", samples["feasible"], "application/xml")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["latest_revision"] == 2

    # instance correction against stale package rev is rejected
    r = client.post(
        f"/instances/{inst['instance_id']}/revisions",
        headers={"Expected-Package-Revision": "5"},
        files={"instance": ("i.xml", samples["feasible"], "application/xml")},
    )
    assert r.status_code == 412

    # re-check on the corrected revision
    second = check(client, inst["instance_id"], 2, 1).json()
    assert second["report"]["status"] == "feasible"
    assert second["report"]["is_correction"] is True
    assert second["superseded_report_ids"] == [first["report_id"]]

    # old report flagged expired, new one is current; both still readable
    listing = client.get(
        f"/instances/{inst['instance_id']}/reports"
    ).json()
    assert listing["current"]["report_id"] == second["report"]["report_id"]
    statuses = {r["report_id"]: r["superseded"] for r in listing["history"]}
    assert statuses[first["report_id"]] is True
    assert statuses[second["report"]["report_id"]] is False
    got = client.get(f"/reports/{first['report_id']}")
    assert got.status_code == 200
    assert got.json()["superseded"] is True


def test_late_report_kept_as_history_only(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["infeasible"])

    # first report on rev 1 (infeasible)
    first = check(client, inst["instance_id"], 1, 1).json()["report"]

    # correction to rev 2 then a fresh feasible report
    client.post(
        f"/instances/{inst['instance_id']}/revisions",
        headers={"Expected-Package-Revision": "1"},
        files={"instance": ("i.xml", samples["feasible"], "application/xml")},
    )
    current = check(client, inst["instance_id"], 2, 1).json()["report"]

    # a late report deliberately targeting the old revision 1:
    # without the explicit late flag it is rejected as stale...
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 412
    # ...with late=true it is accepted and kept as history only
    late = check(client, inst["instance_id"], 1, 1, late=True).json()["report"]
    assert late["late"] is True
    assert late["report_id"] not in (first["report_id"], current["report_id"])

    listing = client.get(f"/instances/{inst['instance_id']}/reports").json()
    # late report never becomes the current report
    assert listing["current"]["report_id"] == current["report_id"]
    history_ids = {r["report_id"] for r in listing["history"]}
    assert late["report_id"] in history_ids
    # the late report is individually readable
    assert client.get(f"/reports/{late['report_id']}").json()["late"] is True


def test_large_exact_amounts_decide_correctly(client, samples):
    """9007199254740993 and 9007199254740992 must not be treated as equal."""
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["large_inf"])
    report = check(client, inst["instance_id"], 1, 1).json()["report"]

    assert report["status"] == "infeasible"
    assert report["witness"] is None
    conflicts = report["conflicts"]
    assert conflicts
    # the contradictory Assets = CurrentAssets + NonCurrentAssets relation
    eq1 = next(c for c in conflicts if c["equation"]["summation"] == "acct:Assets")
    facts = {f["concept"]: f["reported_value"] for f in eq1["facts"]}
    assert facts["acct:Assets"] == 9007199254740993
    assert facts["acct:CurrentAssets"] == 9007199254740992
    assert eq1["residual_at_witness"] == -1

    # the exact amounts survive storage and retrieval unchanged
    stored = client.get(f"/instances/{inst['instance_id']}").json()
    values = {f["concept"]: f["value"] for f in stored["latest"]["facts"]}
    assert values["acct:Assets"] == 9007199254740993
    assert values["acct:CurrentAssets"] == 9007199254740992


def test_large_amounts_with_rounding_have_exact_witness(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["large_rounding"])
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 200
    report = r.json()["report"]
    assert report["status"] == "feasible"

    from fractions import Fraction

    from app.amounts import to_fraction

    witness = {w["concept"]: w for w in report["witness"]}
    for w in report["witness"]:
        assert to_fraction(w["lower"]) <= to_fraction(w["true_value"])
        assert to_fraction(w["true_value"]) <= to_fraction(w["upper"])
    for eq in pkg["latest"]["equations"]:
        residual = sum(
            Fraction(coef) * to_fraction(witness[c]["true_value"])
            for c, coef in eq["coefficients"].items()
        )
        assert residual == 0
    # the half-unit witness is preserved exactly, not collapsed by float()
    assert witness["acct:Assets"]["true_value"] == "9007199254740992.5"


def test_check_uses_raw_snapshot_not_legacy_cached_floats(client, samples):
    """A revision stored with lossy cached facts is still decided exactly.

    Simulates an existing row written by the old float-based pipeline: the
    cached JSON says both INF amounts are 9007199254740992, but the immutable
    raw instance keeps 9007199254740993.  The check must follow the raw XML.
    """
    import json

    from app import db

    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["large_inf"])
    conn = db.get_conn()
    row = conn.execute(
        "SELECT facts_json FROM instance_revisions WHERE instance_id=?",
        (inst["instance_id"],),
    ).fetchone()
    legacy = json.loads(row["facts_json"])
    for fact in legacy:  # the old pipeline collapsed both amounts to 2**53
        if fact["concept"] == "acct:Assets":
            fact.update(value=9007199254740992, lower=9007199254740992,
                        upper=9007199254740992)
    with conn:
        conn.execute(
            "UPDATE instance_revisions SET facts_json=? WHERE instance_id=?",
            (json.dumps(legacy), inst["instance_id"]),
        )

    report = check(client, inst["instance_id"], 1, 1).json()["report"]
    assert report["status"] == "infeasible"
    eq1 = next(c for c in report["conflicts"]
               if c["equation"]["summation"] == "acct:Assets")
    facts = {f["concept"]: f["reported_value"] for f in eq1["facts"]}
    assert facts["acct:Assets"] == 9007199254740993


def test_solver_failure_publishes_no_report(client, samples, monkeypatch):
    from app import storage
    from app.errors import ApiError

    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["feasible"])
    first = check(client, inst["instance_id"], 1, 1).json()["report"]
    first_snapshot = client.get(
        f"/reports/{first['report_id']}/snapshots/instance"
    ).content

    def exploding_check(_facts, _equations):
        raise ApiError(500, "solver_error", "exact solver failed")

    monkeypatch.setattr(storage, "check_facts", exploding_check)
    r = check(client, inst["instance_id"], 1, 1)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "solver_error"

    # no new report was published, and the existing history was not touched
    listing = client.get(f"/instances/{inst['instance_id']}/reports").json()
    assert len(listing["history"]) == 1
    assert listing["current"]["report_id"] == first["report_id"]
    assert listing["current"]["superseded"] is False
    assert client.get(f"/reports/{first['report_id']}/snapshots/instance").content \
        == first_snapshot


def test_report_persists_all_three_sources(client, samples):
    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["feasible"])
    report = check(client, inst["instance_id"], 1, 1).json()["report"]

    inst_xml = client.get(
        f"/reports/{report['report_id']}/snapshots/instance"
    ).content
    xsd_xml = client.get(f"/reports/{report['report_id']}/snapshots/xsd").content
    calc_xml = client.get(f"/reports/{report['report_id']}/snapshots/calc").content
    assert inst_xml == samples["feasible"]
    assert xsd_xml == samples["xsd"]
    assert calc_xml == samples["calc"]

    r = client.get(f"/reports/{report['report_id']}/snapshots/unknown")
    assert r.status_code == 422


def test_reports_survive_restart(client, samples, tmp_path, monkeypatch):
    from app import config, db

    pkg = create_package(client, samples)
    inst = create_instance(client, pkg["package_id"], samples["feasible"])
    report = check(client, inst["instance_id"], 1, 1).json()["report"]

    # simulate process restart: drop cached connection, rebuild app client
    db.reset_conn()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c2:
        r = c2.get(f"/reports/{report['report_id']}")
        assert r.status_code == 200
        assert r.json()["status"] == "feasible"
        assert c2.get(f"/packages/{pkg['package_id']}").status_code == 200
        assert c2.get(f"/instances/{inst['instance_id']}").status_code == 200
        listing = c2.get(f"/instances/{inst['instance_id']}/reports").json()
        assert len(listing["history"]) == 1
        snap = c2.get(f"/reports/{report['report_id']}/snapshots/instance").content
        assert snap == samples["feasible"]

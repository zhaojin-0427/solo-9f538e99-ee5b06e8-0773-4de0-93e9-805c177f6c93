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

"""Shared pytest fixtures: isolated temp DB + TestClient."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    db_path = tmp_path / "data" / "test.db"
    monkeypatch.setenv("XRECON_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XRECON_DB_PATH", str(db_path))

    # Import after env vars are set; reset cached connection between tests.
    from app import config, db
    config.DATA_DIR = Path(os.environ["XRECON_DATA_DIR"])
    config.DB_PATH = Path(os.environ["XRECON_DB_PATH"])
    db.reset_conn()
    db.init_db()

    from app.main import app
    with TestClient(app) as c:
        yield c

    db.reset_conn()


@pytest.fixture()
def samples():
    base = Path(__file__).resolve().parent / "samples"
    return {
        "xsd": (base / "acct.xsd").read_bytes(),
        "calc": (base / "acct-calc.xml").read_bytes(),
        "feasible": (base / "instance-feasible.xml").read_bytes(),
        "rounding": (base / "instance-rounding.xml").read_bytes(),
        "infeasible": (base / "instance-infeasible.xml").read_bytes(),
        "pending": (base / "instance-pending.xml").read_bytes(),
    }


def create_package(client, samples, name="acct"):
    r = client.post(
        "/packages",
        data={"name": name},
        files={
            "xsd": ("acct.xsd", samples["xsd"], "application/xml"),
            "calculation": ("acct-calc.xml", samples["calc"], "application/xml"),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def create_instance(client, package_id, xml: bytes):
    r = client.post(
        f"/packages/{package_id}/instances",
        files={"instance": ("i.xml", xml, "application/xml")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def check(client, instance_id, inst_rev, pkg_rev, late=False):
    url = f"/instances/{instance_id}/checks"
    if late:
        url += "?late=true"
    return client.post(
        url,
        headers={
            "Expected-Instance-Revision": str(inst_rev),
            "Expected-Package-Revision": str(pkg_rev),
        },
    )

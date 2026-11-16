"""Input validation: limits, weights, single entity/period/currency, no dimensions."""
from __future__ import annotations

import pytest

from conftest import create_package


def test_bad_weight_rejected(client, samples):
    calc = samples["calc"].replace(b'weight="1.0"', b'weight="0.5"', 1)
    r = client.post(
        "/packages",
        data={"name": "bad-weight"},
        files={
            "xsd": ("acct.xsd", samples["xsd"], "application/xml"),
            "calculation": ("c.xml", calc, "application/xml"),
        },
    )
    assert r.status_code == 422
    assert "weight" in r.json()["error"]["message"]


def test_unknown_locator_rejected(client, samples):
    calc = samples["calc"].replace(b"#Assets", b"#Ghost", 1)
    r = client.post(
        "/packages",
        data={"name": "bad-loc"},
        files={
            "xsd": ("acct.xsd", samples["xsd"], "application/xml"),
            "calculation": ("c.xml", calc, "application/xml"),
        },
    )
    assert r.status_code == 422
    assert "unknown concept" in r.json()["error"]["message"]


def test_malformed_xml_rejected(client, samples):
    r = client.post(
        "/packages",
        data={"name": "x"},
        files={
            "xsd": ("acct.xsd", b"<not-closed", "application/xml"),
            "calculation": ("c.xml", samples["calc"], "application/xml"),
        },
    )
    assert r.status_code == 422


def test_dimensions_rejected(client, samples):
    dim_xml = samples["feasible"].replace(
        b"<xbrli:context id=\"ctx1\">",
        b"""<xbrli:context id=\"ctx1\"><xbrli:scenario>
          <xbrldi:explicitMember xmlns:xbrldi=\"http://xbrl.org/2006/xbrldi\"
            dimension=\"acct:Region\">acct:US</xbrldi:explicitMember>
        </xbrli:scenario>""",
    )
    pkg = create_package(client, samples)
    r = client.post(
        f"/packages/{pkg['package_id']}/instances",
        files={"instance": ("i.xml", dim_xml, "application/xml")},
    )
    assert r.status_code == 422
    assert "dimension" in r.json()["error"]["message"]


def test_multiple_entities_rejected(client, samples):
    extra_ctx = (
        b'<xbrli:context id="ctx2">'
        b'<xbrli:entity><xbrli:identifier scheme="http://example.com/ids">E-002</xbrli:identifier></xbrli:entity>'
        b'<xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate>'
        b'<xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>'
        b'</xbrli:context>'
    )
    multi = samples["feasible"].replace(b'</xbrli:context>', b'</xbrli:context>' + extra_ctx, 1)
    pkg = create_package(client, samples)
    r = client.post(
        f"/packages/{pkg['package_id']}/instances",
        files={"instance": ("i.xml", multi, "application/xml")},
    )
    assert r.status_code == 422
    assert "entity" in r.json()["error"]["message"]


def test_multiple_currencies_rejected(client, samples):
    multi = samples["feasible"].replace(
        b'<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>',
        b"""<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>""",
    )
    # move one fact onto the EUR unit to make it used
    multi = multi.replace(
        b'<acct:Cash contextRef="ctx1" unitRef="usd"',
        b'<acct:Cash contextRef="ctx1" unitRef="eur"',
    )
    pkg = create_package(client, samples)
    r = client.post(
        f"/packages/{pkg['package_id']}/instances",
        files={"instance": ("i.xml", multi, "application/xml")},
    )
    assert r.status_code == 422
    assert "currency" in r.json()["error"]["message"]


def test_fact_limit_enforced(client, samples):
    from app import config
    assert config.MAX_FACTS == 30
    # build an XSD with 31 concepts and an instance with 31 facts
    ns = "http://example.com/big"
    els = "\n".join(
        f'  <xs:element name="F{i}" type="xs:decimal" abstract="false"/>'
        for i in range(31)
    )
    xsd = (
        f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        f'xmlns:big="{ns}" targetNamespace="{ns}">{els}</xs:schema>'
    ).encode()
    facts = "\n".join(
        f'<big:F{i} contextRef="c" unitRef="u" decimals="0">{i}</big:F{i}>'
        for i in range(31)
    )
    inst = (
        f'<xbrli:xbrl xmlns:big="{ns}" '
        f'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        f'xmlns:iso4217="http://www.xbrl.org/2003/iso4217">'
        f'<xbrli:context id="c"><xbrli:entity>'
        f'<xbrli:identifier scheme="s">E1</xbrli:identifier></xbrli:entity>'
        f'<xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>'
        f'</xbrli:context><xbrli:unit id="u">'
        f'<xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        + facts + f'</xbrli:xbrl>'
    ).encode()
    calc = (
        f'<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xmlns:calc="http://www.xbrl.org/2003/calculationLinkbase" '
        f'xmlns:big="{ns}">'
        f'<calc:calculationLink xlink:type="extended">'
        f'<link:loc xlink:type="locator" xlink:label="s" xlink:href="big.xsd#F0"/>'
        f'<link:loc xlink:type="locator" xlink:label="i" xlink:href="big.xsd#F1"/>'
        f'<calc:calculationArc xlink:type="arc" xlink:from="s" xlink:to="i" '
        f'xlink:arcrole="http://www.xbrl.org/2003/arcrole/summation-item" weight="1"/>'
        f'</calc:calculationLink></link:linkbase>'
    ).encode()
    r = client.post(
        "/packages",
        data={"name": "big"},
        files={
            "xsd": ("big.xsd", xsd, "application/xml"),
            "calculation": ("c.xml", calc, "application/xml"),
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["package_id"]
    r = client.post(
        f"/packages/{pid}/instances",
        files={"instance": ("i.xml", inst, "application/xml")},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "limit_exceeded"


def test_duplicate_fact_rejected(client, samples):
    dup = samples["feasible"].replace(
        b'<acct:Cash contextRef="ctx1" unitRef="usd" decimals="0">100</acct:Cash>',
        b'<acct:Cash contextRef="ctx1" unitRef="usd" decimals="0">100</acct:Cash>'
        b'<acct:Cash contextRef="ctx1" unitRef="usd" decimals="0">101</acct:Cash>',
    )
    pkg = create_package(client, samples)
    r = client.post(
        f"/packages/{pkg['package_id']}/instances",
        files={"instance": ("i.xml", dup, "application/xml")},
    )
    assert r.status_code == 422
    assert "duplicate" in r.json()["error"]["message"]


def test_missing_decimals_rejected(client, samples):
    bad = samples["feasible"].replace(
        b'<acct:Cash contextRef="ctx1" unitRef="usd" decimals="0">100</acct:Cash>',
        b'<acct:Cash contextRef="ctx1" unitRef="usd">100</acct:Cash>',
    )
    pkg = create_package(client, samples)
    r = client.post(
        f"/packages/{pkg['package_id']}/instances",
        files={"instance": ("i.xml", bad, "application/xml")},
    )
    assert r.status_code == 422
    assert "decimals" in r.json()["error"]["message"]


def test_unknown_concept_fact_rejected(client, samples):
    bad = samples["feasible"].replace(
        b'<acct:Cash', b'<acct:Ghost', 1
    )
    pkg = create_package(client, samples)
    r = client.post(
        f"/packages/{pkg['package_id']}/instances",
        files={"instance": ("i.xml", bad, "application/xml")},
    )
    assert r.status_code == 422


def test_not_found_paths(client):
    assert client.get("/packages/nope").status_code == 404
    assert client.get("/instances/nope").status_code == 404
    assert client.get("/reports/nope").status_code == 404

"""Parsing and validation of single-entity / single-period / no-dimension XBRL instances."""
from __future__ import annotations

from decimal import Decimal

from lxml import etree

from . import config
from .amounts import amount_out, decimals_interval, to_fraction
from .errors import BadPayload

XBRLI = "http://www.xbrl.org/2003/instance"
XLINK = "http://www.w3.org/1999/xlink"
ISO4217 = "http://www.xbrl.org/2003/iso4217"
DIM = "http://xbrl.org/2006/xbrldi"


def _parse_xml(data: bytes) -> etree._Element:
    if not data or not data.strip():
        raise BadPayload("XBRL instance is empty")
    try:
        return etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        raise BadPayload(f"XBRL instance is not well-formed XML: {exc.msg}") from exc


def parse_instance(data: bytes, package_info: dict) -> dict:
    root = _parse_xml(data)
    qname = etree.QName(root)
    if qname.namespace != XBRLI or qname.localname != "xbrl":
        raise BadPayload("instance root must be an xbrli:xbrl element")

    target_ns = package_info["target_ns"]
    prefix = package_info["prefix"]
    known_locals = {c.split(":", 1)[1] for c in package_info["concepts"]}

    # ---- contexts: one entity, one period, no dimensions ------------------
    contexts: dict[str, dict] = {}
    for ctx in root.iter(f"{{{XBRLI}}}context"):
        ctx_id = ctx.get("id")
        if not ctx_id:
            raise BadPayload("every context needs an id")
        if ctx.findall(f".//{{{DIM}}}explicitMember") or ctx.findall(
            f".//{{{DIM}}}typedMember"
        ):
            raise BadPayload("dimensional contexts are not supported")

        entity_el = ctx.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}identifier")
        if entity_el is None or not (entity_el.text or "").strip():
            raise BadPayload(f"context '{ctx_id}' lacks an entity identifier")
        entity = entity_el.text.strip()

        period_el = ctx.find(f"{{{XBRLI}}}period")
        if period_el is None:
            raise BadPayload(f"context '{ctx_id}' lacks a period")
        instant = period_el.find(f"{{{XBRLI}}}instant")
        start = period_el.find(f"{{{XBRLI}}}startDate")
        end = period_el.find(f"{{{XBRLI}}}endDate")
        forever = period_el.find(f"{{{XBRLI}}}forever")
        if instant is not None:
            period = f"instant={instant.text.strip()}"
        elif start is not None and end is not None:
            period = f"duration={start.text.strip()}/{end.text.strip()}"
        elif forever is not None:
            period = "forever"
        else:
            raise BadPayload(f"context '{ctx_id}' period must be instant or start/end duration")
        contexts[ctx_id] = {"entity": entity, "period": period}

    if not contexts:
        raise BadPayload("instance contains no contexts")
    entities = {c["entity"] for c in contexts.values()}
    periods = {c["period"] for c in contexts.values()}
    if len(entities) != 1:
        raise BadPayload(f"all facts must share one entity; found {sorted(entities)}")
    if len(periods) != 1:
        raise BadPayload(f"all facts must share one period; found {sorted(periods)}")
    entity = next(iter(entities))
    period = next(iter(periods))

    # ---- units: a single currency unit ------------------------------------
    units: dict[str, str] = {}
    for unit in root.iter(f"{{{XBRLI}}}unit"):
        unit_id = unit.get("id")
        measures = unit.findall(f"{{{XBRLI}}}measure")
        divide = unit.find(f"{{{XBRLI}}}divide")
        if divide is not None or len(measures) != 1:
            raise BadPayload(f"unit '{unit_id}' must be a single ISO 4217 currency measure")
        measure_text = (measures[0].text or "").strip()
        if ":" not in measure_text:
            raise BadPayload(f"unit '{unit_id}' measure must be a prefixed QName like iso4217:USD")
        m_prefix, m_local = measure_text.split(":", 1)
        m_ns = root.nsmap.get(m_prefix)
        if m_ns != ISO4217 or not m_local:
            raise BadPayload(f"unit '{unit_id}' measure must be an iso4217 currency")
        units[unit_id] = m_local
    if not units:
        raise BadPayload("instance contains no currency units")
    currencies = set(units.values())
    if len(currencies) != 1:
        raise BadPayload(f"all facts must share one currency; found {sorted(currencies)}")
    currency = next(iter(currencies))

    # ---- numeric facts -----------------------------------------------------
    facts: list[dict] = []
    seen_concepts: set[str] = set()
    for el in root:
        if not isinstance(el.tag, str):  # comments / processing instructions
            continue
        fq = etree.QName(el)
        if fq.namespace in (XBRLI, LINK_NS) or fq.namespace is None:
            continue
        if el.get("contextRef") is None:
            continue  # non-fact element (e.g. schemaRef, footnote links)
        if fq.namespace != target_ns:
            raise BadPayload(
                f"fact '{fq.localname}' namespace {fq.namespace} does not match package "
                f"targetNamespace {target_ns}"
            )
        if fq.localname not in known_locals:
            raise BadPayload(f"fact concept '{fq.localname}' is not declared in the package XSD")
        if el.get("unitRef") is None:
            raise BadPayload(f"numeric fact '{fq.localname}' needs a unitRef")
        unit_ref = el.get("unitRef")
        if unit_ref not in units:
            raise BadPayload(f"fact '{fq.localname}' references unknown unit '{unit_ref}'")
        ctx_ref = el.get("contextRef")
        if ctx_ref not in contexts:
            raise BadPayload(f"fact '{fq.localname}' references unknown context '{ctx_ref}'")
        decimals_attr = el.get("decimals")
        if decimals_attr is None:
            raise BadPayload(f"numeric fact '{fq.localname}' must declare decimals")
        if decimals_attr.strip() == "INF":
            decimals: object = "INF"
        else:
            try:
                decimals = int(decimals_attr)
            except ValueError:
                raise BadPayload(
                    f"fact '{fq.localname}' decimals must be an integer or INF, "
                    f"got '{decimals_attr}'"
                )
        text = (el.text or "").strip()
        try:
            value = Decimal(text)
        except Exception:
            raise BadPayload(f"fact '{fq.localname}' value '{text}' is not numeric")
        if not value.is_finite():
            raise BadPayload(f"fact '{fq.localname}' value must be finite")
        if value.is_zero():
            value = Decimal(0)  # normalise -0 / 0.000 without losing exactness
        sign, digits, exponent = value.as_tuple()
        # Bound the *rendered* magnitude, not just the coefficient, so exact
        # Fraction/integer conversion and JSON serialization stay cheap and do
        # not trip Python's integer-string digit limit.
        magnitude = exponent + len(digits)
        if (len(digits) > config.MAX_AMOUNT_DIGITS
                or abs(magnitude) > config.MAX_AMOUNT_DIGITS
                or abs(exponent) > config.MAX_AMOUNT_DIGITS):
            raise BadPayload(
                f"fact '{fq.localname}' amount is too large to check exactly"
            )
        if isinstance(decimals, int) and abs(decimals) > config.MAX_DECIMALS_ABS:
            raise BadPayload(
                f"fact '{fq.localname}' decimals magnitude exceeds "
                f"{config.MAX_DECIMALS_ABS}"
            )

        concept = f"{prefix}:{fq.localname}"
        if concept in seen_concepts:
            raise BadPayload(f"duplicate numeric fact for concept '{concept}'")
        seen_concepts.add(concept)

        # Keep the amount exact: ``Decimal``/``Fraction`` preserve every digit,
        # whereas ``float`` would merge distinct INF amounts above 2**53 and
        # lose the half-unit bound for large ``decimals=0`` values.
        exact_value = to_fraction(value)
        lower, upper = decimals_interval(exact_value, decimals)
        facts.append({
            "concept": concept,
            "value": amount_out(exact_value),
            "decimals": decimals,
            "lower": amount_out(lower),
            "upper": amount_out(upper),
        })

    if not facts:
        raise BadPayload("instance contains no numeric facts")
    if len(facts) > config.MAX_FACTS:
        from .errors import LimitExceeded
        raise LimitExceeded(f"too many numeric facts: {len(facts)} > {config.MAX_FACTS}")

    return {
        "entity": entity,
        "period": period,
        "currency": currency,
        "facts": facts,
    }


LINK_NS = "http://www.xbrl.org/2003/linkbase"

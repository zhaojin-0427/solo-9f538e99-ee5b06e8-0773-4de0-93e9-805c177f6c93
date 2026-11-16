"""Parsing and validation of taxonomy packages (XSD + calculation linkbase)."""
from __future__ import annotations

from lxml import etree

from . import config
from .errors import BadPayload

XS = "http://www.w3.org/2001/XMLSchema"
XLINK = "http://www.w3.org/1999/xlink"
LINK = "http://www.xbrl.org/2003/linkbase"
CALC = "http://www.xbrl.org/2003/calculationLinkbase"
SUMMATION_ITEM_ARCROLE = "http://www.xbrl.org/2003/arcrole/summation-item"
CALC_LINK_ROLE = "http://www.xbrl.org/2003/role/calculationLinkbaseRef"


def _parse_xml(data: bytes, what: str) -> etree._Element:
    if not data or not data.strip():
        raise BadPayload(f"{what} is empty")
    try:
        return etree.fromstring(data)
    except etree.XMLSyntaxError as exc:  # untrusted input
        raise BadPayload(f"{what} is not well-formed XML: {exc.msg}") from exc


def _tns_prefix(root: etree._Element, target_ns: str) -> str:
    for prefix, uri in root.nsmap.items():
        if uri == target_ns and prefix:
            return prefix
    return "tns"


def parse_xsd(data: bytes) -> dict:
    root = _parse_xml(data, "XSD schema")
    if etree.QName(root).localname != "schema" or etree.QName(root).namespace != XS:
        raise BadPayload("XSD root must be an xs:schema element")
    target_ns = root.get("targetNamespace")
    if not target_ns:
        raise BadPayload("XSD schema must declare a targetNamespace")

    concepts: dict[str, str] = {}  # local name -> qname
    abstract: set[str] = set()
    for el in root.iter(f"{{{XS}}}element"):
        name = el.get("name")
        if not name:
            continue
        if el.get("abstract", "false") == "true":
            abstract.add(name)
            continue
        concepts[name] = name  # qname completed once prefix is known

    if not concepts:
        raise BadPayload("XSD declares no concrete xs:element concepts")
    return {"target_ns": target_ns, "prefix": _tns_prefix(root, target_ns),
            "concepts": concepts, "abstract": abstract}


def parse_calculation(data: bytes, xsd_info: dict) -> dict:
    root = _parse_xml(data, "calculation linkbase")
    qname = etree.QName(root)
    if qname.namespace != LINK or qname.localname != "linkbase":
        raise BadPayload("calculation linkbase root must be a link:linkbase")

    target_ns: str = xsd_info["target_ns"]
    prefix: str = xsd_info["prefix"]
    known: set[str] = set(xsd_info["concepts"].keys())
    abstract: set[str] = xsd_info["abstract"]

    def q(local: str) -> str:
        return f"{prefix}:{local}"

    # label -> concept local name
    labels: dict[str, str] = {}
    for loc in root.iter(f"{{{LINK}}}loc"):
        label = loc.get(f"{{{XLINK}}}label")
        href = loc.get(f"{{{XLINK}}}href", "")
        if not label or "#" not in href:
            raise BadPayload("calculation link:loc needs xlink:label and xlink:href with fragment")
        local = href.rsplit("#", 1)[1].strip()
        if local not in known:
            raise BadPayload(f"link:loc references unknown concept '{local}' (not in XSD)")
        if local in abstract:
            raise BadPayload(f"link:loc references abstract concept '{local}'")
        labels[label] = local

    equations: list[dict] = []
    for link_el in root.iter(f"{{{CALC}}}calculationLink"):
        role = link_el.get(f"{{{XLINK}}}role", CALC_LINK_ROLE)
        # summation label -> ordered (item label, weight) pairs
        groups: dict[str, list[tuple[str, float]]] = {}
        for arc in link_el.iter(f"{{{CALC}}}calculationArc"):
            arcrole = arc.get(f"{{{XLINK}}}arcrole")
            if arcrole is not None and arcrole != SUMMATION_ITEM_ARCROLE:
                raise BadPayload(f"unsupported arcrole '{arcrole}'; only summation-item is allowed")
            frm = arc.get(f"{{{XLINK}}}from")
            to = arc.get(f"{{{XLINK}}}to")
            if frm not in labels or to not in labels:
                raise BadPayload("calculationArc xlink:from/xlink:to must resolve to link:loc labels")
            try:
                weight = float(arc.get("weight", ""))
            except (TypeError, ValueError):
                raise BadPayload("calculationArc must carry a numeric weight")
            if weight not in (1.0, -1.0):
                raise BadPayload(f"weight {weight} not allowed; only +1 and -1 are supported")
            groups.setdefault(frm, []).append((to, weight))

        for summ_label, arcs in groups.items():
            summation = labels[summ_label]
            items: list[dict] = []
            seen: set[str] = set()
            coefficients: dict[str, int] = {q(summation): -1}
            for item_label, weight in arcs:
                local = labels[item_label]
                if local == summation:
                    raise BadPayload(f"equation summation concept '{summation}' cannot also be an item")
                if local in seen:
                    raise BadPayload(f"concept '{local}' appears twice in one calculation equation")
                seen.add(local)
                w = int(weight)
                items.append({"concept": q(local), "weight": w})
                coefficients[q(local)] = coefficients.get(q(local), 0) + w
            equations.append({
                "id": f"eq{len(equations) + 1}",
                "role": role,
                "summation": q(summation),
                "items": items,
                "coefficients": coefficients,
                "concept_count": len(coefficients),
            })

    if not equations:
        raise BadPayload("calculation linkbase contains no summation equations")
    if len(equations) > config.MAX_EQUATIONS:
        from .errors import LimitExceeded
        raise LimitExceeded(f"too many equations: {len(equations)} > {config.MAX_EQUATIONS}")

    referenced = sorted({c for eq in equations for c in eq["coefficients"]})
    unknown = [c for c in referenced if c.split(":", 1)[1] not in known]
    if unknown:
        raise BadPayload(f"equations reference concepts missing from XSD: {', '.join(unknown)}")

    concept_qnames = sorted(q(local) for local in known)
    return {"concepts": concept_qnames, "equations": equations,
            "target_ns": target_ns, "prefix": prefix}


def parse_package(xsd: bytes, calc: bytes) -> dict:
    xsd_info = parse_xsd(xsd)
    calc_info = parse_calculation(calc, xsd_info)
    return calc_info

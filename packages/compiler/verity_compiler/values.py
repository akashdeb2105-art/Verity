"""Working out which values matter, and how they relate.

This is where a demonstration becomes a contract, and it is deliberately not
clever. If the same number is on the purchase order screen and on the ledger
screen, the person was comparing them -- that is an observation, not an
inference about intent. If a value appears both in a page's address and in its
text, it identifies the record, so it is an input rather than a constant.

Everything proposed here is traceable to a value that literally appeared twice.
Nothing is invented, which is what keeps precision high: a wrong proposed
assertion is worse than a missing one, because a person may trust it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlparse

from .normalize import Step

#: Record identifiers: two-to-five letters, a separator, then digits.
#: Case-insensitive on purpose. One screen shows 'PO-2211' and the next shows
#: 'po-2211'; if those do not match, the comparison the person actually made is
#: invisible to us.
IDENTIFIER = re.compile(r"\b[A-Za-z]{2,5}[-_/]?\d{2,10}\b")
#: Money and plain decimals.
MONEY = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?\b|\b\d+\.\d{1,2}\b")
#: Short all-capitals words, which is how status fields are almost always shown.
ENUM_LIKE = re.compile(r"^[A-Z][A-Z_ ]{1,19}$")

_STOPWORDS = frozenset({"", "-", "n/a", "none", "null", "0"})


@dataclass(frozen=True)
class Sighting:
    """One place a value was seen."""

    step_index: int
    system: str
    key: str
    raw: str
    explicitly_read: bool
    """True when the person clicked the thing to look at it, rather than it
    merely being somewhere on the page. Demonstrated attention, and the signal
    that separates a value that matters from wallpaper."""

    in_url: bool = False


@dataclass
class ValueGroup:
    """One normalised value and everywhere it turned up."""

    normalised: str
    kind: str  # "number" | "identifier" | "text"
    sightings: list[Sighting] = field(default_factory=list)

    @property
    def systems(self) -> set[str]:
        return {s.system for s in self.sightings}

    @property
    def is_cross_system(self) -> bool:
        return len(self.systems) > 1

    @property
    def was_explicitly_read(self) -> bool:
        return any(s.explicitly_read for s in self.sightings)

    @property
    def appears_in_url(self) -> bool:
        return any(s.in_url for s in self.sightings)

    def sighting_in(self, system: str) -> Sighting | None:
        explicit = [s for s in self.sightings if s.system == system and s.explicitly_read]
        if explicit:
            return explicit[0]
        for sighting in self.sightings:
            if sighting.system == system:
                return sighting
        return None


def normalise_value(raw: str) -> tuple[str, str] | None:
    """Return ``(normalised, kind)``, or ``None`` when the value is noise."""
    text = raw.strip()
    if text.lower() in _STOPWORDS or len(text) > 120:
        return None

    number = _as_decimal(text)
    if number is not None:
        return f"{number.normalize():f}", "number"

    if IDENTIFIER.fullmatch(text):
        return text.upper().replace("_", "-").replace("/", "-"), "identifier"

    return " ".join(text.casefold().split()), "text"


def _as_decimal(text: str) -> Decimal | None:
    cleaned = text.replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:  # pragma: no cover - guarded by the regex
        return None


def _candidates(text: str) -> list[str]:
    """The value itself, plus any identifier or amount embedded in it.

    'Invoice INV-4471 for PO-2211' is one string to a browser and two record
    references to a person.
    """
    found = [text]
    found.extend(IDENTIFIER.findall(text))
    found.extend(MONEY.findall(text))
    return found


def build_index(steps: list[Step]) -> dict[str, ValueGroup]:
    """Index every value the session saw, by normalised form."""
    index: dict[str, ValueGroup] = {}

    for step in steps:
        system = step.system or "unknown"
        url_tokens = _url_tokens(step.url)
        explicit_key = _explicit_key(step)

        for key, raw in step.observed.items():
            for candidate in _candidates(raw):
                entry = normalise_value(candidate)
                if entry is None:
                    continue
                normalised, kind = entry
                group = index.setdefault(normalised, ValueGroup(normalised, kind))
                group.sightings.append(
                    Sighting(
                        step_index=step.index, system=system, key=key, raw=candidate,
                        explicitly_read=(key == explicit_key),
                        in_url=normalised in url_tokens,
                    )
                )

        if step.value:
            for candidate in _candidates(step.value):
                entry = normalise_value(candidate)
                if entry is None:
                    continue
                normalised, kind = entry
                group = index.setdefault(normalised, ValueGroup(normalised, kind))
                group.sightings.append(
                    Sighting(
                        step_index=step.index, system=system,
                        key=explicit_key or "entered", raw=candidate,
                        explicitly_read=True, in_url=normalised in url_tokens,
                    )
                )

    return index


def _explicit_key(step: Step) -> str | None:
    """Which observed key the person actually clicked on, if any."""
    if step.verb != "EXTRACT" or not step.element_hint:
        return None
    match = re.search(r"testid=([\w-]+)", step.element_hint)
    return match.group(1) if match else None


def _url_tokens(url: str | None) -> set[str]:
    if not url:
        return set()
    tokens: set[str] = set()
    for segment in unquote(urlparse(url).path).split("/"):
        for candidate in _candidates(segment):
            entry = normalise_value(candidate)
            if entry is not None:
                tokens.add(entry[0])
    return tokens


@dataclass(frozen=True)
class ProposedInput:
    """A value that identifies which record the workflow is about."""

    name: str
    type: str
    example: str
    seen_in: list[str]
    reason: str


@dataclass(frozen=True)
class ProposedComparison:
    """The same value seen in two systems: the person was checking a match."""

    left_system: str
    left_key: str
    right_system: str
    right_key: str
    kind: str
    example: str
    both_explicit: bool


@dataclass(frozen=True)
class ProposedConstant:
    """A value the person deliberately read that never varies elsewhere."""

    system: str
    key: str
    value: str
    reason: str


def analyse(steps: list[Step]) -> tuple[list[ProposedInput], list[ProposedComparison],
                                        list[ProposedConstant]]:
    index = build_index(steps)

    inputs = _propose_inputs(index)
    input_values = {i.example for i in inputs}
    comparisons = _propose_comparisons(index, input_values)
    constants = _propose_constants(index, input_values)
    return inputs, comparisons, constants


def _propose_inputs(index: dict[str, ValueGroup]) -> list[ProposedInput]:
    """A value in a page address that also appears in text identifies a record."""
    proposed: list[ProposedInput] = []
    for group in _ordered(index):
        if group.kind != "identifier" or not group.appears_in_url:
            continue
        systems = sorted(group.systems)
        proposed.append(
            ProposedInput(
                name=_input_name(group), type="string", example=group.normalised,
                seen_in=systems,
                reason="appears in a page address and in page text, so it selects the record",
            )
        )
    # Identifiers that were never in a URL but appear in more than one system
    # still identify something -- an invoice number carried across screens.
    for group in _ordered(index):
        if group.kind != "identifier" or group.appears_in_url:
            continue
        if not group.is_cross_system:
            continue
        if any(p.example == group.normalised for p in proposed):
            continue
        proposed.append(
            ProposedInput(
                name=_input_name(group), type="string", example=group.normalised,
                seen_in=sorted(group.systems),
                reason="the same reference appears in more than one system",
            )
        )
    return proposed


def _propose_comparisons(
    index: dict[str, ValueGroup], input_values: set[str]
) -> list[ProposedComparison]:
    proposed: list[ProposedComparison] = []
    for group in _ordered(index):
        if not group.is_cross_system or group.normalised in input_values:
            continue
        if group.kind == "text" and len(group.normalised) < 3:
            continue

        systems = sorted(group.systems)
        for i, left in enumerate(systems):
            for right in systems[i + 1:]:
                left_sighting = group.sighting_in(left)
                right_sighting = group.sighting_in(right)
                if left_sighting is None or right_sighting is None:
                    continue
                proposed.append(
                    ProposedComparison(
                        left_system=left, left_key=left_sighting.key,
                        right_system=right, right_key=right_sighting.key,
                        kind=group.kind, example=group.normalised,
                        both_explicit=left_sighting.explicitly_read
                        and right_sighting.explicitly_read,
                    )
                )
    return proposed


def _propose_constants(
    index: dict[str, ValueGroup], input_values: set[str]
) -> list[ProposedConstant]:
    """Only from values the person clicked on, and only enum-shaped ones.

    A number the person read is a variable, not a constant: asserting
    ``amount == 14800`` would pass today and be wrong tomorrow. A status word
    is different -- 'DRAFT' is the end state that was demonstrated.
    """
    proposed: list[ProposedConstant] = []
    for group in _ordered(index):
        if group.is_cross_system or group.normalised in input_values:
            continue
        if not group.was_explicitly_read:
            continue
        sighting = next(s for s in group.sightings if s.explicitly_read)
        if not ENUM_LIKE.fullmatch(sighting.raw.strip()):
            continue
        proposed.append(
            ProposedConstant(
                system=sighting.system, key=sighting.key, value=sighting.raw.strip(),
                reason="the person opened this to look at it, and it names a state",
            )
        )
    return proposed


def _ordered(index: dict[str, ValueGroup]) -> list[ValueGroup]:
    """Deterministic order: first sighting, then value. Two runs agree."""
    return sorted(
        index.values(),
        key=lambda g: (min(s.step_index for s in g.sightings), g.normalised),
    )


def _input_name(group: ValueGroup) -> str:
    prefix = group.normalised.split("-")[0].lower()
    known = {"inv": "invoice_number", "po": "po_number", "so": "sales_order_number",
             "gr": "goods_receipt_number", "bill": "bill_number", "ord": "order_number"}
    return known.get(prefix, f"{prefix}_reference")

"""Turning a recorded demonstration into a WorkGraph and a proposed contract.

Deterministic by design. Nothing in this package calls a model: every step,
variable and assertion it proposes is traceable to a value that literally
appeared in the recording. A model may later improve the *wording* of a
proposal, but it does not get to decide what is checked -- a wrong proposed
assertion is worse than a missing one, because a person may trust it.

The compiler and the verifier are siblings, and an import-linter contract stops
either importing the other. A contract that could only be checked by the thing
that wrote it would prove nothing.
"""

from .contract import ContractDraft, propose, to_yaml
from .enrich import Enrichment, Suggestion, build_prompt, enrich
from .graph import build, summarise
from .normalize import Step, normalize, system_of
from .values import (
    ProposedComparison,
    ProposedConstant,
    ProposedInput,
    ValueGroup,
    analyse,
    build_index,
)

__all__ = [
    "ContractDraft", "Enrichment", "ProposedComparison", "ProposedConstant", "ProposedInput",
    "Step", "Suggestion", "ValueGroup", "analyse", "build", "build_index",
    "build_prompt", "enrich", "normalize",
    "propose", "summarise", "system_of", "to_yaml",
]

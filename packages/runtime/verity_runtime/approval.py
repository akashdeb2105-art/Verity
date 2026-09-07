"""Approval, bound to one exact payload in one exact run.

The failure this module exists to prevent is not "nobody approved it". It is
"somebody approved *something*, and then something else happened". An approval
that means "yes, go ahead" is nearly worthless: between the moment a person
reads a screen and the moment a write lands, the payload can change, and every
part of the system will still report that a human approved it.

So an approval here is a claim about three things at once -- the run, the node,
and a digest of exactly what would be written. Change the amount after the fact
and the digest changes, the claim no longer matches, and the write is refused
with the mismatch named. Approving a $14,800 bill cannot authorise a $148,000
one, because those are different facts and there is no approval for the second.

The matching lives in this module rather than in a store, so that a store
written carelessly -- or written by somebody else -- cannot grant anything. A
store's only job is to hand over what it holds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Approval:
    """A person's decision about one specific write.

    ``digest`` is the value produced by
    :func:`verity_connectors.payload_digest`, which covers the resource and
    every field of the payload.
    """

    run_id: str
    node_id: str
    digest: str
    approver: str
    granted_at: float = 0.0
    expires_at: float = 0.0
    """Wall-clock epoch seconds. ``0.0`` means the approval does not expire."""

    revoked: bool = False
    note: str = ""

    def describe(self) -> str:
        return f"{self.approver} approved {self.node_id} in {self.run_id}"


@dataclass(frozen=True)
class ApprovalCheck:
    """Whether a write is approved, and if not, precisely why not."""

    granted: bool
    reason: str
    approval: Approval | None = None

    @property
    def refused(self) -> bool:
        return not self.granted


@runtime_checkable
class ApprovalStore(Protocol):
    """Hands over the approvals recorded for a step. Decides nothing."""

    def approvals_for(self, run_id: str, node_id: str) -> Sequence[Approval]: ...


class NoApprovals:
    """The default store: nothing has been approved.

    A runtime with no approval source configured must not be able to satisfy an
    approval requirement. The safe direction for a missing decision is to stop.
    """

    def approvals_for(self, run_id: str, node_id: str) -> Sequence[Approval]:
        return ()


@dataclass
class InMemoryApprovalStore:
    """Approvals held in the process. Used by the CLI and by tests."""

    approvals: list[Approval] = field(default_factory=list)

    def grant(self, approval: Approval) -> Approval:
        self.approvals.append(approval)
        return approval

    def approvals_for(self, run_id: str, node_id: str) -> Sequence[Approval]:
        return [a for a in self.approvals if a.run_id == run_id and a.node_id == node_id]


def check_approval(
    store: ApprovalStore,
    *,
    run_id: str,
    node_id: str,
    digest: str,
    now: float = 0.0,
) -> ApprovalCheck:
    """Decide whether ``digest`` is approved for this node in this run.

    Every refusal names its own cause. "Not approved" and "approved, but for a
    different payload" are very different situations for the person reading the
    report, and collapsing them into one message is how a tampered run gets
    mistaken for a forgotten one.
    """
    candidates = list(store.approvals_for(run_id, node_id))
    if not candidates:
        return ApprovalCheck(
            granted=False,
            reason=f"no approval on file for {node_id!r} in run {run_id!r}",
        )

    mismatched: list[Approval] = []
    for approval in candidates:
        if approval.run_id != run_id or approval.node_id != node_id:
            continue  # a store that hands back the wrong rows decides nothing
        if approval.digest != digest:
            mismatched.append(approval)
            continue
        if approval.revoked:
            return ApprovalCheck(
                granted=False,
                reason=f"the approval by {approval.approver} was revoked",
                approval=approval,
            )
        if approval.expires_at and now and now > approval.expires_at:
            return ApprovalCheck(
                granted=False,
                reason=f"the approval by {approval.approver} expired",
                approval=approval,
            )
        return ApprovalCheck(
            granted=True,
            reason=f"approved by {approval.approver} for this exact payload",
            approval=approval,
        )

    if mismatched:
        was = mismatched[0]
        return ApprovalCheck(
            granted=False,
            reason=(
                "the payload changed after it was approved: "
                f"{was.approver} approved {was.digest}, this run would write {digest}"
            ),
            approval=was,
        )
    return ApprovalCheck(
        granted=False,
        reason=f"no approval on file for {node_id!r} in run {run_id!r}",
    )


__all__ = [
    "Approval",
    "ApprovalCheck",
    "ApprovalStore",
    "InMemoryApprovalStore",
    "NoApprovals",
    "check_approval",
]

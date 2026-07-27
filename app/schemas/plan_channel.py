import uuid
from pydantic import BaseModel, ConfigDict, Field


class PlanChannelOut(BaseModel):
    """A catalog channel seen from a plan's whitelist.

    `id` is the CHANNEL id — the same value the write endpoints take. The
    plan_channels row id is deliberately not exposed: the admin panel edits the
    membership (plan, channel), never the join row.

    `included` is True only when a plan_channels row exists AND is_enabled —
    exactly the condition EntitlementService checks before allowing playback.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel_key: str
    number: int
    name: str
    category: str | None = None
    logo_url: str | None = None
    is_active: bool
    included: bool


class PlanChannelsReplace(BaseModel):
    """Body of PUT /plans/{plan_id}/channels — the FULL desired whitelist.

    An empty list is legal and means "this plan includes no channel", which
    denies playback of everything to its subscribers. It is not rejected here
    because clearing a plan is a legitimate admin action, but it is audited.
    """
    channel_ids: list[uuid.UUID] = Field(..., max_length=2000)


class PlanChannelsSummary(BaseModel):
    """What a write actually changed, so the caller can tell a no-op apart from
    a real change without re-reading the list.

    added   — channels that were NOT included and now are (new rows + rows
              flipped back from is_enabled=False).
    removed — plan_channels rows deleted.
    total   — channels included by the plan after the operation.
    """
    plan_id: uuid.UUID
    added: int
    removed: int
    total: int

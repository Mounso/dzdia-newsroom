"""Row models returned by `repository.py`."""

from uuid import UUID

from pydantic import BaseModel, JsonValue


class ClaimedJob(BaseModel):
    """A job owned by one worker until its lease expires. `claim_token` proves the ownership."""

    id: int
    kind: str
    payload: dict[str, JsonValue]
    attempts: int  # including the current one
    max_attempts: int
    claim_token: UUID

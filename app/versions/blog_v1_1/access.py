"""Request-scoped grants: no persistent authorization cache or extra session lookup."""

from dataclasses import dataclass

from app.db.database import get_db
from app.services.dashboard import grants, member
from fastapi import Depends, HTTPException


@dataclass(frozen=True)
class Access:
    membership: object
    rules: frozenset

    def allows(self, kind, key):
        return self.membership.role == "admin" or (kind, key, "view") in self.rules

    def require(self, kind, key):
        if not self.allows(kind, key):
            raise HTTPException(403, "Permission denied")


async def permission_context(membership=Depends(member), central=Depends(get_db)):
    rules = () if membership.role == "admin" else await grants(central, membership)
    return Access(
        membership,
        frozenset((rule.resource_type, rule.resource_key, rule.action) for rule in rules),
    )

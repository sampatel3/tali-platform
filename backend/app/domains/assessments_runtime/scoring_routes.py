from fastapi import APIRouter, Depends

from ...components.scoring.metadata import scoring_metadata_payload
from ...deps import get_current_user
from ...models.user import User

router = APIRouter(prefix="/scoring", tags=["Scoring"])


@router.get("/metadata")
def get_scoring_metadata(
    current_user: User = Depends(get_current_user),
):
    return scoring_metadata_payload()

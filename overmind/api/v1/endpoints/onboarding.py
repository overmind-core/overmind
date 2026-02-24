from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from overmind.db.session import get_db
from overmind.models.user_onboarding import UserOnboarding
from overmind.api.v1.helpers.authentication import AuthenticatedUserOrToken, get_current_user

router = APIRouter(tags=["onboarding"])


class UserOnboardingRequest(BaseModel):
    priorities: list[str]
    description: str | None = None


class UserOnboardingResponse(BaseModel):
    step: str
    status: str
    priorities: list[str] | None = None
    description: str

    @classmethod
    def from_model(cls, model: UserOnboarding) -> "UserOnboardingResponse":
        return cls(
            step=model.step,
            status=model.status,
            priorities=model.priorities,
            description=model.description,
        )


@router.post("/", response_model=UserOnboardingResponse)
async def create_user_onboarding(
    data: UserOnboardingRequest,
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(UserOnboarding).where(UserOnboarding.user_id == user.user_id)
    )
    onboarding = res.scalar_one_or_none()
    if onboarding:
        # Update existing onboarding record
        onboarding.priorities = data.priorities
        onboarding.description = data.description
    else:
        # Create new onboarding record
        onboarding = UserOnboarding(
            user_id=user.user_id,
            step="2",
            status="completed",
            priorities=data.priorities,
            description=data.description,
        )
        db.add(onboarding)

    await db.commit()
    return UserOnboardingResponse.from_model(onboarding)


@router.get("/", response_model=UserOnboardingResponse)
async def get_user_onboarding(
    user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(UserOnboarding).where(UserOnboarding.user_id == user.user_id)
    )
    onboarding = res.scalar_one_or_none()
    if not onboarding:
        return Response(status_code=404, content="onboarding not found")

    return UserOnboardingResponse.from_model(onboarding)

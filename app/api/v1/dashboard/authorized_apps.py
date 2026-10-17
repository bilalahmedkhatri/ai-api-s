"""Session-authenticated admin UI adapter for existing app-management operations."""
from app.api.v1.apps.router import (
    AppCreate,
    AppUpdate,
    OwnerPermission,
    available_endpoints,
    list_apps,
    register_app,
    set_owner_permission,
    update_app,
)
from app.db.database import get_db
from app.models.db_models import User
from app.services.dashboard import audit, check_origin, member, require_admin
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class AppSettingsRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def checked(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, headers={"Cache-Control": "no-store"}, content={
                    "detail": "Check the app name, gateway owner, origin and exact endpoint permissions."
                })

        return checked


router = APIRouter(prefix="/dashboard/authorized-apps", tags=["dashboard"], route_class=AppSettingsRoute)


async def mutate(db, m, action, target, operation):
    try:
        # Existing operations commit their change and this audit row together.
        await audit(db, m, action, target)
        return await operation()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "An app with these registration details already exists") from None
    except Exception:
        await db.rollback()
        raise


@router.get("")
async def catalog(request: Request, response: Response, m=Depends(member), db=Depends(get_db)):
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    owners = (await db.execute(select(User.id, User.email, User.is_active, User.api_access_enabled)
                              .order_by(User.email, User.id))).mappings().all()
    return {"apps": await list_apps(db), "owners": [dict(row) for row in owners],
            "endpoints": sorted(available_endpoints(request))}


@router.post("", status_code=201)
async def create(body: AppCreate, request: Request, response: Response,
                 m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    return await mutate(db, m, "CREATE_AUTHORIZED_APP", body.name, lambda: register_app(body, request, db))


@router.patch("/owners/{user_id}/permission")
async def owner_permission(user_id: int, body: OwnerPermission, request: Request, response: Response,
                           m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    return await mutate(db, m, "SET_OWNER_API_ACCESS", str(user_id), lambda: set_owner_permission(user_id, body, db))


@router.patch("/{app_id}")
async def update(app_id: int, body: AppUpdate, request: Request, response: Response,
                 m=Depends(member), db=Depends(get_db)):
    check_origin(request)
    require_admin(m)
    response.headers["Cache-Control"] = "no-store"
    return await mutate(db, m, "UPDATE_AUTHORIZED_APP", str(app_id), lambda: update_app(app_id, body, request, db))

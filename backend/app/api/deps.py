from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import load_settings
from app.db import get_session
from app.security import Actor, decode_token
from app.services.admin import load_actor

_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[Session, Depends(get_session)]


def current_actor(
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Actor:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": "invalid or expired token"},
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    try:
        user_id = decode_token(creds.credentials, load_settings().jwt_secret)
    except (jwt.PyJWTError, ValueError) as exc:
        raise unauthorized from exc
    actor = load_actor(session, user_id)
    if actor is None:
        raise unauthorized
    return actor


ActorDep = Annotated[Actor, Depends(current_actor)]

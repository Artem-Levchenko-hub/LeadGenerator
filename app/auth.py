import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

security = HTTPBasic()


def check_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    users = settings.auth_users_dict
    expected_password = users.get(credentials.username)
    if expected_password is None or not secrets.compare_digest(
        credentials.password.encode(), expected_password.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

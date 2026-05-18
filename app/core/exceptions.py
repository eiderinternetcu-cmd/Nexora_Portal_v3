from fastapi import HTTPException, status


class NexoraException(HTTPException):
    pass


def not_found(resource: str = "Resource") -> NexoraException:
    return NexoraException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource} not found")


def already_exists(resource: str = "Resource") -> NexoraException:
    return NexoraException(status_code=status.HTTP_409_CONFLICT, detail=f"{resource} already exists")


def forbidden(detail: str = "Forbidden") -> NexoraException:
    return NexoraException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def unauthorized(detail: str = "Not authenticated") -> NexoraException:
    return NexoraException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def bad_request(detail: str) -> NexoraException:
    return NexoraException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def locked(detail: str = "Account locked") -> NexoraException:
    return NexoraException(status_code=status.HTTP_423_LOCKED, detail=detail)


def rate_limited() -> NexoraException:
    return NexoraException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Try again later.",
    )

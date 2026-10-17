class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class Invalid(DomainError):
    status_code = 422
    code = "invalid"


class InsufficientStock(Conflict):
    code = "insufficient_stock"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class TooManyAttempts(DomainError):
    status_code = 429
    code = "too_many_attempts"

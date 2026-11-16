"""Exceptions for MIREA RPD."""


class RPDError(Exception):
    """Base exception for RPD operations."""


class RPDAuthError(RPDError):
    """Raised when authentication fails or session has expired."""


class RPDNotFoundError(RPDError):
    """Raised when requested entity or resource is not found."""


class RPDRequestError(RPDError):
    """Raised when network request or server returns an error."""

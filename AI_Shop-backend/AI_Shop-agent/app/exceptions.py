class BusinessException(Exception):

    def __init__(self, code: int, message: str, data=None):

        super().__init__(message)

        self.code = code

        self.message = message

        self.data = data


class PendingActionExpired(ValueError):
    """The confirmation token is no longer actionable."""


class PendingActionConflict(ValueError):
    """A different active proposal already owns the same business resource."""


class RemoteActionRejected(ValueError):
    """Java returned a structured business rejection for a write action.

    The remote command may have committed its domain effect before a later
    side-effect failed, so callers must reconcile the action status before
    closing the local confirmation record.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RemoteActionOutcomeUnknown(RuntimeError):
    """A remote write may have committed, but its response cannot prove the outcome."""

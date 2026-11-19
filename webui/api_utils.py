"""Keep plain-JSON failures and public signatures visible to pywebview."""
from functools import wraps
import inspect


def json_method(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception as error:
            return {"ok": False, "error": str(error)}
    guarded.__signature__ = inspect.signature(method)
    return guarded

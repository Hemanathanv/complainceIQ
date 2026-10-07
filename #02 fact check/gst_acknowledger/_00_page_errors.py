"""Shared error handling for GST page-object actions."""

from functools import wraps
import inspect


class PageActionError(RuntimeError):
    """A concise, page-specific failure suitable for the workflow log."""


def with_page_error_handling(page_class):
    """Log failures from public page actions with their page and locator context."""
    for name, method in vars(page_class).items():
        if name.startswith("_") or not inspect.isfunction(method):
            continue

        @wraps(method)
        def guarded(self, *args, __method=method, __name=name, **kwargs):
            try:
                return __method(self, *args, **kwargs)
            except PageActionError:
                raise
            except Exception as error:
                lines = str(error).splitlines()
                details = lines[0].strip() if lines else repr(error)
                call_log_index = next(
                    (i for i, line in enumerate(lines) if line.strip() == "Call log:"),
                    None,
                )
                if call_log_index is not None and call_log_index + 1 < len(lines):
                    details += f"; {lines[call_log_index + 1].strip()}"
                message = f"{page_class.__name__}.{__name} failed: {details}"
                print(f"PAGE ACTION ERROR: {message}")
                raise PageActionError(message) from None

        setattr(page_class, name, guarded)
    return page_class

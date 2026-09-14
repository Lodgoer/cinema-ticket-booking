"""
Domain exceptions raised by service functions.

Services raise these instead of a plain ValueError so the meaning of
the failure (not found vs. conflict) travels with the exception itself.
Registered as global FastAPI exception handlers in main.py — routers
no longer need a try/except around every service call just to pick
the right HTTP status code.
"""


class NotFoundError(Exception):
    """A referenced entity (booking, seat, showtime, ...) doesn't exist."""


class ConflictError(Exception):
    """The request conflicts with current state — e.g. a seat that's
    already booked, or a booking that's already confirmed/cancelled."""
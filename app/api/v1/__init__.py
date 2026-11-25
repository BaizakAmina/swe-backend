from fastapi import APIRouter

from app.api.v1 import (
    admin,
    analytics,
    auth,
    campaigns,
    events,
    health,
    misc,
    orders,
    organizers,
    seating,
    staff,
    support,
    ticket_types,
    tickets,
    venues,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(auth.me_router)
api_router.include_router(organizers.router)
api_router.include_router(events.router)
api_router.include_router(ticket_types.router)
api_router.include_router(staff.router)
api_router.include_router(seating.router)
api_router.include_router(venues.router)
api_router.include_router(orders.router)
api_router.include_router(tickets.router)
api_router.include_router(tickets.checkin_router)
api_router.include_router(campaigns.router)
api_router.include_router(analytics.router)
api_router.include_router(support.router)
api_router.include_router(admin.router)
api_router.include_router(misc.router)

"""Aggregate recorded metrics without returning identities, prompts or credentials."""

from datetime import timedelta

from sqlalchemy import case, func, select

from app.models.dashboard import AuditLog
from app.models.db_models import FinalResponse
from app.services.dashboard import now


async def dashboard_analytics(db, member, days):
    if days not in {7, 30, 90}:
        from fastapi import HTTPException

        raise HTTPException(400, "Select a 7, 30 or 90 day range")
    bind = db.get_bind() if hasattr(db, "get_bind") else db.session.get_bind()

    def day(column):
        return (
            func.date(func.timezone("UTC", column))
            if bind.dialect.name == "postgresql"
            else func.date(column)
        )

    start = now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    end = now()
    filters = [
        AuditLog.created_at >= start,
        AuditLog.created_at <= end,
        AuditLog.action.in_(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
    ]
    if member.role != "admin":
        filters.append(AuditLog.actor_membership_id == member.id)
    audit_day = day(AuditLog.created_at)
    daily = (
        await db.execute(
            select(
                audit_day.label("day"),
                func.count().label("requests"),
                func.sum(case((AuditLog.status < 400, 1), else_=0)).label("success"),
                func.sum(case((AuditLog.status >= 400, 1), else_=0)).label("errors"),
                func.avg(AuditLog.elapsed_ms).label("latency_ms"),
                func.count(AuditLog.elapsed_ms).label("latency_samples"),
            )
            .where(*filters)
            .group_by(audit_day)
        )
    ).all()
    series = {str(r.day): dict(r._mapping) for r in daily}
    points = []
    for offset in range(days):
        date = (start + timedelta(days=offset)).date().isoformat()
        point = series.get(
            date,
            {
                "day": date,
                "requests": 0,
                "success": 0,
                "errors": 0,
                "latency_ms": None,
                "latency_samples": 0,
            },
        )
        point.update(
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": None,
                "query_responses": 0,
                "cost_samples": 0,
                "token_samples": 0,
            }
        )
        points.append(point)
    usage = None
    if member.role == "admin":
        response_day = day(FinalResponse.created_at)
        rows = (
            await db.execute(
                select(
                    response_day.label("day"),
                    func.count().label("query_responses"),
                    func.coalesce(func.sum(FinalResponse.prompt_tokens), 0).label("prompt_tokens"),
                    func.coalesce(func.sum(FinalResponse.completion_tokens), 0).label(
                        "completion_tokens"
                    ),
                    func.sum(FinalResponse.cost_usd).label("cost_usd"),
                    func.count(FinalResponse.cost_usd).label("cost_samples"),
                    func.sum(
                        case(
                            (
                                (
                                    FinalResponse.prompt_tokens.is_not(None)
                                    | FinalResponse.completion_tokens.is_not(None)
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("token_samples"),
                )
                .where(FinalResponse.created_at >= start, FinalResponse.created_at <= end)
                .group_by(response_day)
            )
        ).all()
        for r in rows:
            point = next((p for p in points if p["day"] == str(r.day)), None)
            if point:
                point.update({k: v for k, v in r._mapping.items() if k != "day"})
        cost_samples = sum(p["cost_samples"] for p in points)
        usage = {
            "prompt_tokens": sum(p["prompt_tokens"] for p in points),
            "completion_tokens": sum(p["completion_tokens"] for p in points),
            "token_samples": sum(p["token_samples"] for p in points),
            "cost_samples": cost_samples,
            "query_responses": sum(p["query_responses"] for p in points),
            "cost_usd": sum(p["cost_usd"] or 0 for p in points) if cost_samples else None,
        }
    total = sum(p["requests"] for p in points)
    errors = sum(p["errors"] for p in points)
    samples = sum(p["latency_samples"] for p in points)
    latency = (
        sum((p["latency_ms"] or 0) * p["latency_samples"] for p in points) / samples
        if samples
        else None
    )
    return {
        "days": days,
        "scope": "Dashboard API calls" if member.role == "admin" else "Your dashboard API calls",
        "requests": total,
        "errors": errors,
        "success_rate": (total - errors) * 100 / total if total else None,
        "error_rate": errors * 100 / total if total else None,
        "latency_ms": latency,
        "latency_samples": samples,
        "usage": usage,
        "series": points,
    }

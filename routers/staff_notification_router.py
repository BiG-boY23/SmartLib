"""Personal staff inbox; read state never changes another recipient's inbox."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from database import get_db
from models import Notification, User
from security import require_roles
from staff_activity import STAFF_ROLES


router = APIRouter(prefix="/admin/notifications", tags=["Staff notifications"])
staff_only = require_roles(list(STAFF_ROLES))


@router.get("")
def list_staff_notifications(response: Response, limit: int = Query(100, ge=1, le=100),
                             db: Session = Depends(get_db),
                             current_user: User = Depends(staff_only)):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    unread = query.filter(Notification.read_at.is_(None)).count()
    rows = query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all()
    response.headers["Cache-Control"] = "no-store"
    return {"unread_count": unread, "items": [
        {"id": row.id, "title": row.title, "body": row.body, "kind": row.kind,
         "created_at": row.created_at, "read_at": row.read_at, "read": row.read_at is not None}
        for row in rows
    ]}


@router.post("/read-all")
def mark_all_staff_notifications_read(db: Session = Depends(get_db),
                                      current_user: User = Depends(staff_only)):
    changed = db.query(Notification).filter(
        Notification.user_id == current_user.id, Notification.read_at.is_(None)
    ).update({Notification.read_at: datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return {"message": "Notifications marked as read.", "updated_count": changed}


@router.post("/{notification_id}/read")
def mark_staff_notification_read(notification_id: int, db: Session = Depends(get_db),
                                 current_user: User = Depends(staff_only)):
    row = db.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == current_user.id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        db.commit()
    return {"message": "Notification marked as read."}

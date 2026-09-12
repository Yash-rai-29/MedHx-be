import datetime
import logging
from common_code.notification_dispatcher import dispatch_notification

logger = logging.getLogger(__name__)


async def send_export_completed_notification(
    patient_id: str,
    export_id: str,
    download_url: str | None,
    expires_at: datetime.datetime,
    file_size_bytes: int,
) -> bool:
    """Dispatches FCM push notification and writes in-app notification when export is ready."""
    try:
        success = await dispatch_notification(
            patient_id=patient_id,
            title="Medical Data Export Ready 📦",
            body="Your requested health records archive is ready for download (valid for 24 hours).",
            notification_type="export",
            extra_data={
                "export_id": export_id,
                "download_url": download_url or "",
                "expires_at": expires_at.isoformat(),
                "file_size_bytes": str(file_size_bytes),
            },
            deeplink=f"/profile/export/{export_id}",
        )
        logger.info(f"FCM notification sent for export {export_id} (success={success})")
        return success
    except Exception as e:
        logger.warning(f"Failed to send FCM export notification for {export_id}: {e}")
        return False

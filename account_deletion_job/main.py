import argparse
import asyncio
import datetime
import logging
import os
import sys

from common_code.config import settings
from common_code.firestore import get_db
from account_deletion_job.deletion_engine import permanently_delete_patient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("account_deletion_job")


async def run_deletion_job(target_patient_id: str | None = None) -> int:
    """
    Scans account_deletion_requests in Firestore:
    1. If target_patient_id is provided, purges that specific account immediately.
    2. Otherwise, finds all records with status == 'pending_deletion' where scheduled_deletion_at <= now.
    Permanently deletes all health records, Firebase Auth user, and Cloud Storage media.
    """
    db = get_db()
    now = datetime.datetime.now(datetime.UTC)

    if target_patient_id:
        logger.info(f"Executing immediate deletion for specific target patient: {target_patient_id}")
        try:
            res = await permanently_delete_patient(uid=target_patient_id, db=db)
            logger.info(f"Successfully deleted patient {target_patient_id}: {res}")
            return 0
        except Exception as e:
            logger.error(f"Failed to delete patient {target_patient_id}: {e}", exc_info=True)
            return 1

    logger.info(f"Scanning '{settings.ACCOUNT_DELETIONS_COLLECTION}' for requests eligible for purge (scheduled_deletion_at <= {now.isoformat()})...")

    snaps = await (
        db.collection(settings.ACCOUNT_DELETIONS_COLLECTION)
        .where("status", "==", "pending_deletion")
        .get()
    )

    eligible_uids = []
    for s in snaps:
        d = s.to_dict() or {}
        sched = d.get("scheduled_deletion_at")
        if isinstance(sched, datetime.datetime):
            if sched <= now:
                eligible_uids.append(s.id)
        elif sched is None:
            # Fallback if no schedule timestamp, check requested_at + grace period
            req_at = d.get("requested_at")
            grace_hours = settings.ACCOUNT_DELETION_GRACE_PERIOD_HOURS
            if isinstance(req_at, datetime.datetime) and (req_at + datetime.timedelta(hours=grace_hours)) <= now:
                eligible_uids.append(s.id)

    logger.info(f"Found {len(eligible_uids)} account(s) ready for permanent deletion.")

    success_count = 0
    failure_count = 0

    for uid in eligible_uids:
        logger.info(f"Starting permanent purge for account: {uid}...")
        try:
            # 1. Optimistic lock: set status to 'processing'
            await db.collection(settings.ACCOUNT_DELETIONS_COLLECTION).document(uid).update({
                "status": "processing",
                "processing_started_at": datetime.datetime.now(datetime.UTC),
            })

            # 2. Execute full permanent purge
            await permanently_delete_patient(uid=uid, db=db)
            success_count += 1
            logger.info(f"Successfully purged account {uid}.")
        except Exception as e:
            failure_count += 1
            logger.error(f"Failed to purge account {uid}: {e}", exc_info=True)
            try:
                await db.collection(settings.ACCOUNT_DELETIONS_COLLECTION).document(uid).update({
                    "status": "failed",
                    "error_message": str(e),
                    "failed_at": datetime.datetime.now(datetime.UTC),
                })
            except Exception:
                pass

    logger.info(f"Account deletion job finished. Processed: {len(eligible_uids)}, Success: {success_count}, Failures: {failure_count}")
    return 0 if failure_count == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="AI Health Companion Account Deletion Job")
    parser.add_argument("--patient-id", type=str, default=os.getenv("PATIENT_ID"), help="Optional specific patient UID to purge")
    args = parser.parse_args()

    exit_code = asyncio.run(run_deletion_job(target_patient_id=args.patient_id))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

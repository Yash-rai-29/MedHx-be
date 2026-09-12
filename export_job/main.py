#!/usr/bin/env python3
"""
Cloud Run Job Entrypoint: Patient Data Export Runner
Parses EXPORT_ID and PATIENT_ID from arguments or environment variables and runs the export pipeline.
"""

import argparse
import asyncio
import logging
import os
import sys

from common_code.firestore import get_db
from export_job.export_engine import execute_export

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("export_job")


def parse_args():
    parser = argparse.ArgumentParser(description="Cloud Run Job: Patient Medical Data Exporter")
    parser.add_argument(
        "--export-id",
        type=str,
        default=os.getenv("EXPORT_ID"),
        help="Firestore Export document ID",
    )
    parser.add_argument(
        "--patient-id",
        type=str,
        default=os.getenv("PATIENT_ID"),
        help="Patient Firebase UID",
    )
    return parser.parse_args()


async def main_async():
    args = parse_args()
    export_id = args.export_id
    patient_id = args.patient_id

    if not export_id or not patient_id:
        logger.error("Missing required arguments. Both EXPORT_ID and PATIENT_ID must be provided.")
        sys.exit(1)

    logger.info(f"Starting Cloud Run Job: export_id={export_id}, patient_id={patient_id}")
    db = get_db()

    try:
        res = await execute_export(export_id=export_id, patient_id=patient_id, db=db)
        logger.info(f"Export Job successfully finished for {export_id}: {res.get('file_size_bytes')} bytes")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error executing export {export_id}: {e}", exc_info=True)
        sys.exit(1)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

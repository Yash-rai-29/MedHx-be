import asyncio
import datetime
import os
import sys

# Add workspace directory to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common_code.firestore import get_db
from common_code.config import settings

async def main():
    db = get_db()
    
    privacy_policy = {
        "doc_type": "privacy_policy",
        "title": "Privacy Policy",
        "content_markdown": "# Privacy Policy\n\nYour privacy is important to us. This policy describes how we collect, use, and process your health data...\n\n- We protect patient data\n- No third-party sharing\n- Safe clinical AI companion",
        "version": "1.0.0",
        "updated_at": datetime.datetime.now(datetime.UTC)
    }
    
    terms_of_service = {
        "doc_type": "terms_of_service",
        "title": "Terms of Service",
        "content_markdown": "# Terms of Service\n\nWelcome to AI Health Companion. By using this service, you agree to these terms:\n\n- Non-diagnostic AI only\n- Reminders are aids, consult doctors\n- Data lifecycle compliance",
        "version": "1.0.0",
        "updated_at": datetime.datetime.now(datetime.UTC)
    }
    
    await db.collection(settings.LEGAL_COLLECTION).document("privacy_policy_1.0.0").set(privacy_policy)
    await db.collection(settings.LEGAL_COLLECTION).document("terms_of_service_1.0.0").set(terms_of_service)
    print("Legal documents created successfully in Firestore collection:", settings.LEGAL_COLLECTION)

if __name__ == "__main__":
    asyncio.run(main())

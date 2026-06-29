import pytest
import datetime
import base64
import json
from unittest.mock import patch
from common_code.config import settings

def test_get_and_update_reminders(client, mock_db, mock_user):
    # Initialize some mock reminders in firestore matching new ReminderResponse schema
    mock_db.db_store[settings.REMINDERS_COLLECTION] = {
        "reminder-1": {
            "id": "reminder-1",
            "patientId": mock_user["uid"],
            "type": "medicine",
            "status": "active",
            "title": "Take Paracetamol",
            "notes": "With water",
            "schedule": {
                "recurrence": "daily",
                "start_date": "2026-06-27",
                "time_of_day": "09:00"
            },
            "medicine_details": {
                "name": "Paracetamol",
                "dosage": "500mg"
            },
            "notification_enabled": True,
            "next_trigger_at": datetime.datetime.now(datetime.UTC),
            "created_at": datetime.datetime.now(datetime.UTC)
        }
    }
    
    # 1. Test List Reminders
    response = client.get("/reminders")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "reminder-1"
    assert data[0]["title"] == "Take Paracetamol"
    
    # 2. Test Update Reminder (Pause it)
    update_payload = {"status": "paused", "notification_enabled": False}
    response_put = client.put("/reminders/reminder-1", json=update_payload)
    assert response_put.status_code == 200
    updated_data = response_put.json()
    assert updated_data["status"] == "paused"
    assert updated_data["notification_enabled"] is False
    
    # Verify in DB
    db_record = mock_db.db_store[settings.REMINDERS_COLLECTION]["reminder-1"]
    assert db_record["status"] == "paused"
    assert db_record["notification_enabled"] is False

def test_trigger_notification_callback(client, mock_db, mock_user):
    # Initialize user profile with FCM token
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Arjun Kumar",
            "fcm_token": "mock-fcm-token-12345"
        }
    }
    
    # Initialize active reminder
    mock_db.db_store[settings.REMINDERS_COLLECTION] = {
        "reminder-2": {
            "id": "reminder-2",
            "patientId": mock_user["uid"],
            "type": "medicine",
            "status": "active",
            "title": "Take Amoxicillin",
            "notes": None,
            "schedule": {
                "recurrence": "daily",
                "start_date": "2026-06-27",
                "time_of_day": "13:30"
            },
            "medicine_details": {
                "name": "Amoxicillin",
                "dosage": "500mg"
            },
            "notification_enabled": True,
            "next_trigger_at": datetime.datetime.now(datetime.UTC),
            "created_at": datetime.datetime.now(datetime.UTC)
        }
    }
    
    # Trigger callback
    payload = {
        "reminder_id": "reminder-2",
        "patient_id": mock_user["uid"],
        "target_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "type": "notify"
    }
    
    with patch("common_code.notification_dispatcher.messaging.send") as mock_send, \
         patch("patient_service.reminders.reminders_func.create_cloud_task") as mock_cloud_task:
        mock_send.return_value = "projects/mock/messages/12345"
        headers = {"X-Cloud-Tasks-Secret": "local-tasks-secret"}
        
        from firebase_admin import messaging as fb_messaging
        fb_messaging.Message.reset_mock()
        fb_messaging.Notification.reset_mock()
        
        response = client.post("/reminders/trigger", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        
        # Verify push was sent
        assert fb_messaging.Message.call_count == 1
        _, kwargs = fb_messaging.Message.call_args
        assert kwargs["token"] == "mock-fcm-token-12345"
        
        assert fb_messaging.Notification.call_count == 1
        _, notif_kwargs = fb_messaging.Notification.call_args
        assert "Time for your medicine" in notif_kwargs["title"]
        assert "Amoxicillin" in notif_kwargs["body"]
 
        assert kwargs["data"]["type"] == "medicine"
        assert kwargs["data"]["patient_id"] == mock_user["uid"]
        assert kwargs["data"]["reminder_id"] == "reminder-2"
        assert "notification_id" in kwargs["data"]

def test_manual_reminder_creation(client, mock_db, mock_user):
    payload = {
        "type": "medicine",
        "title": "Take Vitamin D",
        "notes": "With morning tea",
        "schedule": {
            "recurrence": "once",
            "start_date": "2026-07-01",
            "time_of_day": "08:00"
        },
        "medicine_details": {
            "name": "Vitamin D",
            "dosage": "1 tablet"
        },
        "notification_enabled": True
    }
    
    with patch("patient_service.reminders.reminders_func.create_cloud_task") as mock_schedule:
        mock_schedule.return_value = "projects/medhx-care-ai/queues/notification-queue/tasks/task-999"
        
        response = client.post("/reminders", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] is not None
        assert data["title"] == "Take Vitamin D"
        assert data["schedule"]["time_of_day"] == "08:00"
        assert data["schedule"]["recurrence"] == "once"
        assert data["status"] == "active"
        
        # Verify in Mock DB
        db_record = mock_db.db_store[settings.REMINDERS_COLLECTION][data["id"]]
        assert db_record["patientId"] == mock_user["uid"]
        assert db_record["type"] == "medicine"
        assert db_record["title"] == "Take Vitamin D"
        assert db_record["schedule"]["time_of_day"] == "08:00"
        assert db_record["notification_enabled"] is True
        assert db_record["status"] == "active"
        assert isinstance(db_record["next_trigger_at"], datetime.datetime)

def test_pubsub_handler_creates_reminders(client, mock_db, mock_user):
    # Setup mock user profile
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "meal_times": {
                "breakfast": "08:00",
                "lunch": "13:00",
                "dinner": "20:00"
            }
        }
    }
    
    # Lock the test run date and time to avoid rollover issues
    from zoneinfo import ZoneInfo
    now_local = datetime.datetime(2026, 6, 28, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")) # 9:00 AM IST
    time_1 = (now_local + datetime.timedelta(hours=2)).strftime("%H:%M")
    time_2 = (now_local + datetime.timedelta(hours=3)).strftime("%H:%M")

    # Define Pub/Sub event payload matching expected reminder suggestions schema
    event_payload = {
        "consultation_id": "consult-111",
        "patient_id": mock_user["uid"],
        "doctor_id": "doc-222",
        "reminder_suggestions": [
            {
                "type": "medicine",
                "title": "Take Metformin",
                "notes": "after breakfast",
                "medicine_details": {
                    "name": "Metformin",
                    "dosage": "500mg"
                },
                "suggested_schedule": {
                    "recurrence": "daily",
                    "time_of_day": time_1
                }
            },
            {
                "type": "follow_up",
                "title": "Cardiology Consultation",
                "notes": "routine visit",
                "follow_up_details": {
                    "specialty": "Cardiology",
                    "urgency": "routine"
                },
                "suggested_schedule": {
                    "recurrence": "once",
                    "time_of_day": time_2
                }
            }
        ]
    }
    
    encoded_payload = base64.b64encode(json.dumps(event_payload).encode("utf-8")).decode("utf-8")
    
    envelope = {
        "message": {
            "data": encoded_payload,
            "messageId": "msg-pubsub-123"
        },
        "subscription": "projects/medhx-care-ai/subscriptions/consultation-published-sub"
    }
    
    # Custom MockDatetime class subclassing datetime.datetime
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            val = datetime.datetime(2026, 6, 28, 3, 30, tzinfo=datetime.UTC)
            if tz:
                return val.astimezone(tz)
            return val

    with patch("patient_service.reminders.reminders_func.datetime", MockDatetime), \
         patch("patient_service.reminders.reminders_router._datetime", MockDatetime), \
         patch("patient_service.reminders.reminders_func.create_cloud_task") as mock_schedule:
        mock_schedule.return_value = "projects/medhx-care-ai/queues/notification-queue/tasks/task-mock"
        
        response = client.post("/reminders/pubsub-handler", json=envelope)
        assert response.status_code == 200
        res = response.json()
        assert res["status"] == "ok", f"Status not ok: {res}"
        assert res["created"] == 2, f"Created {res['created']} instead of 2. Failed: {res.get('failed')}"
        
        # Verify reminders written to Firestore
        reminders = mock_db.db_store[settings.REMINDERS_COLLECTION]
        assert len(reminders) == 2
        
        med_reminders = [r for r in reminders.values() if r["type"] == "medicine"]
        assert len(med_reminders) == 1
        assert med_reminders[0]["schedule"]["time_of_day"] == time_1
        assert med_reminders[0]["patientId"] == mock_user["uid"]
        assert med_reminders[0]["title"] == "Take Metformin"
        
        followup_reminders = [r for r in reminders.values() if r["type"] == "follow_up"]
        assert len(followup_reminders) == 1
        assert followup_reminders[0]["patientId"] == mock_user["uid"]
        assert followup_reminders[0]["schedule"]["time_of_day"] == time_2
        assert followup_reminders[0]["title"] == "Cardiology Consultation"

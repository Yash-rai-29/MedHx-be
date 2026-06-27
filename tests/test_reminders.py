import pytest
import datetime
import base64
import json
from unittest.mock import patch
from common_code.config import settings

def test_get_and_update_reminders(client, mock_db, mock_user):
    # Initialize some mock reminders in firestore
    mock_db.db_store[settings.REMINDERS_COLLECTION] = {
        "reminder-1": {
            "patientId": mock_user["uid"],
            "type": "medicine",
            "title": "Take Paracetamol",
            "schedule": "09:00",
            "mealRelativeTiming": "AFTER_FOOD",
            "notificationEnabled": True,
            "status": "active"
        }
    }
    
    # 1. Test List Reminders
    response = client.get("/reminders")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "reminder-1"
    assert data[0]["title"] == "Take Paracetamol"
    
    # 2. Test Update Reminder (Mark Completed)
    update_payload = {"status": "completed", "notification_enabled": False}
    response_put = client.put("/reminders/reminder-1", json=update_payload)
    assert response_put.status_code == 200
    updated_data = response_put.json()
    assert updated_data["status"] == "completed"
    assert updated_data["notification_enabled"] is False
    
    # Verify in DB
    db_record = mock_db.db_store[settings.REMINDERS_COLLECTION]["reminder-1"]
    assert db_record["status"] == "completed"
    assert db_record["notificationEnabled"] is False

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
            "patientId": mock_user["uid"],
            "type": "medicine",
            "title": "Take Amoxicillin",
            "schedule": "13:30",
            "mealRelativeTiming": "AFTER_FOOD",
            "notificationEnabled": True,
            "status": "active"
        }
    }
    
    # Trigger callback
    payload = {
        "reminder_id": "reminder-2",
        "patient_id": mock_user["uid"]
    }
    
    with patch("common_code.notification_dispatcher.send_push_notification") as mock_send_push:
        mock_send_push.return_value = "projects/mock/messages/12345"
        
        response = client.post("/reminders/trigger-notification", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        
        # Verify push was sent
        assert mock_send_push.call_count == 1
        _, kwargs = mock_send_push.call_args
        assert kwargs["token"] == "mock-fcm-token-12345"
        assert kwargs["title"] == "Time for your medicine, Arjun! ⏰"
        assert kwargs["body"] == "Please take your Amoxicillin (after food)."

        assert kwargs["data"]["type"] == "medicine"
        assert kwargs["data"]["patient_id"] == mock_user["uid"]
        assert kwargs["data"]["reminder_id"] == "reminder-2"
        assert "notification_id" in kwargs["data"]
        assert kwargs["data"]["deeplink"] == "/reminders/reminder-2"

        
        # Verify reminder status is now completed in Firestore
        db_record = mock_db.db_store[settings.REMINDERS_COLLECTION]["reminder-2"]
        assert db_record["status"] == "completed"


def test_manual_reminder_creation(client, mock_db, mock_user):
    payload = {
        "type": "medicine",
        "title": "Take Vitamin D",
        "schedule_time": "08:00",
        "meal_relation": "BEFORE_FOOD",
        "notification_enabled": True,
        "target_date": "2026-07-01"
    }
    
    with patch("patient_service.reminders.reminders_func.schedule_notification_task") as mock_schedule:
        mock_schedule.return_value = "projects/medhx-care-ai/queues/notification-queue/tasks/task-999"
        
        response = client.post("/reminders", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] is not None
        assert data["title"] == "Take Vitamin D"
        assert data["schedule_time"] == "08:00"
        assert data["meal_relation"] == "BEFORE_FOOD"
        assert data["target_date"] == "2026-07-01"
        assert data["status"] == "active"
        
        # Verify in Mock DB
        db_record = mock_db.db_store[settings.REMINDERS_COLLECTION][data["id"]]
        assert db_record["patientId"] == mock_user["uid"]
        assert db_record["type"] == "medicine"
        assert db_record["title"] == "Take Vitamin D"
        assert db_record["schedule"] == "08:00"
        assert db_record["mealRelativeTiming"] == "BEFORE_FOOD"
        assert db_record["notificationEnabled"] is True
        assert db_record["status"] == "active"
        assert db_record["cloud_task_name"] == "projects/medhx-care-ai/queues/notification-queue/tasks/task-999"
        assert isinstance(db_record["targetDate"], datetime.datetime)
        assert db_record["targetDate"].year == 2026
        assert db_record["targetDate"].month == 7
        assert db_record["targetDate"].day == 1


def test_pubsub_handler_creates_reminders(client, mock_db, mock_user):
    # Setup mock user profile with meal times
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "meal_times": {
                "breakfast": "08:00",
                "lunch": "13:00",
                "dinner": "20:00"
            }
        }
    }
    
    # Define Pub/Sub event payload
    event_payload = {
        "consultation_id": "consult-111",
        "patient_id": mock_user["uid"],
        "doctor_id": "doc-222",
        "medicines": [
            {"name": "Metformin", "dosage": "twice daily", "meal_relation": "AFTER_FOOD"}
        ],
        "follow_up_days": 7
    }
    
    encoded_payload = base64.b64encode(json.dumps(event_payload).encode("utf-8")).decode("utf-8")
    
    envelope = {
        "message": {
            "data": encoded_payload,
            "messageId": "msg-pubsub-123"
        },
        "subscription": "projects/medhx-care-ai/subscriptions/consultation-published-sub"
    }
    
    with patch("patient_service.reminders.reminders_func.schedule_notification_task") as mock_schedule:
        mock_schedule.return_value = "projects/medhx-care-ai/queues/notification-queue/tasks/task-mock"
        
        response = client.post("/reminders/pubsub-handler", json=envelope)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        
        # Verify reminders written to Firestore
        reminders = mock_db.db_store[settings.REMINDERS_COLLECTION]
        # We expect 2 medicine reminders + 1 follow-up reminder = 3 total
        assert len(reminders) == 3
        
        med_reminders = [r for r in reminders.values() if r["type"] == "medicine"]
        assert len(med_reminders) == 2
        schedules = {r["schedule"] for r in med_reminders}
        assert schedules == {"08:30", "20:30"}
        assert all(r["patientId"] == mock_user["uid"] for r in med_reminders)
        assert all("Metformin" in r["title"] for r in med_reminders)
        
        followup_reminders = [r for r in reminders.values() if r["type"] == "follow-up"]
        assert len(followup_reminders) == 1
        assert followup_reminders[0]["patientId"] == mock_user["uid"]
        assert followup_reminders[0]["schedule"] == "10:00"
        assert isinstance(followup_reminders[0]["targetDate"], datetime.datetime)

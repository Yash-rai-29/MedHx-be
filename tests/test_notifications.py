import pytest
import datetime
from unittest.mock import patch
from common_code.config import settings
from common_code.notification_dispatcher import dispatch_notification

@pytest.mark.anyio
async def test_dispatch_notification_creates_db_record(client, mock_db, mock_user):
    # Setup mock user profile
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Jane Doe",
            "fcm_token": "token-123"
        }
    }
    # We patch messaging.send to return a mock message ID
    with patch("common_code.notification_dispatcher.messaging.send") as mock_send:
        mock_send.return_value = "msg-111"
        
        success = await dispatch_notification(
            patient_id=mock_user["uid"],
            title="Lab Result Ready",
            body="Your recent blood work report is available.",
            notification_type="report",
            extra_data={"document_id": "doc-99"},
            deeplink="/documents/doc-99"
        )
        
        assert success is True
        
        # Verify document was created in firestore
        notifications = mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]
        assert len(notifications) == 1
        
        notif_id = list(notifications.keys())[0]
        notif_data = notifications[notif_id]
        
        assert notif_data["patientId"] == mock_user["uid"]
        assert notif_data["title"] == "Lab Result Ready"
        assert notif_data["body"] == "Your recent blood work report is available."
        assert notif_data["deeplink"] == "/documents/doc-99"
        assert notif_data["isRead"] is False
        assert notif_data["type"] == "report"
        assert notif_data["extraData"] == {"document_id": "doc-99"}
        assert notif_data["pushStatus"] == "sent"
        assert notif_data["pushMessageId"] == "msg-111"
        assert isinstance(notif_data["createdAt"], datetime.datetime)

def test_list_notifications(client, mock_db, mock_user):
    # Setup mock notifications with different timestamps
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.NOTIFICATIONS_COLLECTION] = {
        "notif-new": {
            "patientId": mock_user["uid"],
            "title": "New Alert",
            "body": "New alert body",
            "isRead": False,
            "createdAt": now,
            "type": "medicine",
            "pushStatus": "skipped_no_token"
        },
        "notif-old": {
            "patientId": mock_user["uid"],
            "title": "Old Alert",
            "body": "Old alert body",
            "isRead": True,
            "createdAt": now - datetime.timedelta(hours=1),
            "type": "general",
            "pushStatus": "skipped_no_token"
        },
        "notif-other-user": {
            "patientId": "other-patient-uid",
            "title": "Other Alert",
            "body": "Other alert body",
            "isRead": False,
            "createdAt": now,
            "type": "medicine",
            "pushStatus": "skipped_no_token"
        }
    }

    response = client.get("/notifications")
    assert response.status_code == 200
    data = response.json()

    # Should only return notifications for the authenticated user, ordered descending by createdAt
    assert "notifications" in data
    notifications = data["notifications"]
    assert len(notifications) == 2

    # Order verification (newest first)
    assert notifications[0]["id"] == "notif-new"
    assert notifications[0]["title"] == "New Alert"
    assert notifications[0]["is_read"] is False

    assert notifications[1]["id"] == "notif-old"
    assert notifications[1]["title"] == "Old Alert"
    assert notifications[1]["is_read"] is True

    # When all docs fit in one page, no cursor is returned
    assert data["next_cursor"] is None


def test_notification_cursor_pagination(client, mock_db, mock_user):
    """Verify that next_cursor is returned when more pages exist and can be used to fetch older pages."""
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.NOTIFICATIONS_COLLECTION] = {
        "notif-1": {
            "patientId": mock_user["uid"],
            "title": "Newest",
            "body": "",
            "isRead": False,
            "createdAt": now,
            "type": "medicine",
            "pushStatus": "skipped_no_token"
        },
        "notif-2": {
            "patientId": mock_user["uid"],
            "title": "Middle",
            "body": "",
            "isRead": False,
            "createdAt": now - datetime.timedelta(hours=1),
            "type": "general",
            "pushStatus": "skipped_no_token"
        },
        "notif-3": {
            "patientId": mock_user["uid"],
            "title": "Oldest",
            "body": "",
            "isRead": True,
            "createdAt": now - datetime.timedelta(hours=2),
            "type": "general",
            "pushStatus": "skipped_no_token"
        },
    }

    # Fetch first page (limit=2), expecting a cursor back
    resp1 = client.get("/notifications?limit=2")
    assert resp1.status_code == 200
    page1 = resp1.json()
    assert len(page1["notifications"]) == 2
    assert page1["notifications"][0]["id"] == "notif-1"
    assert page1["notifications"][1]["id"] == "notif-2"
    assert page1["next_cursor"] is not None

    # Use the cursor to fetch the second page
    cursor = page1["next_cursor"]
    resp2 = client.get(f"/notifications?limit=2&before={cursor}")
    assert resp2.status_code == 200
    page2 = resp2.json()
    assert len(page2["notifications"]) == 1
    assert page2["notifications"][0]["id"] == "notif-3"
    # No more pages
    assert page2["next_cursor"] is None

def test_mark_notification_as_read(client, mock_db, mock_user):
    mock_db.db_store[settings.NOTIFICATIONS_COLLECTION] = {
        "notif-1": {
            "patientId": mock_user["uid"],
            "title": "Unread Alert",
            "body": "Alert body",
            "isRead": False,
            "createdAt": datetime.datetime.now(datetime.UTC),
            "type": "general",
            "pushStatus": "skipped_no_token"
        },
        "notif-other": {
            "patientId": "other-user",
            "title": "Other User Alert",
            "body": "Alert body",
            "isRead": False,
            "createdAt": datetime.datetime.now(datetime.UTC),
            "type": "general",
            "pushStatus": "skipped_no_token"
        }
    }
    
    # 1. Try to read a notification that doesn't exist
    resp = client.post("/notifications/notif-none/read")
    assert resp.status_code == 404
    
    # 2. Try to read a notification belonging to another user
    resp = client.post("/notifications/notif-other/read")
    assert resp.status_code == 403
    
    # 3. Read own notification successfully
    resp = client.post("/notifications/notif-1/read")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    
    # Verify in DB
    db_record = mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]["notif-1"]
    assert db_record["isRead"] is True

def test_mark_all_notifications_as_read(client, mock_db, mock_user):
    mock_db.db_store[settings.NOTIFICATIONS_COLLECTION] = {
        "notif-1": {
            "patientId": mock_user["uid"],
            "isRead": False,
            "createdAt": datetime.datetime.now(datetime.UTC)
        },
        "notif-2": {
            "patientId": mock_user["uid"],
            "isRead": False,
            "createdAt": datetime.datetime.now(datetime.UTC)
        },
        "notif-3": {
            "patientId": "other-user",
            "isRead": False,
            "createdAt": datetime.datetime.now(datetime.UTC)
        }
    }
    
    resp = client.post("/notifications/read-all")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    
    # Verify both notifications of mock_user are read
    assert mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]["notif-1"]["isRead"] is True
    assert mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]["notif-2"]["isRead"] is True
    # Other user notification remains unread
    assert mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]["notif-3"]["isRead"] is False

@pytest.mark.anyio
async def test_notification_templates_and_formatting(client, mock_db, mock_user):
    # Setup mock user profile
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Jane Doe",
            "fcm_token": "token-123"
        }
    }
    
    # Setup mock reminder
    mock_db.db_store[settings.REMINDERS_COLLECTION] = {
        "rem-456": {
            "patientId": mock_user["uid"],
            "type": "medicine",
            "title": "Take Paracetamol",
            "schedule": "09:00",
            "mealRelativeTiming": "AFTER_FOOD",
            "notificationEnabled": True,
            "status": "active"
        }
    }

    # Test 1: Resolve using template defaults (title and body are None)
    with patch("common_code.notification_dispatcher.messaging.send") as mock_send:
        mock_send.return_value = "msg-222"
        
        success = await dispatch_notification(
            patient_id=mock_user["uid"],
            title=None,
            body=None,
            notification_type="medicine",
            extra_data={"reminder_id": "rem-456"}
        )
        
        assert success is True
        notifications = mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]
        
        # Get the latest created document
        latest_notif = sorted(
            notifications.values(),
            key=lambda x: x["createdAt"],
            reverse=True
        )[0]
        
        assert latest_notif["title"] == "Time for your medicine, Jane! ⏰"
        assert latest_notif["body"] == "Please take your Paracetamol (after food)."
        assert latest_notif["deeplink"] == "/reminders/rem-456"


    # Test 2: Resolve with custom formatting & key missing safety
    with patch("common_code.notification_dispatcher.messaging.send") as mock_send:
        mock_send.return_value = "msg-333"
        
        success = await dispatch_notification(
            patient_id=mock_user["uid"],
            title="Update on {item_name}",
            body="Your request for {item_name} is {status}. Code: {missing_code}",
            notification_type="general",
            extra_data={"item_name": "Blood Report", "status": "approved"}
        )
        
        assert success is True
        notifications = mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]
        latest_notif = sorted(
            notifications.values(),
            key=lambda x: x["createdAt"],
            reverse=True
        )[0]
        
        assert latest_notif["title"] == "Update on Blood Report"
        assert latest_notif["body"] == "Your request for Blood Report is approved. Code: {missing_code}"
        assert latest_notif["deeplink"] == "/"


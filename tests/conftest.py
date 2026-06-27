import sys
from unittest.mock import MagicMock, AsyncMock

# ── Mock Firebase Admin SDK globally before imports ───────────
mock_firebase = MagicMock()
mock_auth = MagicMock()
mock_messaging = MagicMock()
mock_creds = MagicMock()

# Inject into sys.modules
sys.modules['firebase_admin'] = mock_firebase
sys.modules['firebase_admin.auth'] = mock_auth
sys.modules['firebase_admin.messaging'] = mock_messaging
sys.modules['firebase_admin.credentials'] = mock_creds
sys.modules['google.cloud.secretmanager'] = MagicMock()

# Attach submodules to parent mock
mock_firebase.auth = mock_auth
mock_firebase.messaging = mock_messaging
mock_firebase.credentials = mock_creds

# Setup return values for common functions
mock_auth.verify_id_token.return_value = {
    "uid": "test-patient-123",
    "email": "patient@example.com",
    "name": "Test Patient",
    "role": "patient"
}
mock_auth.set_custom_user_claims.return_value = None

import pytest
from fastapi.testclient import TestClient

# ── Mock Firestore DB implementation ───────────────────────────
class MockDocumentSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data or {}

class MockDocumentReference:
    def __init__(self, doc_id, collection_store):
        self.id = doc_id
        self.collection_store = collection_store

    async def set(self, data):
        self.collection_store[self.id] = data

    async def get(self):
        data = self.collection_store.get(self.id)
        return MockDocumentSnapshot(self.id, data)

    async def update(self, data):
        if self.id not in self.collection_store:
            self.collection_store[self.id] = {}
        doc = self.collection_store[self.id]
        for key, value in data.items():
            # Support Firestore dotted-field path notation: "fcm_tokens.ios" -> nested dict
            parts = key.split(".")
            target = doc
            for part in parts[:-1]:
                if part not in target or not isinstance(target[part], dict):
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value

class MockQuery:
    def __init__(self, collection_store, filters=None):
        self.collection_store = collection_store
        self.filters = filters or []
        self._order_field: str | None = None
        self._order_desc: bool = False
        self._limit_val: int | None = None
        self._start_after_val: dict | None = None

    def where(self, field, op, value):
        new_filters = self.filters + [(field, op, value)]
        q = MockQuery(self.collection_store, new_filters)
        q._order_field = self._order_field
        q._order_desc = self._order_desc
        q._limit_val = self._limit_val
        q._start_after_val = self._start_after_val
        return q

    def order_by(self, field, direction=None):
        from google.cloud import firestore as _fs
        q = MockQuery(self.collection_store, self.filters)
        q._order_field = field
        q._order_desc = (direction == _fs.Query.DESCENDING)
        q._limit_val = self._limit_val
        q._start_after_val = self._start_after_val
        return q

    def limit(self, value):
        q = MockQuery(self.collection_store, self.filters)
        q._order_field = self._order_field
        q._order_desc = self._order_desc
        q._limit_val = value
        q._start_after_val = self._start_after_val
        return q

    def start_after(self, doc_snapshot_or_dict):
        q = MockQuery(self.collection_store, self.filters)
        q._order_field = self._order_field
        q._order_desc = self._order_desc
        q._limit_val = self._limit_val
        q._start_after_val = doc_snapshot_or_dict
        return q

    async def get(self):
        results = []
        for doc_id, data in self.collection_store.items():
            match = True
            for field, op, value in self.filters:
                val = data.get(field)
                if op == "==" and val != value:
                    match = False
                elif op == ">" and not (val > value):
                    match = False
            if match:
                results.append(MockDocumentSnapshot(doc_id, data))

        # Apply ordering
        if self._order_field:
            results.sort(
                key=lambda s: s._data.get(self._order_field) or 0,
                reverse=self._order_desc
            )

        # Apply start_after cursor (Firestore semantics: exclusive of the cursor value)
        if self._start_after_val and self._order_field:
            cursor_val = self._start_after_val.get(self._order_field)
            import datetime as _dt
            # Normalise both values to naive UTC for safe comparison
            def _naive(v):
                if isinstance(v, _dt.datetime) and v.tzinfo is not None:
                    return v.replace(tzinfo=None)
                return v
            cursor_val = _naive(cursor_val)
            cut = len(results)
            for i, snap in enumerate(results):
                snap_val = _naive(snap._data.get(self._order_field))
                if snap_val is None:
                    continue
                if self._order_desc:
                    if snap_val < cursor_val:
                        cut = i
                        break
                else:
                    if snap_val > cursor_val:
                        cut = i
                        break
            results = results[cut:]


        # Apply limit
        if self._limit_val is not None:
            results = results[:self._limit_val]

        return results

class MockCollectionReference:
    def __init__(self, name, db_store):
        self.name = name
        self.db_store = db_store
        if name not in self.db_store:
            self.db_store[name] = {}

    @property
    def store(self):
        return self.db_store[self.name]

    def document(self, doc_id):
        return MockDocumentReference(doc_id, self.store)

    def where(self, field, op, value):
        return MockQuery(self.store).where(field, op, value)

    def limit(self, value):
        return MockQuery(self.store).limit(value)

    async def get(self):
        return await MockQuery(self.store).get()

    async def add(self, data):
        import uuid
        doc_id = str(uuid.uuid4())
        self.store[doc_id] = data
        ref = MockDocumentReference(doc_id, self.store)
        return None, ref

class MockWriteBatch:
    def __init__(self, db_store):
        self.db_store = db_store
        self.operations = []

    def set(self, doc_ref, data):
        self.operations.append(("set", doc_ref, data))

    def update(self, doc_ref, data):
        self.operations.append(("update", doc_ref, data))

    def delete(self, doc_ref):
        self.operations.append(("delete", doc_ref, None))

    async def commit(self):
        for op_type, doc_ref, data in self.operations:
            if op_type == "set":
                doc_ref.collection_store[doc_ref.id] = data
            elif op_type == "update":
                if doc_ref.id not in doc_ref.collection_store:
                    doc_ref.collection_store[doc_ref.id] = {}
                doc = doc_ref.collection_store[doc_ref.id]
                for key, value in data.items():
                    parts = key.split(".")
                    target = doc
                    for part in parts[:-1]:
                        if part not in target or not isinstance(target[part], dict):
                            target[part] = {}
                        target = target[part]
                    target[parts[-1]] = value
            elif op_type == "delete":
                if doc_ref.id in doc_ref.collection_store:
                    del doc_ref.collection_store[doc_ref.id]

class MockFirestoreClient:
    def __init__(self):
        self.db_store = {}

    def collection(self, name):
        return MockCollectionReference(name, self.db_store)

    def batch(self):
        return MockWriteBatch(self.db_store)

# ── Pytest Fixtures ──────────────────────────────────────────
@pytest.fixture
def mock_db():
    return MockFirestoreClient()

@pytest.fixture
def mock_user():
    return {
        "uid": "test-patient-123",
        "email": "patient@example.com",
        "name": "Test Patient",
        "role": "patient"
    }

@pytest.fixture
def client(mock_db, mock_user, monkeypatch):
    import common_code.firestore
    monkeypatch.setattr(common_code.firestore, "_db", mock_db)

    from patient_service.app import app
    from common_code.firebase_auth import get_current_user, require_role

    # Override dependencies
    app.dependency_overrides[get_current_user] = lambda: mock_user
    
    async def patient_gate():
        return mock_user
    app.dependency_overrides[require_role(["patient"])] = patient_gate

    yield TestClient(app)

    app.dependency_overrides.clear()

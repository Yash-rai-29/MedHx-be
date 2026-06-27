import firebase_admin
from firebase_admin import credentials, auth
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from common_code.config import settings
from common_code.firestore import get_db

# Initialize Firebase Admin SDK if not already done
try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app()

security = HTTPBearer()

async def get_current_user(cred: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Decodes the Firebase JWT from HTTP Authorization Bearer token header.
    Returns:
        dict: The decoded token payload containing user UID, custom claims, etc.
    """
    token = cred.credentials
    try:
        # Verify the Firebase ID token synchronously
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired Firebase Auth token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

def require_role(allowed_roles: list[str]):
    """
    FastAPI dependency factory enforcing user custom claim role match.
    Enforces a Firestore fallback lookup if the role is not present on the claims yet.
    """
    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        role = user.get("role")
        if not role:
            # Fallback check: look for role in Firestore if claim not updated yet
            try:
                db = get_db()
                uid = user.get("uid")
                user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                    role = user_data.get("role")
                    # Update local dict so routers have role
                    user["role"] = role
            except Exception:
                pass
                
        if not role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No role assigned to this credentials token."
            )
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' is not authorized. Allowed: {allowed_roles}"
            )
        return user
    return dependency

import os
import jwt
from datetime import datetime, timedelta

# Need to grab credentials
from dotenv import load_dotenv
load_dotenv('.env')

secret = os.getenv("SUPABASE_JWT_SECRET")

# Mock supabase JWT payload
payload = {
    "aud": "authenticated",
    "sub": "b2f6f555-5c14-432d-9b59-ea1605ac6e83",
    "email": "testagent@example.com",
    "phone": "",
    "app_metadata": {
        "provider": "email",
        "providers": ["email"]
    },
    "user_metadata": {
        "full_name": "Test Agent"
    },
    "role": "authenticated",
    "aal": "aal1",
    "amr": [{"method": "password", "timestamp": 1690000000}],
    "session_id": "8f8bba40-1a74-4b52-9721-cb9e44f800df",
    "is_anonymous": False
}

token = jwt.encode(payload, secret, algorithm="HS256")
print(token)

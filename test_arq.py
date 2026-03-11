import os
import json
import jwt
import asyncio
import urllib.request

env = {}
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                key, val = line.strip().split("=", 1)
                env[key] = val

SUPABASE_JWT_SECRET = env.get("SUPABASE_JWT_SECRET") or env.get("JWT_SECRET")

# Mock user id matching a typical UUID
USER_ID = "00000000-0000-0000-0000-000000000000"

payload = {
    "sub": USER_ID,
    "email": "test@example.com",
    "role": "authenticated",
    "aud": "authenticated"
}

token = jwt.encode(payload, SUPABASE_JWT_SECRET, algorithm="HS256")
print(f"Generated JWT Token")

req_body = {
    "keyword": "test keyword",
    "maxEmails": 10,
    "timeoutMinutes": 5,
    "sortBy": "relevance",
    "uploadDate": "any",
    "minViews": 0,
    "minSubscribers": 0,
    "maxSubscribers": 0,
    "minDuration": 0,
    "maxDuration": 0,
    "country": "US",
    "language": "en"
}

data = json.dumps(req_body).encode("utf-8")

req = urllib.request.Request("http://localhost:8000/api/start", data=data)
req.add_header("Authorization", f"Bearer {token}")
req.add_header("Content-Type", "application/json")

try:
    response = urllib.request.urlopen(req)
    result = json.loads(response.read().decode())
    print(f"API Response: {result}")
except Exception as e:
    print(f"API Error: {e}")
    if hasattr(e, 'read'):
        print(e.read().decode())

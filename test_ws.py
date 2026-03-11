import asyncio
import json
import redis.asyncio as aioredis
from shared_state import REDIS_URL

async def test_pubsub():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    job_id = "test-job-123"
    
    # simulate worker broadcast
    await client.publish(f"channel:{job_id}", json.dumps({
        "type": "email",
        "data": {"email": "test@gmail.com", "channelName": "Test Channel"},
        "total": 1
    }))
    print("Published mock email result to channel")

if __name__ == "__main__":
    asyncio.run(test_pubsub())

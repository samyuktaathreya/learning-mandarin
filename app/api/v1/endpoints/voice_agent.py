import os
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
import httpx
# from openai import OpenAI  <- You don't actually need this if you are using httpx to make the web request

router = APIRouter()

load_dotenv(os.path.join(os.path.dirname(__file__), '../../../.env'))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# FIX 1: I deleted the duplicate `/realtime/session` route that was using the fake command.
# We only need this one route to get the token for your frontend.

@router.post("/api/voice-session")
async def create_voice_session():
    """Generates an ephemeral token for the React frontend to establish a WebRTC session."""
    
    # FIX 2: I renamed this to `http_client` so it doesn't clash with any other `client` variables
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "session": {
                    "type": "realtime",
                    "model": "gpt-realtime-1.5",  # Explicit model name required
                }
            }
        )
        
        if response.status_code != 200:
            # print(f"📡 OpenAI Response Status Code: {response.status_code}")
            print(f"❌ OpenAI Error Body: {response.text}\n")
            raise HTTPException(status_code=500, detail="Failed to create session secret")
        
        data = response.json()
        print(f"DEBUG DATA FROM OPENAI: {data}")
        
        # FIX 3: We now open the "client_secret" dictionary first, then grab the "value" inside it
        token = data.get("value")

        if not token:
            raise HTTPException(
                status_code=500, 
                detail="Could not find token 'value' in OpenAI response"
            )
        
        return {"client_secret": token}
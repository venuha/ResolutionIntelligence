"""One Render worker; hosted state lives in Supabase, not the container filesystem."""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("resolution.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8103")),
                workers=1, proxy_headers=True, forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"))

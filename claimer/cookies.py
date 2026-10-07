import os
import json
import base64
import httpx
from nacl import encoding, public

def load_cookies() -> list[dict]:
    cookies_json = os.getenv("EPIC_COOKIES", "[]")
    try:
        cookies = json.loads(cookies_json)
        if isinstance(cookies, list):
            return cookies
    except json.JSONDecodeError:
        pass
    return []

def validate_cookies(cookies: list[dict]) -> bool:
    if not cookies:
        return False
        
    url = "https://egs-platform-service.store.epicgames.com/api/v1/private/egs/account/subscriptions"
    
    # Extract cookies for httpx
    # Need to match the domain, but for simplicity we can just pass them if they are epicgames.com cookies
    cookie_jar = httpx.Cookies()
    for cookie in cookies:
        if "epicgames.com" in cookie.get("domain", ""):
            cookie_jar.set(cookie["name"], cookie["value"], domain=cookie["domain"])
            
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    with httpx.Client(cookies=cookie_jar, timeout=10.0, headers=headers) as client:
        try:
            response = client.get(url, params={"count": 1, "start": 0})
            if response.status_code == 200:
                return True
            # Cloudflare serves a JS challenge to datacenter IPs (e.g. GitHub Actions
            # runners) on this endpoint, so a non-200 here does NOT prove the cookies
            # are bad. Defer the real check to the Chromium browser in claim_game(),
            # which can pass the challenge and reports login state accurately.
            if response.headers.get("cf-mitigated") == "challenge":
                print("Cloudflare challenge on validation endpoint; deferring login check to browser.")
                return True
        except Exception as e:
            print(f"Cookie validation error: {e}")
    return False

def save_cookies(cookies: list[dict]):
    cookies_str = json.dumps(cookies)
    print("::EPIC_COOKIES_START::")
    print(cookies_str)
    print("::EPIC_COOKIES_END::")
    
def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    public_key_bytes = base64.b64decode(public_key_b64)
    public_key = public.PublicKey(public_key_bytes)
    sealed_box = public.SealedBox(public_key)
    encrypted_bytes = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted_bytes).decode("utf-8")

def sync_github_secret(cookies: list[dict]) -> bool:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    
    if not token or not repo:
        print("GITHUB_TOKEN or GITHUB_REPO not set. Skipping secret sync.")
        return False
        
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    # 1. Get public key
    pk_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
    try:
        with httpx.Client(headers=headers, timeout=10.0) as client:
            r = client.get(pk_url)
            r.raise_for_status()
            pk_data = r.json()
            key_id = pk_data["key_id"]
            public_key = pk_data["key"]
            
            # 2. Encrypt
            secret_value = json.dumps(cookies)
            encrypted_value = _encrypt_secret(public_key, secret_value)
            
            # 3. Update secret
            secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/EPIC_COOKIES"
            payload = {
                "encrypted_value": encrypted_value,
                "key_id": key_id
            }
            r = client.put(secret_url, json=payload)
            r.raise_for_status()
            print("Successfully updated EPIC_COOKIES in GitHub Secrets.")
            return True
            
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403, 404):
            print(f"Warning: GitHub API permission error ({e.response.status_code}). Ensure GITHUB_TOKEN has write access to repository secrets.")
        else:
            print(f"Failed to sync GitHub secret: {e}")
    except Exception as e:
        print(f"Error syncing GitHub secret: {e}")
        
    return False

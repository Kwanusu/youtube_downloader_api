import os
import requests
from dotenv import load_dotenv

load_dotenv()

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")

DOMAINS = ["zoho.com", "zoho.in", "zoho.eu", "zoho.com.au"]
REQUEST_TIMEOUT = (10, 30)


def find_correct_zoho_config():
    if not all([ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN]):
        print("ERROR: Missing one or more environment variables in .env file!")
        return

    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }

    for domain in DOMAINS:
        print(f"Testing domain: {domain}...")
        url = f"https://accounts.{domain}/oauth/v2/token"
        
        try:
            # 1. Get Access Token
            res = requests.post(url, data=params, timeout=REQUEST_TIMEOUT)
            
            try:
                token_res = res.json()
            except ValueError:
                print(f"  Non-JSON response from {domain} (HTTP {res.status_code})")
                continue

            access_token = token_res.get("access_token")
            
            if not access_token:
                print(f"  Response error for {domain}: {token_res}")
                continue

            print(f"  Successfully retrieved access token on {domain}!")
            
            # 2. Fetch User Profile for ZSOID
            headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
            user_url = f"https://meeting.{domain}/api/v2/user.json"
            
            user_res = requests.get(
                user_url, headers=headers, timeout=REQUEST_TIMEOUT
            )
            
            zsoid = None
            if user_res.status_code == 200:
                try:
                    user_data = user_res.json()
                    zsoid = user_data.get("userDetails", {}).get("zsoid") or user_data.get("zsoid")
                except ValueError:
                    pass

            print("\n" + "=" * 40)
            print(" SUCCESS! CONFIRMED YOUR ZOHO CONFIGURATION:")
            print(f" ZOHO_DOMAIN = \"{domain}\"")
            if zsoid:
                print(f" ZOHO_ZSOID  = \"{zsoid}\"")
            print("=" * 40 + "\n")
            return domain, zsoid

        except Exception as e:
            print(f" Failed on {domain}: {e}")


if __name__ == "__main__":
    find_correct_zoho_config()
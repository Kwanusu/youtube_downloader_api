"""One-time YouTube login. Run on a computer with a browser, signed in as the
channel owner, then copy the generated token.json to the server."""
from google_auth_oauthlib.flow import InstalledAppFlow

import config
from youtube_client import SCOPES

if __name__ == "__main__":
    flow = InstalledAppFlow.from_client_config(config.GOOGLE_CLIENT_CONFIG, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    config.YOUTUBE_TOKEN_FILE.write_text(creds.to_json())
    print(f"Saved {config.YOUTUBE_TOKEN_FILE}. Copy it to the server's YOUTUBE_TOKEN_FILE path.")

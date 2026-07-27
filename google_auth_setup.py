"""
One-time Google OAuth2 setup script.
Run this ONCE to authenticate with your Google account and save the token.

Usage:
    python google_auth_setup.py

It will open your browser → log in with your Google account → close the tab.
A file called google_token.json will be saved — the pipeline uses this forever.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
CLIENT_SECRET_FILE = 'google_client_secret.json'
TOKEN_FILE = 'google_token.json'

def main():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            print('Token refreshed automatically.')
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            print('Authentication successful!')

        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
        print(f'Token saved to {TOKEN_FILE}')
    else:
        print('Already authenticated — token is valid.')

if __name__ == '__main__':
    main()

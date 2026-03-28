"""
Google Drive OAuth Token Generator

Description:
This script generates a token.json file using Google OAuth 2.0.

Requirements:
- Google Drive API enabled
- OAuth client_secret file (client_secret_web_local.json)
- Library: google-auth-oauthlib

Usage:
Run this script and complete authentication in browser.
"""

from google_auth_oauthlib.flow import InstalledAppFlow
import os

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/drive.readonly'
]

creds = None

if os.path.exists('token.json'):
    print("Token already exists")
else:
    flow = InstalledAppFlow.from_client_secrets_file(
        'client_secret_web_local.json', SCOPES)

    # Using PORT=8080
    creds = flow.run_local_server(port=8080)

    with open('token.json', 'w') as token:
        token.write(creds.to_json())

    print("token.json generated successfully")
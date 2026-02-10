import firebase_admin
from firebase_admin import credentials, auth
import requests
import streamlit as st
import json
import os

# Initialize Firebase App
def initialize_firebase():
    try:
        if not firebase_admin._apps:
            # Check if secrets are available
            cred_path = st.secrets.get("firebase", {}).get("service_account_path", "firebase_credentials.json")
            
            if os.path.exists(cred_path):
                 # Verify it's not the dummy placeholder before trying to load
                 try:
                     with open(cred_path, 'r') as f:
                         content = f.read()
                         if "YOUR_PRIVATE_KEY" not in content and content.strip() != "":
                             cred = credentials.Certificate(cred_path)
                             firebase_admin.initialize_app(cred)
                         else:
                             print("Service account file contains placeholder data. Skipping Admin SDK init.")
                 except Exception:
                     pass
            else:
                # No service account, which is fine if we just want client-side auth via REST
                pass
    except Exception as e:
        # Just log via console, don't crash the UI for this if possible
        print(f"Firebase Admin SDK initialization skipped or failed: {e}")

def sign_in_with_email_and_password(email, password):
    # Retrieve API Key from secrets
    api_key = st.secrets.get("firebase", {}).get("web_api_key")
    
    # If key is missing or is the default placeholder, fallback to mock
    if not api_key or api_key == "YOUR_FIREBASE_WEB_API_KEY":
        st.warning("Using Mock Auth because valid API Key is missing.")
        if email == "test@example.com" and password == "password":
            return {"localId": "test_user_id", "email": email, "idToken": "mock_token"}
        else:
            return None

    request_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True
    }
    
    try:
        response = requests.post(request_url, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        error_msg = e.response.json().get('error', {}).get('message', 'Unknown error')
        st.error(f"Authentication failed: {error_msg}")
        return None
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")
        return None

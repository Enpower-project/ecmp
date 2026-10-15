import os
from dotenv import load_dotenv
from app.keycloak_client import KeycloakClient

load_dotenv()

RESOURCE_SERVER_CLIENT_ID = os.getenv("RESOURCE_SERVER_CLIENT_ID")
RESOURCE_SERVER_CLIENT_SECRET = os.getenv("RESOURCE_SERVER_CLIENT_SECRET")
REALM = os.getenv("KC_REALM")
KC_BASE_URL = os.getenv("KC_BASE_URL")

kc = KeycloakClient(
    client_id=RESOURCE_SERVER_CLIENT_ID, 
    client_secret=RESOURCE_SERVER_CLIENT_SECRET, 
    base_keycloak_url=KC_BASE_URL,
    realm_name=REALM
)
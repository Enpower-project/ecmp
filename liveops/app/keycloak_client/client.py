import asyncio
import logging

import aiohttp
from fastapi import HTTPException
from jose import JWTError, jwt


logger = logging.getLogger(__name__)


class KeycloakClient:
    def __init__(self, client_id, client_secret, base_keycloak_url, realm_name):
        """
        Initialize a lightweight Keycloak client wrapper for token validation.

        Args:
            client_id: Resource server client identifier.
            client_secret: Client secret used for Keycloak access.
            base_keycloak_url: Base URL of the Keycloak server.
            realm_name: Target Keycloak realm name.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_kc_url = base_keycloak_url
        self.realm = realm_name

        self.realm_url = f"{self.base_kc_url}/realms/{self.realm}"
        self.certs_url = f"{self.realm_url}/protocol/openid-connect/certs"
        self.jwks = {}
        self.jwks_lock = asyncio.Lock()

    async def init(self):
        """
        Load the initial JWKS set used for JWT verification.
        """
        await self.fetch_keycloak_public_keys()

    async def fetch_keycloak_public_keys(self, retries=None, delay=5):
        """
        Fetch public signing keys from Keycloak and cache them locally.

        Args:
            retries: Optional maximum number of retry attempts.
            delay: Delay in seconds between retries.

        Returns:
            dict | None: Cached JWKS payload when available, otherwise `None`.
        """
        attempt = 0
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.certs_url) as response:
                        if response.status == 200:
                            self.jwks = await response.json()
                            logger.debug("JWKS fetched successfully.")
                            return self.jwks
                        logger.error("Failed to fetch JWKS: %s", response.status)
            except aiohttp.ClientConnectorError as exc:
                logger.error("Connection error while fetching JWKS: %s", exc)
            except Exception as exc:
                logger.error("Unexpected JWKS fetch error: %s", exc)

            attempt += 1
            if retries is not None and attempt >= retries:
                logger.info("Max retries reached while fetching JWKS.")
                return None

            logger.debug("Retrying JWKS fetch in %ss (attempt %s)", delay, attempt)
            await asyncio.sleep(delay)


    async def verify_token(self, token: str):
        """
        Verify a bearer token against the cached Keycloak JWKS.

        Args:
            token: Bearer token from the incoming request.

        Returns:
            dict: Decoded JWT payload.

        Raises:
            HTTPException: If the token key is invalid or JWT validation fails.
        """
        if not self.jwks.get("keys"):
            async with self.jwks_lock:
                if not self.jwks.get("keys"):
                    await self.fetch_keycloak_public_keys(retries=1)

        try:
            headers = jwt.get_unverified_header(token)
            kid = headers.get("kid")
            key = next((key for key in self.jwks["keys"] if key["kid"] == kid), None)

            if not key:
                raise HTTPException(status_code=401, detail="Invalid token key")

            return jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
            )
        except JWTError as exc:
            logger.error("JWT validation failed: %s", exc)
            raise HTTPException(status_code=401, detail=f"JWT validation failed: {exc}")

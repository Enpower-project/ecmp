import aiohttp
import asyncio
import time
import logging
from fastapi import Request, HTTPException
from jose import jwt, JWTError

logger = logging.getLogger(__name__)


class KeycloakClient:
    def __init__(self, client_id, client_secret, base_keycloak_url, realm_name):
        """
        Initialize a Keycloak client wrapper for token validation and admin APIs.

        Args:
            client_id: Resource server or admin client identifier.
            client_secret: Client secret used for client-credentials flows.
            base_keycloak_url: Base URL of the Keycloak server.
            realm_name: Target Keycloak realm name.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_kc_url = base_keycloak_url
        self.realm = realm_name

        # endpoints
        self.realm_url = f"{self.base_kc_url}/realms/{self.realm}"
        self.admin_url = f"{self.base_kc_url}/admin/realms/{self.realm}"
        self.certs_url = f"{self.realm_url}/protocol/openid-connect/certs"
        self.token_url = f"{self.realm_url}/protocol/openid-connect/token"
        self.introspect_url = f"{self.realm_url}/protocol/openid-connect/token/introspect"
        self.permission_url = f"{self.realm_url}/authz/protection/permission"
        self.resources_url = f"{self.realm_url}/authz/protection/resource_set"
        self.clients_url = f"{self.admin_url}/clients"
        self.users_url = f"{self.admin_url}/users"

        self.jwks = {}

        self.pat = None
        self.pat_expire = 0
        self.pat_lock = asyncio.Lock()

    
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
                            break
                        else:
                            logger.error(f"Failed to fetch JWKS: {response.status}")
            except aiohttp.ClientConnectorError as e:
                logger.error(f"Connection error: {e}")
            except Exception as e:
                logger.error(f"An error occurred: {e}")

            attempt += 1
            if retries is not None and attempt >= retries:
                logger.info("Max retries reached.")
                return None

            logger.debug(f"Retrying in {delay}s... (Attempt {attempt})")
            await asyncio.sleep(delay)
    

    async def _get_protection_api_token(self):
        """
        Retrieve a protection API token using the client credentials grant.
        """
        # client credentials grant in more general context
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        data = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.token_url, headers=headers, data=data) as response:
                if response.status == 200:
                    token_response = await response.json()
                    self.pat = token_response.get('access_token')
                    expires_in = token_response.get('expires_in', 300)
                    self.pat_expire = time.time() + expires_in - 60
                else:
                    logger.error(f"Failed to get PAT: {response.status}")
                    return None
    

    async def ensure_pat(self):
        """
        Ensure a valid protection API token is available before admin calls.
        """
        async with self.pat_lock:
            if self.pat is None or time.time() >= self.pat_expire:
                await self._get_protection_api_token()


    # async def verify_token(self, request: Request, token: str):
    async def verify_token(self, token: str):
        """
        Verify token from FastAPI request
        """
        logger.debug("Starting token verification")
        logger.debug(f"token: {token}")
        try:
            headers = jwt.get_unverified_header(token)
            logger.debug(f"Unverified headers: {headers}")

            kid = headers.get("kid")
            logger.debug(f"Extracted kid: {kid}")

            # logger.debug(f"jwks: {self.jwks}")

            key = next((k for k in self.jwks["keys"] if k["kid"] == kid), None)
            # logger.debug(f"key: {key}")
            if not key:
                logger.warning("No matching key found for kid")
                raise HTTPException(status_code=401, detail="Invalid token key")

            payload = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                # NOTE: enable this for production if needed
                # issuer=self.issuer  # Enable this in production
            )

            logger.debug(f"Token successfully decoded: {payload}")
            return payload
        
        except JWTError as e:
            logger.error(f"JWT validation failed: {e}")
            raise HTTPException(status_code=401, detail=f"JWT validation failed: {e}")
        
    
    async def validate_rpt(self, rpt):
        """
        Introspect a requesting party token and report whether it is active.

        Args:
            rpt: Requesting party token to validate.

        Returns:
            bool: `True` when the token is active, otherwise `False`.
        """
        data = {
            'token': rpt,
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.introspect_url, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('active', False)
                else:
                    logger.error(f"Failed to introspect RPT: {response.status}")
                    return False


    async def get_permission_ticket(self, resource_id, resource_scopes):
        """
        Request a permission ticket for the given resource and scopes.

        Args:
            resource_id: Keycloak resource identifier.
            resource_scopes: Scopes requested for the resource.

        Returns:
            str | None: Permission ticket on success, otherwise `None`.
        """
        await self.ensure_pat()
        if not self.pat:
            logger.error("PAT is missing.")
            return None

        headers = {
            'Authorization': f'Bearer {self.pat}',
            'Content-Type': 'application/json'
        }

        data = [{
            'resource_id': resource_id,
            'resource_scopes': resource_scopes
        }]

        async with aiohttp.ClientSession() as session:
            async with session.post(self.permission_url, headers=headers, json=data) as response:
                if response.status == 201:
                    ticket_response = await response.json()
                    return ticket_response.get('ticket')
                else:
                    logger.error(f"Failed to get permission ticket: {response.status}")
                    return None


    async def create_client_scope(self, client_id, scope_name):
        """
        Create a new authorization scope for a specific Keycloak client.

        Args:
            client_id: Internal Keycloak client UUID.
            scope_name: Scope name to create.

        Returns:
            dict: Success flag and optional error details from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "name": scope_name
        }

        logger.debug(f"data: {data}")

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.clients_url}/{client_id}/authz/resource-server/scope", headers=headers, json=data) as resp:
                try:
                    result= await resp.text()
                except:
                    result = {"error": await resp.text()}
                
                logger.debug(f"result: {result}")
                logger.debug(f"status: {resp.status}")

                if resp.status == 200:
                    logger.debug(f"Scope created")
                    return {"success": True}
                else:
                    logger.error(f"Failed to create scope: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }
    

    async def get_client_scopes(self, client_id):
        """
        Retrieve authorization scopes configured for a Keycloak client.

        Args:
            client_id: Internal Keycloak client UUID.

        Returns:
            dict: Success flag and optional error details from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.clients_url}/{client_id}/authz/resource-server/scope", headers=headers) as resp:
                try:
                    result= await resp.text()
                except:
                    result = {"error": await resp.text()}
                
                logger.debug(f"result: {result}")
                logger.debug(f"status: {resp.status}")

                if resp.status == 200:
                    logger.debug(f"Client scopes")
                    return {"success": True}
                else:
                    logger.error(f"Failed to get client scopes: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }


    async def get_realm_clients(self):
        """
        List all clients available in the configured realm.

        Returns:
            dict: Success payload containing simplified client metadata, or an
            error payload when the request fails.
        """
        await self.ensure_pat()

        headers = {'Authorization': f'Bearer {self.pat}'}

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.clients_url}", headers=headers) as response:
                try:
                    result= await response.json()
                except:
                    result = {"error": await response.text()}
                    
                logger.debug(f"{result}")
                logger.debug(f"status: {response.status}")

                if response.status == 200:
                    resource = await response.json()
                    # reshape the response
                    output = [
                        {
                            "id": item.get("id"),
                            "client_id": item.get("clientId"),
                            "client_name": item.get("name"),
                            "root_url": item.get("rootIrl"),
                            "base_url": item.get("baseUrl"),
                            "admin_url": item.get("adminUrl"),
                            "enabled": item.get("enabled"),
                            "always_display_in_console": item.get("alwaysDisplayInConsole"),
                            "client_authentication_type": item.get("clientAuthenticationType"),
                            "redirect_uris": item.get("redirectUris"),
                            "webOrigins": item.get("webOrigins"),
                            "standard_flow_enabled": item.get("standardFlowEnabled"),
                            "direct_access_grants_enabled": item.get("directAccessGrantsEnabled"),
                            "service_accounts_enabled": item.get("serviceAccountsEnabled"),
                            "authorization_services_enabled": item.get("authorizationServicesEnabled"),
                            "public_client": item.get("publicClient"),
                            "protocol": item.get("protocol")
                        } for item in result
                    ]

                    logger.debug(f"Users retrieved: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to retrieve the users: {response.status}, {result}")
                    return {
                        "success": False,
                        "status": response.status,
                        "error": result
                    }
                

    async def create_client(self, client_id, client_name, protocol, public_client, redirect_uris, base_url, admin_url, standard_flow_enabled, secret, service_accounts_enabled, direct_access_grants_enabled, authorization_services_enabled, always_display_in_console, enabled):
        """
        Create a new client in the configured Keycloak realm.

        Args:
            client_id: External client identifier.
            client_name: Human-readable client name.
            protocol: Client protocol, typically `openid-connect`.
            public_client: Whether the client is public.
            redirect_uris: Allowed redirect URIs.
            base_url: Base URL exposed by the client.
            admin_url: Admin callback URL.
            standard_flow_enabled: Whether standard auth code flow is enabled.
            secret: Client secret for confidential clients.
            service_accounts_enabled: Whether service accounts are enabled.
            direct_access_grants_enabled: Whether password grants are enabled.
            authorization_services_enabled: Whether authz services are enabled.
            always_display_in_console: Whether to show the client in the console.
            enabled: Whether the client is active.

        Returns:
            dict: Success flag and optional error details from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "clientId": client_id,
            "name": client_name, 
            "enabled": enabled,
            "protocol": protocol,
            "publicClient": public_client,
            "secret": secret,
            "redirectUris": redirect_uris,
            "baseUrl": base_url,
            "adminUrl": admin_url,
            "standardFlowEnabled": standard_flow_enabled,
            "serviceAccountsEnabled": service_accounts_enabled,
            "directAccessGrantsEnabled": direct_access_grants_enabled,
            "authorizationServicesEnabled": authorization_services_enabled,
            "alwaysDisplayInConsole": always_display_in_console
        }

        logger.info(f"data: {data}")

        async with aiohttp.ClientSession() as session:
            async with session.post(self.clients_url, headers=headers, json=data) as resp:
                try:
                    result= await resp.text()
                except:
                    result = {"error": await resp.text()}
                
                logger.debug(f"result: {result}")
                logger.debug(f"status: {resp.status}")

                if resp.status == 201:
                    logger.debug(f"Client created")
                    return {"success": True}
                else:
                    logger.error(f"Failed to create client: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }


    async def get_client_by_id(self, client_id):
        """
        Retrieve a single realm client by its internal Keycloak UUID.

        Args:
            client_id: Internal Keycloak client UUID.

        Returns:
            dict: Success payload containing simplified client metadata, or an
            error payload when the request fails.
        """
        await self.ensure_pat()

        headers = {'Authorization': f'Bearer {self.pat}'}

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.clients_url}/{client_id}", headers=headers) as response:
                try:
                    result= await response.json()
                except:
                    result = {"error": await response.text()}
                    
                logger.info(f"{result}")
                logger.info(f"status: {response.status}")

                if response.status == 200:
                    user = await response.json()
                    # reshape the response
                    
                    output = {
                        "id": result.get("id"),
                        "client_id": result.get("clientId"),
                        "client_name": result.get("name"),
                        "root_url": result.get("rootIrl"),
                        "base_url": result.get("baseUrl"),
                        "admin_url": result.get("adminUrl"),
                        "enabled": result.get("enabled"),
                        "always_display_in_console": result.get("alwaysDisplayInConsole"),
                        "client_authentication_type": result.get("clientAuthenticationType"),
                        "redirect_uris": result.get("redirectUris"),
                        "webOrigins": result.get("webOrigins"),
                        "standard_flow_enabled": result.get("standardFlowEnabled"),
                        "direct_access_grants_enabled": result.get("directAccessGrantsEnabled"),
                        "service_accounts_enabled": result.get("serviceAccountsEnabled"),
                        "authorization_services_enabled": result.get("authorizationServicesEnabled"),
                        "public_client": result.get("publicClient"),
                        "protocol": result.get("protocol")   
                    }

                    logger.debug(f"CLient with ID {client_id} retrieved: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to retrieve the client {client_id}: {response.status}, {result}")
                    return {
                        "success": False,
                        "status": response.status,
                        "error": result
                    }
                

    async def update_client(self, client_uuid, client_name, protocol, public_client, redirect_uris, base_url, admin_url, standard_flow_enabled, secret, service_accounts_enabled, direct_access_grants_enabled, authorization_services_enabled, always_display_in_console, enabled):
        """
        Update an existing Keycloak client.

        Args:
            client_uuid: Internal Keycloak client UUID.
            client_name: Human-readable client name.
            protocol: Client protocol.
            public_client: Whether the client is public.
            redirect_uris: Allowed redirect URIs.
            base_url: Base URL exposed by the client.
            admin_url: Admin callback URL.
            standard_flow_enabled: Whether standard auth code flow is enabled.
            secret: Client secret for confidential clients.
            service_accounts_enabled: Whether service accounts are enabled.
            direct_access_grants_enabled: Whether password grants are enabled.
            authorization_services_enabled: Whether authz services are enabled.
            always_display_in_console: Whether to show the client in the console.
            enabled: Whether the client is active.

        Returns:
            dict: Success flag and HTTP status from Keycloak.
        """
        await self.ensure_pat()

        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "name": client_name, 
            "enabled": enabled,
            "protocol": protocol,
            "publicClient": public_client,
            "secret": secret,
            "redirectUris": redirect_uris,
            "baseUrl": base_url,
            "adminUrl": admin_url,
            "standardFlowEnabled": standard_flow_enabled,
            "serviceAccountsEnabled": service_accounts_enabled,
            "directAccessGrantsEnabled": direct_access_grants_enabled,
            "authorizationServicesEnabled": authorization_services_enabled,
            "alwaysDisplayInConsole": always_display_in_console
        }

        async with aiohttp.ClientSession() as session:
            async with session.put(f"{self.clients_url}/{client_uuid}", headers=headers, json=data) as response:
                if response.status == 204:
                    logger.debug(f"Client updated")
                    return {
                        "success": True, 
                        "status": response.status
                    }
                else:
                    logger.error(f"Failed to update client: {response.status}")
                    return {
                        "success": False,
                        "status": response.status,
                    }
                

    async def delete_client(self, client_id):
        """
        Delete a Keycloak client by its internal UUID.

        Args:
            client_id: Internal Keycloak client UUID.

        Returns:
            dict: Success flag and HTTP status from Keycloak.
        """
        await self.ensure_pat()

        headers = {
            "Authorization": f"Bearer {self.pat}",
        }

        async with aiohttp.ClientSession() as session:
            async with session.delete(f"{self.clients_url}/{client_id}", headers=headers) as response:
                if response.status == 204:
                    logger.debug(f"Client deleted")
                    return {
                        "success": True, 
                        "status": response.status
                    }
                else:
                    logger.error(f"Failed to delete client: {response.status}")
                    return {
                        "success": False,
                        "status": response.status,
                    }
                

    async def get_all_users(self):
        """
        List all users in the configured realm.

        Returns:
            dict: Success payload containing simplified user metadata, or an
            error payload when the request fails.
        """
        await self.ensure_pat()

        headers = {'Authorization': f'Bearer {self.pat}'}

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.users_url}", headers=headers) as response:
                try:
                    result= await response.json()
                except:
                    result = {"error": await response.text()}
                    
                logger.debug(f"{result}")
                logger.debug(f"status: {response.status}")

                if response.status == 200:
                    resource = await response.json()
                    # reshape the response
                    output = [
                        {
                            "id": item.get("id"),
                            "username": item.get("username"),
                            "first_name": item.get("firstName"),
                            "last_name": item.get("lastName"),
                            "email": item.get("email")
                        } for item in result
                    ]

                    logger.debug(f"Users retrieved: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to retrieve the users: {response.status}, {result}")
                    return {
                        "success": False,
                        "status": response.status,
                        "error": result
                    }


    async def create_user(self, username, first_name, last_name, email, email_verified, credentials, enabled):
        """
        Create a new Keycloak user.

        Args:
            username: Login name of the new user.
            first_name: User first name.
            last_name: User last name.
            email: User email address.
            email_verified: Whether the email is already verified.
            credentials: Credential descriptor passed to Keycloak.
            enabled: Whether the user should be active.

        Returns:
            dict: Success flag and optional error details from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "username": username,
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "emailVerified": email_verified,
            "credentials": [credentials],
            "enabled": enabled
        }

        logger.debug(f"data: {data}")

        async with aiohttp.ClientSession() as session:
            async with session.post(self.users_url, headers=headers, json=data) as resp:
                try:
                    result= await resp.text()
                except:
                    result = {"error": await resp.text()}
                
                logger.debug(f"result: {result}")
                logger.debug(f"status: {resp.status}")

                if resp.status == 201:
                    logger.debug(f"User created")
                    return {"success": True}
                else:
                    logger.error(f"Failed to create user: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }
    

    async def update_user(self, user_id, username, first_name, last_name, email, email_verified, credentials, enabled):
        """
        Update an existing Keycloak user.

        Args:
            user_id: Internal Keycloak user UUID.
            username: Login name of the user.
            first_name: User first name.
            last_name: User last name.
            email: User email address.
            email_verified: Whether the email is verified.
            credentials: Credential descriptor passed to Keycloak.
            enabled: Whether the user should be active.

        Returns:
            dict: Success flag and HTTP status from Keycloak.
        """
        await self.ensure_pat()

        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "username": username,
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "emailVerified": email_verified,
            "credentials": [credentials],
            "enabled": enabled
        }

        async with aiohttp.ClientSession() as session:
            async with session.put(f"{self.users_url}/{user_id}", headers=headers, json=data) as response:
                if response.status == 204:
                    logger.debug(f"User updated")
                    return {
                        "success": True, 
                        "status": response.status
                    }
                else:
                    logger.error(f"Failed to update user: {response.status}")
                    return {
                        "success": False,
                        "status": response.status,
                    }
                         

    async def get_user_by_id(self, user_id):
        """
        Retrieve a single user by internal Keycloak UUID.

        Args:
            user_id: Internal Keycloak user UUID.

        Returns:
            dict: Success payload containing simplified user metadata, or an
            error payload when the request fails.
        """
        await self.ensure_pat()

        headers = {'Authorization': f'Bearer {self.pat}'}

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.users_url}/{user_id}", headers=headers) as response:
                try:
                    result= await response.json()
                except:
                    result = {"error": await response.text()}
                    
                logger.debug(f"{result}")
                logger.debug(f"status: {response.status}")

                if response.status == 200:
                    user = await response.json()
                    # reshape the response
                    
                    output = {
                        'id': user.get('id'),
                        'username': user.get('username'),
                        'first_name': user.get('firstName'),
                        'last_name': user.get('lastName'),
                        'email': user.get('email'),
                        'email_verified': user.get('emailVerified'),
                        'created_timestamp': user.get('createdTimestamp'),
                        'enabled': user.get('enabled'),
                        'required_actions': user.get('requiredActions')   
                    }

                    logger.debug(f"User with ID {user_id} retrieved: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to retrieve the user {user_id}: {response.status}, {result}")
                    return {
                        "success": False,
                        "status": response.status,
                        "error": result
                    }


    async def delete_user(self, user_id):
        """
        Delete a Keycloak user by internal UUID.

        Args:
            user_id: Internal Keycloak user UUID.

        Returns:
            dict: Success flag and HTTP status from Keycloak.
        """
        await self.ensure_pat()

        headers = {
            "Authorization": f"Bearer {self.pat}",
        }

        async with aiohttp.ClientSession() as session:
            async with session.delete(f"{self.users_url}/{user_id}", headers=headers) as response:
                if response.status == 204:
                    logger.debug(f"User deleted")
                    return {
                        "success": True, 
                        "status": response.status
                    }
                else:
                    logger.error(f"Failed to delete user: {response.status}")
                    return {
                        "success": False,
                        "status": response.status,
                    }
                


    async def get_resources_list(self):
        """
        Retrieve the raw list of authorization resource identifiers.

        Returns:
            list | None: Resource identifiers on success, otherwise `None`.
        """
        await self.ensure_pat()
        headers = {'Authorization': f'Bearer {self.pat}'}

        async with aiohttp.ClientSession() as session:
            async with session.get(self.resources_url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Failed to get resources: {response.status}")
                    return None
                

    async def get_all_resources(self):
        """
        Retrieve and reshape all authorization resources.

        Returns:
            dict: Resource metadata keyed by resource identifier.
        """
        await self.ensure_pat()
        res_ids = await self.get_resources_list()
        if not res_ids:
            return {}

        headers = {'Authorization': f'Bearer {self.pat}'}

        resources = {}

        for resid in res_ids:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.resources_url}/{resid}", headers=headers) as response:
                    if response.status == 200:
                        resources[resid] = await response.json()
                    else:
                        logger.error(f"Failed to get resource {resid}: {response.status}")

        # reshape the resources output
        output = {
            res_id: {
                "resource_name": body.get("name"),
                "resource_id": body.get("_id"),
                "owner_id": body.get("owner", {}).get("id")
            }
            for res_id, body in resources.items()
        }

        return output
    

    async def get_resource(self, resource_id):
        """
        Retrieve a single authorization resource and reshape its metadata.

        Args:
            resource_id: Keycloak resource identifier.

        Returns:
            dict: Success payload containing resource metadata, or an error
            payload when the request fails.
        """
        await self.ensure_pat()

        headers = {'Authorization': f'Bearer {self.pat}'}

        resource = None

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.resources_url}/{resource_id}", headers=headers) as response:
                try:
                    result= await response.json()
                except:
                    result = {"error": await response.text()}

                if response.status == 200:
                    resource = await response.json()
                    # reshape the resource output
                    output = {
                        "resource_name": resource.get("name"),
                        "resource_id": resource.get("_id"),
                        "resource_type": resource.get("type"),
                        "owner_id": resource.get("owner", {}).get("id"),
                        "resource_uris": resource.get("uris"),
                        "resource_scopes": [item.get("name") for item in resource.get("scopes")],
                        "resource_attributes": resource.get('attributes', {}),
                        "owner_managed_access": resource.get("ownerManagedAccess")
                    }
                    logger.debug(f"Resource retrieved: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to retrieve resource: {response.status}, {result}")
                    return {
                        "success": False,
                        "status": response.status,
                        "error": result
                    }
    

    async def update_resource(self, resource_id, resource_name, resource_type, resource_uris, resource_scopes, resource_attributes, owner_managed_access):
        """
        Update an existing authorization resource.

        Args:
            resource_id: Keycloak resource identifier.
            resource_name: Display name of the resource.
            resource_type: Resource type string.
            resource_uris: URIs associated with the resource.
            resource_scopes: Scopes attached to the resource.
            resource_attributes: Custom resource attributes.
            owner_managed_access: Whether owner-managed access is enabled.

        Returns:
            dict: Success flag and HTTP status from Keycloak.
        """
        await self.ensure_pat()

        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "name": resource_name,
            "type": resource_type,
            "uris": resource_uris,
            "scopes": resource_scopes,
            "attributes": resource_attributes,
            "ownerManagedAccess": owner_managed_access
        }

        async with aiohttp.ClientSession() as session:
            async with session.put(f"{self.resources_url}/{resource_id}", headers=headers, json=data) as response:
                if response.status == 204:
                    logger.debug(f"Resource updated")
                    return {
                        "success": True, 
                        "status": response.status
                    }
                else:
                    logger.error(f"Failed to update resource: {response.status}")
                    return {
                        "success": False,
                        "status": response.status,
                    }


    async def delete_resource(self, resource_id):
        """
        Delete an authorization resource.

        Args:
            resource_id: Keycloak resource identifier.

        Returns:
            dict: Success flag and HTTP status from Keycloak.
        """
        await self.ensure_pat()

        headers = {
            "Authorization": f"Bearer {self.pat}",
        }

        async with aiohttp.ClientSession() as session:
            async with session.delete(f"{self.resources_url}/{resource_id}", headers=headers) as response:
                if response.status == 204:
                    logger.debug(f"Resource deleted")
                    return {
                        "success": True, 
                        "status": response.status
                    }
                else:
                    logger.error(f"Failed to delete resource: {response.status}")
                    return {
                        "success": False,
                        "status": response.status,
                    }
    

    async def create_resource_for_user(self, resource_name, resource_type, owner_id, resource_uris, resource_scopes, owner_managed_access):
        """
        Create an authorization resource owned by a specific user.

        Args:
            resource_name: Display name of the resource.
            resource_type: Resource type string.
            owner_id: Keycloak user identifier that owns the resource.
            resource_uris: URIs associated with the resource.
            resource_scopes: Scopes attached to the resource.
            owner_managed_access: Whether owner-managed access is enabled.

        Returns:
            dict: Success payload containing the created resource, or an error
            payload when creation fails.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "name": resource_name,
            "type": resource_type,
            "owner": owner_id,
            "uris": resource_uris,
            "scopes": resource_scopes,
            "ownerManagedAccess": owner_managed_access
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.resources_url, headers=headers, json=data) as resp:
                try:
                    result= await resp.json()
                except:
                    result = {"error": await resp.text()}

                if resp.status == 201:
                    result = await resp.json()

                    # reshape the output
                    output = {
                        'resource_name': result.get('name'),
                        'resource_id': result.get('_id'),
                        'resource_type': result.get('type'),
                        'owner_id': result.get('owner', {}).get('id'),
                        'resource_uris': result.get('uris'),
                        'resource_scopes': [item.get('name') for item in result.get('scopes')],
                        'owner_managed_access': result.get('ownerManagedAccess')
                    }

                    logger.debug(f"Resource created: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to create resource: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }


    async def create_user_based_policy(self, client_uuid, user, name, description, logic, decision_strategy):
        """
        Create a user-based authorization policy for a client.

        Args:
            client_uuid: Internal Keycloak client UUID.
            user: User identifier referenced by the policy.
            name: Policy name.
            description: Policy description.
            logic: Policy logic mode.
            decision_strategy: Policy decision strategy.

        Returns:
            dict: Success payload containing the created policy, or an error
            payload when creation fails.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "name": name,
            "description": description,
            "type": 'user',
            "logic": logic,
            "decisionStrategy": decision_strategy,
            "users": [user]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.clients_url}/{client_uuid}/authz/resource-server/policy/user", headers=headers, json=data) as resp:
                try:
                    result = await resp.json()
                except:
                    result = {"error": await resp.text()}

                logger.debug(f"policy creation result:{result}")

                if resp.status == 201:
                    result = await resp.json()
                    
                    # reshape the output
                    output = {
                        'id': result.get('id'),
                        'name': result.get('name'),
                        'description': result.get('description'),
                        'type': result.get('type'),
                        'logic': result.get('logic'),
                        'decisionStrategy': result.get('decisionStrategy'),
                        'users': result.get('users')
                    }

                    logger.debug(f"User-based policy created: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to create user-based policy: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }
    

    async def create_scope_based_permission(self, client_uuid, name, description, resources, scopes, policies):
        """
        Create a scope-based permission for a client.

        Args:
            client_uuid: Internal Keycloak client UUID.
            name: Permission name.
            description: Permission description.
            resources: Resource identifiers covered by the permission.
            scopes: Scope names covered by the permission.
            policies: Policy identifiers linked to the permission.

        Returns:
            dict: Success payload containing the created permission, or an error
            payload when creation fails.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json"
        }

        data = {
            "name": name,
            "description": description,
            "resources": resources,
            "scopes": scopes,
            "policies": policies
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.clients_url}/{client_uuid}/authz/resource-server/permission/scope", headers=headers, json=data) as resp:
                try:
                    result = await resp.json()
                except:
                    result = {"error": await resp.text()}

                logger.debug(f"permission creation result:{result}")

                if resp.status == 201:
                    result = await resp.json()
                    
                    # reshape the output
                    output = {
                        'id': result.get('id'),
                        'name': result.get('name'),
                        'description': result.get('description'),
                        'type': result.get('type'),
                        'logic': result.get('logic'),
                        'decisionStrategy': result.get('decisionStrategy'),
                        'users': result.get('users')
                    }

                    logger.debug(f"Scope-based permission created: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to create scope-based permission: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }
    

    async def get_all_policies(self, client_uuid):
        """
        Retrieve policies for a client authorization server.

        Args:
            client_uuid: Internal Keycloak client UUID.

        Returns:
            dict: Success payload or an error payload from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.clients_url}/{client_uuid}/authz/resource-server/policy", headers=headers) as resp:
                try:
                    result = await resp.json()
                except:
                    result = {"error": await resp.text()}

                logger.debug(f"result:{result}")

                if resp.status == 201:
                    result = await resp.json()
                    
                    # reshape the output
                    output = {
                        'id': result.get('id'),
                        'name': result.get('name'),
                        'description': result.get('description'),
                        'type': result.get('type'),
                        'logic': result.get('logic'),
                        'decisionStrategy': result.get('decisionStrategy'),
                        'users': result.get('users')
                    }

                    logger.debug(f"Scope-based permission created: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to create scope-based permission: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }


    async def get_all_permissions(self, client_uuid):
        """
        Retrieve permissions for a client authorization server.

        Args:
            client_uuid: Internal Keycloak client UUID.

        Returns:
            dict: Success payload or an error payload from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.clients_url}/{client_uuid}/authz/resource-server/permission", headers=headers) as resp:
                try:
                    result = await resp.json()
                except:
                    result = {"error": await resp.text()}

                logger.debug(f"result:{result}")

                if resp.status == 201:
                    result = await resp.json()
                    
                    # reshape the output
                    output = {
                        'id': result.get('id'),
                        'name': result.get('name'),
                        'description': result.get('description'),
                        'type': result.get('type'),
                        'logic': result.get('logic'),
                        'decisionStrategy': result.get('decisionStrategy'),
                        'users': result.get('users')
                    }

                    logger.debug(f"Scope-based permission created: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to create scope-based permission: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }
                

    async def delete_permission(self, client_uuid, permission_id):
        """
        Delete a permission from a client authorization server.

        Args:
            client_uuid: Internal Keycloak client UUID.
            permission_id: Permission identifier to remove.

        Returns:
            dict: Success payload or an error payload from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.delete(f"{self.clients_url}/{client_uuid}/authz/resource-server/permission/{permission_id}", headers=headers) as resp:
                try:
                    result = await resp.json()
                except:
                    result = {"error": await resp.text()}

                logger.debug(f"result:{result}")

                if resp.status == 201:
                    result = await resp.json()
                    
                    # reshape the output
                    output = {
                        'id': result.get('id'),
                        'name': result.get('name'),
                        'description': result.get('description'),
                        'type': result.get('type'),
                        'logic': result.get('logic'),
                        'decisionStrategy': result.get('decisionStrategy'),
                        'users': result.get('users')
                    }

                    logger.debug(f"Scope-based permission created: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to create scope-based permission: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }
                

    async def delete_policy(self, client_uuid, policy_id):
        """
        Delete a policy from a client authorization server.

        Args:
            client_uuid: Internal Keycloak client UUID.
            policy_id: Policy identifier to remove.

        Returns:
            dict: Success payload or an error payload from Keycloak.
        """
        await self.ensure_pat()
        
        headers = {
            "Authorization": f"Bearer {self.pat}"
        }

        async with aiohttp.ClientSession() as session:
            async with session.delete(f"{self.clients_url}/{client_uuid}/authz/resource-server/policy/{policy_id}", headers=headers) as resp:
                try:
                    result = await resp.json()
                except:
                    result = {"error": await resp.text()}

                logger.debug(f"result:{result}")

                if resp.status == 201:
                    result = await resp.json()
                    
                    # reshape the output
                    output = {
                        'id': result.get('id'),
                        'name': result.get('name'),
                        'description': result.get('description'),
                        'type': result.get('type'),
                        'logic': result.get('logic'),
                        'decisionStrategy': result.get('decisionStrategy'),
                        'users': result.get('users')
                    }

                    logger.debug(f"Scope-based permission created: {output}")
                    return {
                        "success": True, 
                        "data": output
                    }
                else:
                    logger.error(f"Failed to create scope-based permission: {resp.status}, {result}")
                    return {
                        "success": False,
                        "status": resp.status,
                        "error": result
                    }

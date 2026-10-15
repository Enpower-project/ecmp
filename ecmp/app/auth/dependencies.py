from fastapi.security import OAuth2PasswordBearer
from fastapi import Depends, HTTPException, Request, status
from typing import Optional

import logging
logger = logging.getLogger(__name__)

from app.keycloak_instance import kc


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def require_token(required_role: Optional[str] = None):
    async def verify_token(request: Request, token: str = Depends(oauth2_scheme)):
        # Skip JWT check for specific routes
        open_paths = ["/docs", "/openapi.json"]
        if request.url.path in open_paths:
            return None

        try:
            decoded_token = await kc.verify_token(token)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )
        
        # check for specific role enforcement
        if required_role:
            roles = decoded_token.get("realm_access", {}).get("roles", [])
            if required_role not in roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires {required_role} role"
                )
        
        return decoded_token
    
    return verify_token
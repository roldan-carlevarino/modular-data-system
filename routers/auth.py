import os
import bcrypt
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])

# ---- Config from env ----
JWT_SECRET = os.getenv("JWT_SECRET", "fallback-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24 * 7  # 7 days

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH", "")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---- Models ----
class Token(BaseModel):
    access_token: str
    token_type: str


class LoginBody(BaseModel):
    username: str
    password: str


# ---- Helpers ----
def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(subject: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """Dependency: extracts and validates the JWT. Returns the username."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ---- Endpoints ----
@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends()):
    """Authenticate with username + password, returns a JWT."""
    if form.username != AUTH_USERNAME:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not AUTH_PASSWORD_HASH:
        raise HTTPException(status_code=500, detail="Auth not configured on server")

    if not verify_password(form.password, AUTH_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(form.username)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(user: str = Depends(get_current_user)):
    """Return the current authenticated user."""
    return {"username": user}

"""
CGrep Spectrum Monitor — Authentication Router
Implements:
  - POST /api/auth/verify-password
  - GET  /api/webauthn/register/generate
  - POST /api/webauthn/register/verify
  - GET  /api/webauthn/authenticate/generate
  - POST /api/webauthn/authenticate/verify
  - POST /api/qr/generate
  - GET  /api/qr/poll/{session_id}
  - POST /api/qr/mobile-verify
  - GET  /api/auth/me
  - POST /api/auth/logout

Security rules enforced:
  - No tokens in localStorage — JWT delivered ONLY via HttpOnly, Secure, SameSite=Strict cookie
  - No public sign-up endpoint
  - All WebAuthn challenges and QR sessions expire in exactly 3 minutes
"""

import os
import uuid
import base64
import hmac
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Response, Request, Cookie, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from types import SimpleNamespace

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    PublicKeyCredentialDescriptor,
    AuthenticatorAttachment,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes

from models import SessionLocal, User, PasskeyCredential, AuthChallenge, PendingQRSession, Role, Permission, AuditLog

def utc_iso(dt) -> str | None:
    """Serialize a naive UTC datetime to ISO-8601 with Z suffix."""
    if dt is None:
        return None
    s = dt.isoformat()
    return s if s.endswith('Z') or '+' in s else s + 'Z'

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_SECRET_KEY   = os.environ.get('JWT_SECRET_KEY', 'CHANGE_THIS_IN_PRODUCTION_USE_A_LONG_RANDOM_SECRET')
_JWT_ALGO     = 'HS256'
_JWT_TTL_HOURS         = 3   # sessions expire after 3 hours
_JWT_TTL_DAYS          = 0   # kept for cookie max_age calc only
_CHALLENGE_TTL_MINUTES = 3
_QR_TTL_MINUTES        = 3

_revoked_jtis: set = set()
_pw_rate_buckets: dict = defaultdict(list)

# ---------------------------------------------------------------------------
# Global invalidation — any token issued before this UNIX timestamp is rejected.
# Persisted to disk so server restarts preserve the logout-all state.
# ---------------------------------------------------------------------------
_INVALIDATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.global_invalidation_ts')


def _load_global_invalidation() -> float:
    try:
        return float(open(_INVALIDATION_FILE).read().strip())
    except Exception:
        return 0.0


def _save_global_invalidation(ts: float):
    with open(_INVALIDATION_FILE, 'w') as f:
        f.write(str(ts))


_global_invalidation_ts: float = _load_global_invalidation()


def _rate_limit_password(ip: str, limit: int = 5, window: int = 60):
    now = time.time()
    bucket = [t for t in _pw_rate_buckets[ip] if now - t < window]
    bucket.append(now)
    _pw_rate_buckets[ip] = bucket
    if len(bucket) > limit:
        raise HTTPException(status_code=429, detail='Too many attempts — wait 60 seconds')

_RP_NAME = os.environ.get('WEBAUTHN_RP_NAME', 'CGrep Spectrum Monitor')


_RP_ID_OVERRIDE     = os.environ.get('WEBAUTHN_RP_ID', '').strip()
_ORIGIN_OVERRIDE    = os.environ.get('WEBAUTHN_ORIGIN', '').strip()
_PUBLIC_BASE_URL    = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')


def _get_rp_id(request: Request) -> str:
    """Derive rpId — env override wins, then request host, 127.0.0.1 → localhost."""
    if _RP_ID_OVERRIDE:
        return _RP_ID_OVERRIDE
    host = request.headers.get('host', 'localhost').split(':')[0]
    # WebAuthn spec explicitly allows 'localhost' but not raw IP addresses
    if host == '127.0.0.1':
        return 'localhost'
    return host


def _get_origin(request: Request) -> str:
    """Derive expected origin — env override wins, then reconstruct from request."""
    if _ORIGIN_OVERRIDE:
        return _ORIGIN_OVERRIDE
    host  = request.headers.get('host', 'localhost:8000')
    # If accessed via 127.0.0.1, rewrite to localhost so rpId matches
    host  = host.replace('127.0.0.1', 'localhost')
    scheme = 'https' if request.headers.get('x-forwarded-proto') == 'https' else 'http'
    return f'{scheme}://{host}'

_COOKIE_NAME   = 'dri_session'
_COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'

router = APIRouter()


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def _create_jwt(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        'sub': user_id,
        'jti': str(uuid.uuid4()),
        'iat': now,
        'exp': now + timedelta(hours=_JWT_TTL_HOURS),
    }
    return pyjwt.encode(payload, _SECRET_KEY, algorithm=_JWT_ALGO)


def _decode_jwt(token: str) -> Optional[dict]:
    global _global_invalidation_ts
    try:
        payload = pyjwt.decode(token, _SECRET_KEY, algorithms=[_JWT_ALGO])
        if payload.get('jti') in _revoked_jtis:
            return None
        # Reject tokens issued before the global invalidation timestamp
        iat = payload.get('iat', 0)
        iat_ts = iat.timestamp() if hasattr(iat, 'timestamp') else float(iat)
        if iat_ts < _global_invalidation_ts:
            return None
        return payload
    except Exception:
        return None


def _set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite='none' if _COOKIE_SECURE else 'lax',
        max_age=_JWT_TTL_HOURS * 3600,
        path='/',
    )


def _clear_auth_cookie(response: Response):
    response.delete_cookie(key=_COOKIE_NAME, path='/', httponly=True, samesite='strict')


# ---------------------------------------------------------------------------
# Test mode — bypass auth for stress testing
# ---------------------------------------------------------------------------
_TEST_USER = SimpleNamespace(
    id=uuid.UUID('00000000-0000-0000-0000-000000000001'),
    username='testmode',
    password_hash='',
    is_active=True,
    is_admin=False,
    display_name='Test Mode',
    avatar_url=None,
    roles=[],
    credentials=[],
)


# ---------------------------------------------------------------------------
# Auth dependency — used by protected routes
# ---------------------------------------------------------------------------
def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    if os.environ.get('DRI_TESTMODE'):
        return _TEST_USER
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail='Not authenticated')
    payload = _decode_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail='Invalid or expired session')
    user = db.query(User).filter(User.id == uuid.UUID(payload['sub'])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='User not found or inactive')
    return user


def get_user_from_token_or_cookie(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Accepts the session cookie OR the X-Next-Step-Token header.
    Used for registration endpoints where the cookie doesn't exist yet."""
    token = (
        request.cookies.get(_COOKIE_NAME)
        or request.headers.get('X-Next-Step-Token')
    )
    if not token:
        raise HTTPException(status_code=401, detail='Not authenticated')
    payload = _decode_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail='Invalid or expired token')
    user = db.query(User).filter(User.id == uuid.UUID(payload['sub'])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='User not found or inactive')
    return user


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class VerifyPasswordRequest(BaseModel):
    username:   str
    password:   str
    clientType: str  # 'desktop' | 'mobile'

    def validate_lengths(self):
        if len(self.username) > 128 or len(self.password) > 128:
            raise HTTPException(status_code=400, detail='Input too long')


class WebAuthnRegisterVerifyRequest(BaseModel):
    credential:  dict
    deviceName:  Optional[str] = 'My Device'


class WebAuthnAuthVerifyRequest(BaseModel):
    credential: dict


class QRMobileVerifyRequest(BaseModel):
    sessionId:  str
    credential: dict


# ---------------------------------------------------------------------------
# PHASE 1 — Password verification
# ---------------------------------------------------------------------------
@router.post('/api/auth/verify-password')
async def verify_password(
    body: VerifyPasswordRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    client_ip = get_client_ip(request)
    _rate_limit_password(client_ip)

    if len(body.username) > 128 or len(body.password) > 128:
        raise HTTPException(status_code=400, detail='Invalid credentials')

    user = db.query(User).filter(User.username == body.username).first()

    # Always run bcrypt to prevent timing oracle — use a dummy hash if user not found
    _DUMMY_HASH = '$2b$12$KIXoRNSxd4lTsyC9kZyXBOpnTv3fJiOyXGiIKzLFkDf7VYo5g2PKW'
    check_hash = user.password_hash if (user and user.is_active) else _DUMMY_HASH
    password_ok = bcrypt.checkpw(body.password.encode(), check_hash.encode())

    if not user or not user.is_active or not password_ok:
        _db2 = SessionLocal()
        background_tasks.add_task(
            log_audit_event, _db2, 'LOGIN_FAILED', 'Auth', client_ip,
            actor_id=user.id if user else None,
            details={'username': body.username, 'reason': 'bad_credentials'},
        )
        raise HTTPException(status_code=401, detail='Invalid credentials')

    user_id_str  = str(user.id)
    passkey_count = db.query(PasskeyCredential).filter(
        PasskeyCredential.user_id == user.id
    ).count()

    if passkey_count == 0:
        next_step_token = _create_jwt(user_id_str)
        return JSONResponse({
            'status': 'needs_registration',
            'nextStepToken': next_step_token,
        })

    next_step_token = _create_jwt(user_id_str)
    return JSONResponse({
        'status': 'needs_passkey',
        'clientType': body.clientType,
        'nextStepToken': next_step_token,
    })


# ---------------------------------------------------------------------------
# PHASE 2 — WebAuthn Registration (first-time mobile setup)
# ---------------------------------------------------------------------------
@router.get('/api/webauthn/register/generate')
async def webauthn_register_generate(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_from_token_or_cookie),
):
    rp_id  = _get_rp_id(request)
    origin = _get_origin(request)
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=_RP_NAME,
        user_id=str(current_user.id).encode(),
        user_name=current_user.username,
        user_display_name=current_user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
            for cred in current_user.credentials
        ],
    )

    # Upsert challenge
    expires = datetime.now(timezone.utc) + timedelta(minutes=_CHALLENGE_TTL_MINUTES)
    challenge_b64 = bytes_to_base64url(options.challenge)

    existing = db.query(AuthChallenge).filter(AuthChallenge.user_id == current_user.id).first()
    if existing:
        existing.challenge_string = challenge_b64
        existing.expires_at       = expires.replace(tzinfo=None)
    else:
        db.add(AuthChallenge(
            user_id=current_user.id,
            challenge_string=challenge_b64,
            expires_at=expires.replace(tzinfo=None),
        ))
    db.commit()

    import webauthn, json as _json
    return Response(content=webauthn.options_to_json(options), media_type='application/json')


@router.post('/api/webauthn/register/verify')
async def webauthn_register_verify(
    body: WebAuthnRegisterVerifyRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_from_token_or_cookie),
):
    challenge_row = db.query(AuthChallenge).filter(AuthChallenge.user_id == current_user.id).first()
    if not challenge_row:
        raise HTTPException(status_code=400, detail='No pending challenge')
    if datetime.utcnow() > challenge_row.expires_at:
        raise HTTPException(status_code=400, detail='Challenge expired')

    rp_id  = _get_rp_id(request)
    origin = _get_origin(request)
    try:
        verification = verify_registration_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(challenge_row.challenge_string),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'WebAuthn verification failed: {exc}')

    db.query(AuthChallenge).filter(AuthChallenge.user_id == current_user.id).delete()

    db.add(PasskeyCredential(
        user_id=current_user.id,
        credential_id=bytes_to_base64url(verification.credential_id),
        public_key=bytes_to_base64url(verification.credential_public_key),
        sign_count=verification.sign_count,
        device_name=body.deviceName or 'My Device',
    ))
    db.commit()

    token = _create_jwt(str(current_user.id))
    resp  = JSONResponse({'status': 'registered'})
    _set_auth_cookie(resp, token)
    return resp


# ---------------------------------------------------------------------------
# PHASE 3 — Mobile native WebAuthn login
# ---------------------------------------------------------------------------
@router.get('/api/webauthn/authenticate/generate')
async def webauthn_auth_generate(
    request: Request,
    username: str,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=404, detail='User not found')

    if not user.credentials:
        raise HTTPException(status_code=400, detail='No passkeys registered for this user')

    rp_id  = _get_rp_id(request)
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in user.credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    expires = datetime.now(timezone.utc) + timedelta(minutes=_CHALLENGE_TTL_MINUTES)
    challenge_b64 = bytes_to_base64url(options.challenge)

    existing = db.query(AuthChallenge).filter(AuthChallenge.user_id == user.id).first()
    if existing:
        existing.challenge_string = challenge_b64
        existing.expires_at       = expires.replace(tzinfo=None)
    else:
        db.add(AuthChallenge(
            user_id=user.id,
            challenge_string=challenge_b64,
            expires_at=expires.replace(tzinfo=None),
        ))
    db.commit()

    import webauthn
    return Response(content=webauthn.options_to_json(options), media_type='application/json')


@router.post('/api/webauthn/authenticate/verify')
async def webauthn_auth_verify(
    body: WebAuthnAuthVerifyRequest,
    response: Response,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    raw_id = body.credential.get('rawId') or body.credential.get('id')
    if not raw_id:
        raise HTTPException(status_code=400, detail='Missing credential id')

    cred_row = db.query(PasskeyCredential).filter(
        PasskeyCredential.credential_id == raw_id
    ).first()
    if not cred_row:
        raise HTTPException(status_code=400, detail='Unknown credential')

    user = db.query(User).filter(User.id == cred_row.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='User inactive')

    challenge_row = db.query(AuthChallenge).filter(AuthChallenge.user_id == user.id).first()
    if not challenge_row:
        raise HTTPException(status_code=400, detail='No pending challenge')
    if datetime.utcnow() > challenge_row.expires_at:
        raise HTTPException(status_code=400, detail='Challenge expired')

    rp_id  = _get_rp_id(request)
    origin = _get_origin(request)
    try:
        verification = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(challenge_row.challenge_string),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(cred_row.public_key),
            credential_current_sign_count=cred_row.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'WebAuthn verification failed: {exc}')

    db.query(AuthChallenge).filter(AuthChallenge.user_id == user.id).delete()
    cred_row.sign_count      = verification.new_sign_count
    cred_row.last_login_at   = datetime.utcnow()
    cred_row.last_login_ip   = get_client_ip(request)
    cred_row.last_user_agent = request.headers.get('User-Agent', '')[:512]
    db.commit()

    _ip = get_client_ip(request)
    _ua = request.headers.get('User-Agent', '')[:512]
    _db2 = SessionLocal()
    background_tasks.add_task(
        log_audit_event, _db2, 'LOGIN_SUCCESS', 'Auth', _ip,
        actor_id=user.id,
        details={'method': 'webauthn', 'credential_id': raw_id[:16] + '…'},
        user_agent=_ua,
    )

    token = _create_jwt(str(user.id))
    resp  = JSONResponse({'status': 'authenticated', 'username': user.username})
    _set_auth_cookie(resp, token)
    return resp


# ---------------------------------------------------------------------------
# PHASE 4 — Desktop QR flow
# ---------------------------------------------------------------------------
@router.post('/api/qr/generate')
async def qr_generate(
    request: Request,
    db: Session = Depends(get_db),
):
    token_header = request.headers.get('X-Next-Step-Token')
    token_cookie = request.cookies.get(_COOKIE_NAME)
    token = token_header or token_cookie

    if not token:
        raise HTTPException(status_code=401, detail='Not authenticated')
    payload = _decode_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail='Invalid token')

    user = db.query(User).filter(User.id == uuid.UUID(payload['sub'])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='User not found')

    pk_count = db.query(PasskeyCredential).filter(PasskeyCredential.user_id == user.id).count()
    is_registration = pk_count == 0  # first-time: phone will register; repeat: phone will authenticate

    poll_token = base64.urlsafe_b64encode(os.urandom(32)).decode()
    poll_token_hash = hashlib.sha256(poll_token.encode()).hexdigest()

    # 6-digit PIN displayed on desktop — mobile must submit it to prove physical proximity
    pin = f'{int.from_bytes(os.urandom(3), "big") % 1000000:06d}'
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()

    desktop_ip = get_client_ip(request)

    expires = datetime.now(timezone.utc) + timedelta(minutes=_QR_TTL_MINUTES)
    # Store next_step_token for registration flow — phone needs it to call register/generate
    next_step_token = _create_jwt(str(user.id))
    session = PendingQRSession(
        user_id=user.id,
        status='pending',
        expires_at=expires.replace(tzinfo=None),
        auth_token=poll_token_hash,
        next_step_token=next_step_token if is_registration else None,
    )
    # Attach PIN hash and desktop IP to session for binding
    session.pin_hash   = pin_hash
    session.desktop_ip = desktop_ip
    db.add(session)
    db.commit()
    db.refresh(session)

    session_id = str(session.session_id)
    origin     = _PUBLIC_BASE_URL or _get_origin(request)
    verify_url = f'{origin}/verify-qr/{session_id}'

    return JSONResponse({
        'sessionId':      session_id,
        'pollToken':      poll_token,
        'verifyUrl':      verify_url,
        'expiresIn':      _QR_TTL_MINUTES * 60,
        'isRegistration': is_registration,
        'pin':            pin,  # shown on desktop screen — mobile must enter this
    })


@router.get('/api/qr/status/{session_id}')
async def qr_status(
    session_id: str,
    db: Session = Depends(get_db),
):
    """Public endpoint — mobile uses this to verify the session exists before biometric prompt.
    Returns only liveness info, never delivers a cookie or token."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid session id')

    session = db.query(PendingQRSession).filter(PendingQRSession.session_id == sid).first()
    if not session:
        raise HTTPException(status_code=404, detail='Session not found')

    if datetime.utcnow() > session.expires_at:
        return JSONResponse({'alive': False, 'reason': 'expired'})

    if session.status != 'pending':
        return JSONResponse({'alive': False, 'reason': session.status})

    user = db.query(User).filter(User.id == session.user_id).first()
    pk_count = db.query(PasskeyCredential).filter(PasskeyCredential.user_id == session.user_id).count()
    resp_data = {
        'alive':           True,
        'username':        user.username if user else '',
        'is_registration': pk_count == 0,
    }
    # Only expose next_step_token for registration — one-time use, expires with session
    if pk_count == 0 and session.next_step_token:
        resp_data['next_step_token'] = session.next_step_token
    return JSONResponse(resp_data)


@router.get('/api/qr/poll/{session_id}')
async def qr_poll(
    session_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    # Require poll_token in header — only the desktop that created the QR has it
    poll_token = request.headers.get('X-Poll-Token', '')
    if not poll_token:
        raise HTTPException(status_code=401, detail='Missing poll token')

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid session id')

    session = db.query(PendingQRSession).filter(PendingQRSession.session_id == sid).first()
    if not session:
        raise HTTPException(status_code=404, detail='Session not found')

    # Verify poll_token matches the hash stored at creation time
    expected_hash = hashlib.sha256(poll_token.encode()).hexdigest()
    if not hmac.compare_digest(expected_hash, session.auth_token or ''):
        raise HTTPException(status_code=403, detail='Invalid poll token')

    if datetime.utcnow() > session.expires_at:
        session.status = 'expired'
        db.commit()
        return JSONResponse({'status': 'expired'})

    if session.status == 'verified' and session.verified_jwt:
        resp = JSONResponse({'status': 'verified'})
        _set_auth_cookie(resp, session.verified_jwt)
        db.delete(session)
        db.commit()
        return resp

    return JSONResponse({'status': session.status})


@router.post('/api/qr/mobile-verify')
async def qr_mobile_verify(
    body: QRMobileVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        sid = uuid.UUID(body.sessionId)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid session id')

    session = db.query(PendingQRSession).filter(PendingQRSession.session_id == sid).first()
    if not session:
        raise HTTPException(status_code=404, detail='QR session not found')
    if datetime.utcnow() > session.expires_at:
        raise HTTPException(status_code=400, detail='QR session expired')
    if session.status != 'pending':
        raise HTTPException(status_code=400, detail='Session already used')

    # PIN binding check — mobile must submit the PIN shown on the desktop screen
    submitted_pin = str(body.credential.get('pin') or '')
    stored_pin_hash = getattr(session, 'pin_hash', None)
    if stored_pin_hash:
        submitted_hash = hashlib.sha256(submitted_pin.encode()).hexdigest()
        if not hmac.compare_digest(submitted_hash, stored_pin_hash):
            raise HTTPException(status_code=403, detail='Invalid PIN')

    user = db.query(User).filter(User.id == session.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='User inactive')

    cred_count = db.query(PasskeyCredential).filter(PasskeyCredential.user_id == user.id).count()
    if cred_count == 0:
        raise HTTPException(status_code=400, detail='No passkeys registered')

    raw_id = body.credential.get('rawId') or body.credential.get('id')
    cred_row = db.query(PasskeyCredential).filter(
        PasskeyCredential.credential_id == raw_id,
        PasskeyCredential.user_id == user.id,
    ).first()
    if not cred_row:
        raise HTTPException(status_code=400, detail='Unknown credential')

    challenge_row = db.query(AuthChallenge).filter(AuthChallenge.user_id == user.id).first()
    if not challenge_row:
        raise HTTPException(status_code=400, detail='No pending challenge')
    if datetime.utcnow() > challenge_row.expires_at:
        raise HTTPException(status_code=400, detail='Challenge expired')

    rp_id  = _get_rp_id(request)
    origin = _get_origin(request)
    try:
        verification = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(challenge_row.challenge_string),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(cred_row.public_key),
            credential_current_sign_count=cred_row.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'WebAuthn verification failed: {exc}')

    db.query(AuthChallenge).filter(AuthChallenge.user_id == user.id).delete()
    cred_row.sign_count      = verification.new_sign_count
    cred_row.last_login_at   = datetime.utcnow()
    cred_row.last_login_ip   = get_client_ip(request)
    cred_row.last_user_agent = request.headers.get('User-Agent', '')[:512]

    jwt_token = _create_jwt(str(user.id))
    session.status       = 'verified'
    session.verified_jwt = jwt_token
    db.commit()

    mobile_resp = JSONResponse({'status': 'ok', 'message': 'Desktop session unlocked'})
    _set_auth_cookie(mobile_resp, jwt_token)
    return mobile_resp


# ---------------------------------------------------------------------------
# Session check + logout
# ---------------------------------------------------------------------------
def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail='Admin access required')
    return current_user


@router.get('/api/auth/me')
async def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    perms = get_user_permissions(db, current_user.id)
    roles = [{'name': r.name, 'priority_level': r.priority_level} for r in current_user.roles]
    return {
        'id':           str(current_user.id),
        'username':     current_user.username,
        'is_admin':     current_user.is_admin,
        'roles':        roles,
        'permissions':  sorted(perms),
        'display_name': current_user.display_name,
        'avatar_url':   current_user.avatar_url,
    }


@router.post('/api/auth/logout')
async def logout(request: Request, response: Response, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    token = request.cookies.get(_COOKIE_NAME)
    actor_id = None
    if token:
        payload = _decode_jwt(token)
        if payload:
            if payload.get('jti'):
                _revoked_jtis.add(payload['jti'])
            try:
                actor_id = uuid.UUID(payload.get('sub', ''))
            except (ValueError, AttributeError):
                pass
    _ip = get_client_ip(request)
    _db2 = SessionLocal()
    background_tasks.add_task(
        log_audit_event, _db2, 'LOGOUT', 'Auth', _ip,
        actor_id=actor_id, details={},
    )
    resp = JSONResponse({'status': 'logged_out'})
    _clear_auth_cookie(resp)
    return resp


@router.post('/api/admin/logout-all')
async def logout_all(
    request: Request,
    background_tasks: BackgroundTasks,
    _user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Immediately invalidates every active session across all users.
    Any JWT issued before this moment will be rejected on next request.
    The timestamp is persisted to disk so it survives server restarts."""
    global _global_invalidation_ts
    ts = time.time()
    _global_invalidation_ts = ts
    _save_global_invalidation(ts)
    _ip = get_client_ip(request)
    _db2 = SessionLocal()
    background_tasks.add_task(
        log_audit_event, _db2, 'LOGOUT_ALL', 'Auth', _ip,
        actor_id=_user.id, details={'invalidated_before': datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()},
    )
    return JSONResponse({
        'status': 'all_sessions_invalidated',
        'invalidated_before': datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
    })


@router.get('/api/admin/session-info')
async def session_info(request: Request, _user: User = Depends(get_admin_user)):
    """Returns current session stats and global invalidation time."""
    token = request.cookies.get(_COOKIE_NAME)
    payload = _decode_jwt(token) if token else {}

    def _ts_to_iso(v):
        if not v: return None
        ts = v.timestamp() if hasattr(v, 'timestamp') else float(v)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    return JSONResponse({
        'username':              _user.username,
        'session_issued_at':     _ts_to_iso(payload.get('iat')),
        'session_expires_at':    _ts_to_iso(payload.get('exp')),
        'global_invalidated_at': _ts_to_iso(_global_invalidation_ts) if _global_invalidation_ts else None,
        'session_ttl_hours':     _JWT_TTL_HOURS,
    })


# ---------------------------------------------------------------------------
# RBAC — core utilities
# ---------------------------------------------------------------------------

def get_user_permissions(db: Session, user_id) -> set:
    """Return a flat set of permission name strings for the given user_id."""
    if os.environ.get('DRI_TESTMODE') and _TEST_USER.is_admin:
        # Test user is a full admin; grant every permission in the system.
        return {p.name for p in db.query(Permission).all()}
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return set()
    perms: set = set()
    for role in user.roles:
        for perm in role.permissions:
            perms.add(perm.name)
    # SuperAdmin / is_admin always gets every permission that exists
    if user.is_admin:
        for p in db.query(Permission).all():
            perms.add(p.name)
    return perms


def _user_max_priority(user: User) -> int:
    """Highest priority_level among all roles the user holds; 999 for is_admin."""
    if user.is_admin:
        return 999
    if not user.roles:
        return 0
    return max(r.priority_level for r in user.roles)


def require_permission(required_perm: str):
    """FastAPI dependency factory — raises 403 when user lacks required_perm."""
    def _dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if os.environ.get('DRI_TESTMODE'):
            return current_user
        perms = get_user_permissions(db, current_user.id)
        if required_perm not in perms:
            raise HTTPException(status_code=403, detail='Insufficient clearance')
        return current_user
    return _dependency


# ---------------------------------------------------------------------------
# RBAC — Pydantic schemas
# ---------------------------------------------------------------------------

class AssignRoleRequest(BaseModel):
    user_id: str
    role_id: str

class RemoveRoleRequest(BaseModel):
    user_id: str
    role_id: str

class CreateRoleRequest(BaseModel):
    name:           str
    priority_level: int = 0

class CreatePermissionRequest(BaseModel):
    name: str

class SetRolePermissionsRequest(BaseModel):
    role_id:        str
    permission_ids: list


# ---------------------------------------------------------------------------
# RBAC — Admin endpoints  (all require manage:rbac permission)
# ---------------------------------------------------------------------------

@router.get('/api/admin/permissions')
async def list_permissions(
    _user: User = Depends(require_permission('manage:rbac')),
    db: Session = Depends(get_db),
):
    perms = db.query(Permission).order_by(Permission.name).all()
    return [{'id': str(p.id), 'name': p.name} for p in perms]


@router.post('/api/admin/permissions')
async def create_permission(
    body: CreatePermissionRequest,
    caller: User = Depends(require_permission('manage:rbac')),
    db: Session = Depends(get_db),
):
    if db.query(Permission).filter(Permission.name == body.name).first():
        raise HTTPException(400, 'Permission already exists')
    p = Permission(name=body.name.strip())
    db.add(p)
    db.commit()
    db.refresh(p)
    return {'id': str(p.id), 'name': p.name}


@router.get('/api/admin/roles')
async def list_roles(
    _user: User = Depends(require_permission('manage:rbac')),
    db: Session = Depends(get_db),
):
    roles = db.query(Role).order_by(Role.priority_level.desc()).all()
    return [
        {
            'id':             str(r.id),
            'name':           r.name,
            'priority_level': r.priority_level,
            'permissions':    [{'id': str(p.id), 'name': p.name} for p in r.permissions],
        }
        for r in roles
    ]


@router.post('/api/admin/roles')
async def create_role(
    body: CreateRoleRequest,
    caller: User = Depends(require_permission('manage:rbac')),
    db: Session = Depends(get_db),
):
    if _user_max_priority(caller) <= body.priority_level and not caller.is_admin:
        raise HTTPException(403, 'Cannot create a role with equal or higher priority than your own')
    if db.query(Role).filter(Role.name == body.name).first():
        raise HTTPException(400, 'Role already exists')
    r = Role(name=body.name.strip(), priority_level=body.priority_level)
    db.add(r)
    db.commit()
    db.refresh(r)
    return {'id': str(r.id), 'name': r.name, 'priority_level': r.priority_level}


@router.put('/api/admin/roles/{role_id}/permissions')
async def set_role_permissions(
    role_id: str,
    body: SetRolePermissionsRequest,
    caller: User = Depends(require_permission('manage:rbac')),
    db: Session = Depends(get_db),
):
    role = db.query(Role).filter(Role.id == uuid.UUID(role_id)).first()
    if not role:
        raise HTTPException(404, 'Role not found')
    if _user_max_priority(caller) < role.priority_level and not caller.is_admin:
        raise HTTPException(403, 'Cannot edit a role with higher priority than your own')
    caller_perms = get_user_permissions(db, caller.id)
    new_perms = []
    for pid in body.permission_ids:
        p = db.query(Permission).filter(Permission.id == uuid.UUID(pid)).first()
        if not p:
            raise HTTPException(404, f'Permission {pid} not found')
        if p.name not in caller_perms and not caller.is_admin:
            raise HTTPException(403, f'You do not hold permission "{p.name}" and cannot assign it')
        new_perms.append(p)
    role.permissions = new_perms
    db.commit()
    return {'status': 'ok', 'permissions': [p.name for p in new_perms]}


@router.get('/api/admin/users')
async def list_users(
    _user: User = Depends(require_permission('manage:users')),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at).all()
    return [
        {
            'id':         str(u.id),
            'username':   u.username,
            'is_admin':   u.is_admin,
            'is_active':  u.is_active,
            'created_at': utc_iso(u.created_at),
            'roles': [{'id': str(r.id), 'name': r.name, 'priority_level': r.priority_level} for r in u.roles],
            'devices': [
                {
                    'id':             str(c.id),
                    'device_name':    c.device_name,
                    'created_at':     utc_iso(c.created_at),
                    'last_login_at':  utc_iso(c.last_login_at),
                    'last_login_ip':  c.last_login_ip,
                    'last_user_agent': c.last_user_agent,
                }
                for c in u.credentials
            ],
        }
        for u in users
    ]


@router.get('/api/admin/users/{user_id}/logins')
async def user_login_history(
    user_id: str,
    _caller: User = Depends(require_permission('manage:users')),
    db: Session = Depends(get_db),
    limit: int = 20,
):
    """Return the last N login events for a user from the audit log."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, 'Invalid user_id')
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.actor_id == uid, AuditLog.action.in_(['LOGIN_SUCCESS', 'LOGIN_FAILED']))
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            'timestamp':  utc_iso(r.timestamp),
            'action':     r.action,
            'ip_address': r.ip_address,
            'user_agent': r.user_agent,
            'details':    r.details,
        }
        for r in rows
    ]


@router.post('/api/admin/roles/assign')
async def assign_role(
    body: AssignRoleRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    caller: User = Depends(require_permission('manage:users')),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == uuid.UUID(body.user_id)).first()
    if not target:
        raise HTTPException(404, 'User not found')
    role = db.query(Role).filter(Role.id == uuid.UUID(body.role_id)).first()
    if not role:
        raise HTTPException(404, 'Role not found')
    if _user_max_priority(caller) < role.priority_level and not caller.is_admin:
        raise HTTPException(403, 'Cannot assign a role with higher priority than your own')
    if role not in target.roles:
        target.roles.append(role)
        db.commit()
    _ip = get_client_ip(request)
    _db2 = SessionLocal()
    background_tasks.add_task(
        log_audit_event, _db2, 'ROLE_ASSIGNED', 'RBAC', _ip,
        actor_id=caller.id,
        details={'target_user_id': str(target.id), 'target_username': target.username,
                 'role_id': str(role.id), 'role_name': role.name},
    )
    return {'status': 'ok'}


@router.post('/api/admin/roles/remove')
async def remove_role(
    body: RemoveRoleRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    caller: User = Depends(require_permission('manage:users')),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == uuid.UUID(body.user_id)).first()
    if not target:
        raise HTTPException(404, 'User not found')
    role = db.query(Role).filter(Role.id == uuid.UUID(body.role_id)).first()
    if not role:
        raise HTTPException(404, 'Role not found')
    if _user_max_priority(caller) < role.priority_level and not caller.is_admin:
        raise HTTPException(403, 'Cannot remove a role with higher priority than your own')
    if role in target.roles:
        target.roles.remove(role)
        db.commit()
    _ip = get_client_ip(request)
    _db2 = SessionLocal()
    background_tasks.add_task(
        log_audit_event, _db2, 'ROLE_REMOVED', 'RBAC', _ip,
        actor_id=caller.id,
        details={'target_user_id': str(target.id), 'target_username': target.username,
                 'role_id': str(role.id), 'role_name': role.name},
    )
    return {'status': 'ok'}


# ---------------------------------------------------------------------------
# Audit Logging — utilities
# ---------------------------------------------------------------------------

def get_client_ip(request: Request) -> str:
    """Extract real client IP respecting Cloudflare Tunnel headers."""
    cf_ip = request.headers.get('CF-Connecting-IP')
    if cf_ip:
        return cf_ip.strip()
    fwd = request.headers.get('X-Forwarded-For')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


def log_audit_event(
    db: Session,
    action: str,
    resource_type: str,
    ip_address: str,
    actor_id=None,
    details: dict | None = None,
    user_agent: str | None = None,
) -> None:
    """Write one audit log row. Designed to be called inside a BackgroundTask."""
    try:
        entry = AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details or {},
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f'[audit] ERROR writing {action}: {exc}')
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Audit Logs — read endpoint
# ---------------------------------------------------------------------------

@router.get('/api/admin/audit-logs')
async def list_audit_logs(
    request: Request,
    _caller: User = Depends(require_permission('view:audit_logs')),
    db: Session = Depends(get_db),
    limit:      int      = 50,
    offset:     int      = 0,
    action:     Optional[str] = None,
    actor_id:   Optional[str] = None,
    start_date: Optional[str] = None,
    end_date:   Optional[str] = None,
):
    q = db.query(AuditLog).order_by(AuditLog.timestamp.desc())

    if action:
        q = q.filter(AuditLog.action == action)
    if actor_id:
        try:
            q = q.filter(AuditLog.actor_id == uuid.UUID(actor_id))
        except ValueError:
            raise HTTPException(400, 'Invalid actor_id UUID')
    if start_date:
        try:
            q = q.filter(AuditLog.timestamp >= datetime.fromisoformat(start_date))
        except ValueError:
            raise HTTPException(400, 'Invalid start_date (use ISO 8601)')
    if end_date:
        try:
            q = q.filter(AuditLog.timestamp <= datetime.fromisoformat(end_date))
        except ValueError:
            raise HTTPException(400, 'Invalid end_date (use ISO 8601)')

    total = q.count()
    rows  = q.offset(offset).limit(limit).all()

    return {
        'total': total,
        'offset': offset,
        'limit': limit,
        'logs': [
            {
                'id':            str(r.id),
                'timestamp':     utc_iso(r.timestamp),
                'actor_id':      str(r.actor_id) if r.actor_id else None,
                'actor':         r.actor.username if r.actor else None,
                'action':        r.action,
                'resource_type': r.resource_type,
                'ip_address':    r.ip_address,
                'details':       r.details,
            }
            for r in rows
        ],
    }

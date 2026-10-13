import hashlib
import secrets
from datetime import timedelta
import jwt
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from .models import AuthSession, RefreshCredential


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def access_token(session):
    now = timezone.now()
    return jwt.encode(
        {
            "sub": str(session.user_id),
            "sid": str(session.id),
            "iat": now,
            "exp": min(now + timedelta(minutes=5), session.expires_at),
            "iss": "seatlock",
            "aud": "seatlock-api",
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def new_credential(session):
    raw = secrets.token_urlsafe(48)
    RefreshCredential.objects.create(session=session, digest=digest(raw), expires_at=session.expires_at)
    return raw


def create_session(user):
    with transaction.atomic():
        session = AuthSession.objects.create(user=user, expires_at=timezone.now() + timedelta(days=7))
        return access_token(session), new_credential(session)


def rotate(raw):
    candidate = RefreshCredential.objects.filter(digest=digest(raw)).first()
    if not candidate:
        raise AuthenticationFailed("Invalid refresh token.")
    result = None
    with transaction.atomic():
        session = AuthSession.objects.select_for_update().get(id=candidate.session_id)
        credential = RefreshCredential.objects.select_for_update().get(id=candidate.id)
        if credential.consumed:
            session.revoked = True
            session.save(update_fields=["revoked"])
        elif not session.revoked and session.expires_at > timezone.now():
            credential.consumed = True
            credential.save(update_fields=["consumed"])
            result = access_token(session), new_credential(session)
    # Raise outside the transaction so replay revocation cannot roll back.
    if result is None:
        raise AuthenticationFailed("Session expired or refresh replay detected. Sign in again.")
    return result


class AccessAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            return None
        if len(header) != 2 or header[0].lower() != b"bearer":
            raise AuthenticationFailed("Expected Bearer access token.")
        try:
            claims = jwt.decode(
                header[1],
                settings.JWT_SECRET,
                algorithms=["HS256"],
                audience="seatlock-api",
                issuer="seatlock",
                options={"require": ["sub", "sid", "exp", "iat"]},
            )
            session = AuthSession.objects.select_related("user").get(
                id=claims["sid"], user_id=claims["sub"], revoked=False, expires_at__gt=timezone.now()
            )
            if not session.user.is_active:
                raise AuthenticationFailed("Inactive account.")
            return session.user, session
        except (
            jwt.PyJWTError,
            AuthSession.DoesNotExist,
            ValueError,
            TypeError,
            DjangoValidationError,
            DjangoValidationError,
        ):
            raise AuthenticationFailed("Invalid or revoked access token.")

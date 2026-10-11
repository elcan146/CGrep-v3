import os
from sqlalchemy import create_engine, Column, Integer, Float, DateTime, String, Boolean, ForeignKey, Text, Table, UniqueConstraint, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from cryptography.fernet import Fernet
from datetime import datetime
import uuid

Base = declarative_base()

# ---------------------------------------------------------------------------
# RBAC — association tables (no ORM class needed, plain Table objects)
# ---------------------------------------------------------------------------

role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id',       PG_UUID(as_uuid=True), ForeignKey('roles.id',       ondelete='CASCADE'), primary_key=True),
    Column('permission_id', PG_UUID(as_uuid=True), ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True),
)

user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', PG_UUID(as_uuid=True), ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
)


class Role(Base):
    __tablename__ = 'roles'

    id             = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name           = Column(String(100), unique=True, nullable=False)
    priority_level = Column(Integer, default=0, nullable=False)

    permissions = relationship('Permission', secondary=role_permissions, back_populates='roles')
    users       = relationship('User',       secondary=user_roles,       back_populates='roles')


class Permission(Base):
    __tablename__ = 'permissions'

    id   = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), unique=True, nullable=False)

    roles = relationship('Role', secondary=role_permissions, back_populates='permissions')

class SweepData(Base):
    __tablename__ = 'rf_sweep_data'
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    frequency_mhz = Column(Float, index=True)
    power_dbfs = Column(Float)
    floor_dbfs = Column(Float, nullable=True)
    snr_db = Column(Float)
    sdr_id = Column(String(100), nullable=True, index=True)

    __table_args__ = (
        __import__('sqlalchemy').Index('ix_sweep_ts_freq', 'timestamp', 'frequency_mhz'),
    )


class SignalEvent(Base):
    """rtl-sdr-analyzer style signal event logging.
    
    Stores detected signals with confidence scores and interference flags
    for advanced analysis and jamming detection.
    """
    __tablename__ = 'signal_events'
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    frequency_mhz = Column(Float, index=True)
    power_dbfs = Column(Float)
    bandwidth_hz = Column(Float, nullable=True)
    duration_sec = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)  # Z-score based confidence
    event_type = Column(String(50), default="detection")  # detection, interference, jamming


# ---------------------------------------------------------------------------
# Station Outage Tracking
# ---------------------------------------------------------------------------

class StationOutage(Base):
    """Records a detected signal-loss window for a known licensed station."""
    __tablename__ = 'station_outages'

    id            = Column(Integer, primary_key=True, index=True)
    station_freq  = Column(Float, nullable=False, index=True)  # MHz e.g. 106.3
    station_name  = Column(String(100), nullable=True)
    started_at    = Column(DateTime, nullable=False, index=True)
    ended_at      = Column(DateTime, nullable=True)             # NULL = still ongoing
    duration_sec  = Column(Float, nullable=True)                # filled when ended_at set
    min_power     = Column(Float, nullable=True)                # lowest power seen during outage

    @property
    def duration_min(self):
        if self.duration_sec is not None:
            return round(self.duration_sec / 60, 2)
        return None


# ---------------------------------------------------------------------------
# Audit Logging
# ---------------------------------------------------------------------------

class DeviceSession(Base):
    """One row per unique (user_id, session_id, ip_address, user_agent) — updated on every request."""
    __tablename__ = 'device_sessions'

    id            = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id       = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    session_id    = Column(String(64),  nullable=True, index=True)
    ip_address    = Column(String(64),  nullable=True)
    user_agent    = Column(String(512), nullable=True)
    first_seen    = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen     = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    request_count = Column(Integer, default=1, nullable=False)
    device_hints  = Column(JSONB, nullable=True)   # Sec-CH-UA Client Hints: brand, model, platform

    __table_args__ = (
        UniqueConstraint('user_id', 'session_id', 'ip_address', 'user_agent', name='uq_device_session'),
    )

    user = relationship('User', foreign_keys=[user_id])


class ActivityLog(Base):
    """Comprehensive per-request + frontend interaction tracking."""
    __tablename__ = 'activity_logs'

    id           = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp    = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id      = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    session_id   = Column(String(64), nullable=True, index=True)
    category     = Column(String(50),  nullable=False, index=True)   # API | UI | WS | ERROR
    event_type   = Column(String(100), nullable=False, index=True)   # e.g. GET /api/history, CLICK_EXPORT
    method       = Column(String(10),  nullable=True)
    path         = Column(String(512), nullable=True)
    status_code  = Column(Integer,     nullable=True)
    duration_ms  = Column(Integer,     nullable=True)
    ip_address   = Column(String(64),  nullable=True)
    user_agent   = Column(String(512), nullable=True)
    payload      = Column(JSONB, default=dict)

    user         = relationship('User', foreign_keys=[user_id], lazy='select')


class AuditLog(Base):
    __tablename__ = 'audit_logs'

    id            = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp     = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor_id      = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    action        = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False)
    ip_address    = Column(String(64))
    user_agent    = Column(String(512), nullable=True)
    details       = Column(JSONB, default=dict)

    actor         = relationship('User', foreign_keys=[actor_id], lazy='joined')


# ---------------------------------------------------------------------------
# Authentication Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = 'users'

    id            = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username      = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active     = Column(Boolean, default=True)
    is_admin      = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=datetime.utcnow)
    display_name  = Column(String(100), nullable=True)
    avatar_url    = Column(String(512), nullable=True)

    chat_messages = relationship('ChatMessage', foreign_keys='ChatMessage.user_id', back_populates='sender', lazy='dynamic')
    credentials   = relationship('PasskeyCredential', back_populates='user', cascade='all, delete-orphan')
    challenge     = relationship('AuthChallenge',      back_populates='user', uselist=False, cascade='all, delete-orphan')
    qr_sessions   = relationship('PendingQRSession',   back_populates='user', cascade='all, delete-orphan')
    roles         = relationship('Role',               secondary='user_roles', back_populates='users')
    audit_logs    = relationship('AuditLog',           foreign_keys='AuditLog.actor_id', back_populates='actor')
    activity_logs = relationship('ActivityLog',        foreign_keys='ActivityLog.user_id', overlaps='user')


# ---------------------------------------------------------------------------
# Map Annotations — admin-placed glowing dot clusters
# ---------------------------------------------------------------------------

class MapAnnotation(Base):
    """A glowing dot cluster placed on the 3D map by an admin."""
    __tablename__ = 'map_annotations'

    id         = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    lon        = Column(Float, nullable=False)   # WGS-84 longitude
    lat        = Column(Float, nullable=False)   # WGS-84 latitude
    label      = Column(String(200), nullable=True)
    color      = Column(String(20), nullable=False, default='#00e5ff')
    radius     = Column(Float, nullable=False, default=8.0)  # dot cluster visual radius px

    creator = relationship('User', foreign_keys=[created_by])


# ---------------------------------------------------------------------------
class EncryptedText(TypeDecorator):
    """SQLAlchemy column type that transparently encrypts/decrypts text via Fernet."""
    impl = Text
    cache_ok = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        key = os.environ.get('CHAT_ENCRYPTION_KEY', '').strip().encode()
        if key:
            try:
                self.cipher = Fernet(key)
            except Exception:
                self.cipher = None
        else:
            self.cipher = None

    def process_bind_param(self, value, dialect):
        if value is None or self.cipher is None:
            return value
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        return self.cipher.encrypt(value.encode('utf-8')).decode('utf-8')

    def process_result_value(self, value, dialect):
        if value is None or self.cipher is None:
            return value
        try:
            return self.cipher.decrypt(value.encode('utf-8')).decode('utf-8')
        except Exception:
            # Allow reading unencrypted legacy rows during migration
            return value


# Chat
# ---------------------------------------------------------------------------

class ChatMessage(Base):
    """Persisted chat messages for the global room and AI system chat."""
    __tablename__ = 'chat_messages'

    id          = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp   = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id     = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    sender_type = Column(String(20), nullable=False, default='user')   # user | ai | system
    content     = Column(EncryptedText, nullable=False)
    room        = Column(String(100), nullable=False, default='global', index=True)

    sender = relationship('User', foreign_keys=[user_id], back_populates='chat_messages')


class PasskeyCredential(Base):
    __tablename__ = 'passkey_credentials'

    id              = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    credential_id   = Column(Text, unique=True, nullable=False)
    public_key      = Column(Text, nullable=False)
    sign_count      = Column(Integer, default=0)
    device_name     = Column(String(255))
    created_at      = Column(DateTime, default=datetime.utcnow)
    last_login_at   = Column(DateTime, nullable=True)
    last_login_ip   = Column(String(64), nullable=True)
    last_user_agent = Column(String(512), nullable=True)

    user          = relationship('User', back_populates='credentials')


class AuthChallenge(Base):
    __tablename__ = 'auth_challenges'

    user_id          = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    challenge_string = Column(Text, nullable=False)
    expires_at       = Column(DateTime, nullable=False)

    user             = relationship('User', back_populates='challenge')


class PendingQRSession(Base):
    __tablename__ = 'pending_qr_sessions'

    session_id      = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    status          = Column(String(50), default='pending')
    auth_token      = Column(Text)   # SHA-256 hash of the poll_token secret
    verified_jwt    = Column(Text)   # final JWT stored temporarily until desktop polls
    next_step_token = Column(Text)   # JWT for phone to use during first-time registration
    expires_at      = Column(DateTime, nullable=False)
    pin_hash        = Column(Text, nullable=True)   # SHA-256 of 6-digit PIN shown on desktop
    desktop_ip      = Column(String(64), nullable=True)  # IP that created the QR session

    user         = relationship('User', back_populates='qr_sessions')


# Get password from environment variable or use default for local development
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme")

# First, connect to default 'postgres' database to create sdr_db if needed
from sqlalchemy import text
admin_engine = create_engine(f"postgresql://postgres:{DB_PASSWORD}@localhost:5432/postgres")
with admin_engine.connect() as conn:
    conn.execute(text("commit"))  # close the implicit transaction
    result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'sdr_db'"))
    if not result.fetchone():
        conn.execute(text("CREATE DATABASE sdr_db"))
        print("[models] Created database 'sdr_db'")
    else:
        print("[models] Database 'sdr_db' already exists")
admin_engine.dispose()

# Now connect to sdr_db
SQLALCHEMY_DATABASE_URL = f"postgresql://postgres:{DB_PASSWORD}@localhost:5432/sdr_db"
DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '100'))
DB_MAX_OVERFLOW = int(os.environ.get('DB_MAX_OVERFLOW', '100'))
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)
print("[models] Tables created (if not existing)")
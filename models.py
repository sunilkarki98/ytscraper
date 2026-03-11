"""
SQLAlchemy models for the SaaS platform.
Tables: User, Job, Result, Usage
"""
import enum
import uuid
import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text, Index
)
from sqlalchemy.orm import relationship
from database import Base


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.datetime.now(datetime.UTC)


class JobStatus(str, enum.Enum):
    """Typed job statuses — prevents typos and enables IDE autocomplete."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True, default="")
    name = Column(String(100), default="")
    created_at = Column(DateTime(timezone=True), default=_now)

    # Credits & plan
    free_credits = Column(Integer, default=500)          # 500 free emails
    paid_credits = Column(Integer, default=0)            # purchased credits
    has_db_addon = Column(Boolean, default=False)        # $5/mo database add-on
    db_addon_expires = Column(DateTime(timezone=True), nullable=True)   # when add-on expires

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)

    # Relationships
    jobs = relationship("Job", back_populates="user", lazy="dynamic")
    usage_records = relationship("Usage", back_populates="user", lazy="dynamic")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    keyword = Column(String(255), nullable=False)
    status = Column(String(20), default=JobStatus.QUEUED)
    email_count = Column(Integer, default=0)

    # Config
    max_emails = Column(Integer, default=500)
    country = Column(String(5), default="US")
    language = Column(String(5), default="en")
    filters_json = Column(Text, default="{}")
    min_subscribers = Column(Integer, default=0)
    max_subscribers = Column(Integer, default=0)
    timeout_minutes = Column(Integer, default=30)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Stats
    channels_scanned = Column(Integer, default=0)
    stats_json = Column(Text, default="{}")

    # Queue position (for free users)
    queue_position = Column(Integer, nullable=True)

    # Retention
    expires_at = Column(DateTime(timezone=True), nullable=True)  # 7 days for free users, null for paid DB

    # Relationships
    user = relationship("User", back_populates="jobs")
    results = relationship("Result", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_expires", "expires_at"),
        Index("ix_jobs_user_status", "user_id", "status"),
    )


class Result(Base):
    __tablename__ = "results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    # Email data
    email = Column(String(255), nullable=False)
    channel_name = Column(String(255), default="")
    channel_url = Column(String(500), default="")
    channel_id = Column(String(50), default="")
    subscribers = Column(Integer, default=0)
    source = Column(String(20), default="youtube")
    extracted_at = Column(DateTime(timezone=True), default=_now)
    search_keyword = Column(String(255), default="")

    # Social links
    instagram = Column(String(500), default="")
    twitter = Column(String(500), default="")
    tiktok = Column(String(500), default="")
    facebook = Column(String(500), default="")
    linkedin = Column(String(500), default="")
    website = Column(String(500), default="")

    # Relationships
    job = relationship("Job", back_populates="results")

    def to_dict(self) -> dict:
        """Single source of truth for Result → API dict serialization."""
        return {
            "email": self.email,
            "channelName": self.channel_name,
            "channelUrl": self.channel_url,
            "channelId": self.channel_id,
            "subscribers": self.subscribers,
            "instagram": self.instagram,
            "twitter": self.twitter,
            "tiktok": self.tiktok,
            "facebook": self.facebook,
            "linkedin": self.linkedin,
            "website": self.website,
            "searchKeyword": self.search_keyword,
        }

    __table_args__ = (
        Index("ix_results_user_email", "user_id", "email"),  # dedup per user
        Index("ix_results_job_id_sorted", "job_id", "id"),   # fast paginated queries
    )


class Usage(Base):
    __tablename__ = "usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    month = Column(String(7), nullable=False)  # "2025-02"
    emails_scraped = Column(Integer, default=0)
    jobs_run = Column(Integer, default=0)
    credits_used = Column(Integer, default=0)  # free + paid

    user = relationship("User", back_populates="usage_records")

    __table_args__ = (
        Index("ix_usage_user_month", "user_id", "month", unique=True),
    )

"""SQLAlchemy models for the Eliza AI access-control system."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Text, Float,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from app.db import Base


def _now():
    return datetime.now(timezone.utc)


def _new_uuid():
    return str(uuid.uuid4())


class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True, default=_new_uuid)
    normalized_email = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_activity_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_device_type = Column(String, default="")
    status = Column(String, default="active", index=True)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    access_periods = relationship("AccessPeriod", back_populates="customer", cascade="all, delete-orphan")
    question_usages = relationship("QuestionUsage", back_populates="customer", cascade="all, delete-orphan")
    feedbacks = relationship("Feedback", back_populates="customer", cascade="all, delete-orphan")
    visits = relationship("Visit", back_populates="customer", cascade="all, delete-orphan")


class AccessPeriod(Base):
    __tablename__ = "access_periods"

    id = Column(String, primary_key=True, default=_new_uuid)
    customer_id = Column(String, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    period_started_at = Column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    period_expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    first_question_at = Column(DateTime(timezone=True), nullable=True)
    blocked_at = Column(DateTime(timezone=True), nullable=True)
    blocked_until = Column(DateTime(timezone=True), nullable=True, index=True)
    questions_used = Column(Integer, default=0, nullable=False)
    status = Column(String, default="active", index=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    customer = relationship("Customer", back_populates="access_periods")
    question_usages = relationship("QuestionUsage", back_populates="access_period", cascade="all, delete-orphan")
    feedbacks = relationship("Feedback", back_populates="access_period", cascade="all, delete-orphan")
    visits = relationship("Visit", back_populates="access_period", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_access_periods_customer_status", "customer_id", "status"),
    )


class QuestionUsage(Base):
    __tablename__ = "question_usages"

    id = Column(String, primary_key=True, default=_new_uuid)
    customer_id = Column(String, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    access_period_id = Column(String, ForeignKey("access_periods.id", ondelete="CASCADE"), nullable=False, index=True)
    request_id = Column(String, unique=True, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    status = Column(String, default="accepted", index=True)
    device_type = Column(String, default="")
    session_id = Column(String, default="", index=True)

    customer = relationship("Customer", back_populates="question_usages")
    access_period = relationship("AccessPeriod", back_populates="question_usages")


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(String, primary_key=True, default=_new_uuid)
    customer_id = Column(String, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    access_period_id = Column(String, ForeignKey("access_periods.id", ondelete="CASCADE"), nullable=False, index=True)
    rating = Column(Integer, nullable=False)
    category = Column(String, nullable=False, index=True)
    message = Column(Text, nullable=False)
    device_type = Column(String, default="")
    browser_name = Column(String, default="")
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    status = Column(String, default="new", index=True)
    admin_notes = Column(Text, default="", nullable=True)

    customer = relationship("Customer", back_populates="feedbacks")
    access_period = relationship("AccessPeriod", back_populates="feedbacks")


class Visit(Base):
    __tablename__ = "visits"

    id = Column(String, primary_key=True, default=_new_uuid)
    customer_id = Column(String, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    access_period_id = Column(String, ForeignKey("access_periods.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String, default="visit", index=True)
    visit_date = Column(String, default=lambda: _now().strftime("%Y-%m-%d"), index=True)
    device_category = Column(String, default="unknown")
    browser_family = Column(String, default="unknown")
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    customer = relationship("Customer", back_populates="visits")
    access_period = relationship("AccessPeriod", back_populates="visits")

    __table_args__ = (
        UniqueConstraint("customer_id", "visit_date", "access_period_id", name="uq_visit_customer_date_period"),
    )


class Admin(Base):
    __tablename__ = "admins"

    id = Column(String, primary_key=True, default=_new_uuid)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
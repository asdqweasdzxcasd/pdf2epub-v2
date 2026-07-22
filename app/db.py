"""SQLAlchemy 엔진/세션 + Job 모델

MVP 단계라 alembic 없이 create_all로 단순 마이그레이션.
"""

import logging
from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    upload_path: Mapped[str] = mapped_column(String(512), nullable=False)
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 동시 1건 제한용 클라이언트 식별자 (IP + 세션 쿠키)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_session: Mapped[str | None] = mapped_column(String(64), nullable=True)


_engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def init_db() -> None:
    """앱 부팅 시 호출. 테이블이 없으면 생성하고, 누락된 컬럼은 보강한다."""
    Base.metadata.create_all(_engine)
    # 기존 jobs 테이블에 신규 컬럼 idempotent하게 추가 (PostgreSQL 9.6+)
    with _engine.begin() as conn:
        for col_def in (
            "ADD COLUMN IF NOT EXISTS client_ip VARCHAR(64)",
            "ADD COLUMN IF NOT EXISTS client_session VARCHAR(64)",
        ):
            try:
                conn.execute(text(f"ALTER TABLE jobs {col_def}"))
            except Exception:
                logger.exception("ALTER TABLE jobs %s 실패", col_def)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

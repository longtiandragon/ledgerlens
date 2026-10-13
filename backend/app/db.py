from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import JSON, String, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Entity(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Book(Entity):
    __tablename__ = 'books'
    name: Mapped[str]
    framework: Mapped[str]
    currency: Mapped[str] = mapped_column(default='CNY')
    period: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
    revision: Mapped[int] = mapped_column(default=0)
    accounts: Mapped[list] = mapped_column(JSON)
    locked_periods: Mapped[list] = mapped_column(JSON, default=list)


class Document(Entity):
    __tablename__ = 'documents'
    book_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    sha256: Mapped[str]
    storage_path: Mapped[str]
    synthetic: Mapped[bool] = mapped_column(default=False)
    media_type: Mapped[str] = mapped_column(default='application/octet-stream')
    extracted: Mapped[list] = mapped_column(JSON, default=list)


class ImportBatch(Entity):
    __tablename__ = 'imports'
    book_id: Mapped[str] = mapped_column(index=True)
    document_id: Mapped[str]
    status: Mapped[str] = mapped_column(default='preview')
    rows: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    columns: Mapped[list] = mapped_column(JSON, default=list)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_id: Mapped[str | None]
    imported: Mapped[int] = mapped_column(default=0)
    duplicate_count: Mapped[int] = mapped_column(default=0)


class OcrRun(Entity):
    __tablename__ = 'ocr_runs'
    book_id: Mapped[str] = mapped_column(index=True)
    document_id: Mapped[str]
    status: Mapped[str] = mapped_column(default='queued')
    worker_token: Mapped[str | None]
    book_version: Mapped[int]
    period: Mapped[str]
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    result_digest: Mapped[str | None]
    error: Mapped[str | None]


class OcrReview(Entity):
    __tablename__ = 'ocr_reviews'
    __table_args__ = (UniqueConstraint('run_id', 'version'), UniqueConstraint('parent_id'),)
    book_id: Mapped[str] = mapped_column(index=True)
    run_id: Mapped[str] = mapped_column(index=True)
    version: Mapped[int]
    parent_id: Mapped[str | None]
    book_revision: Mapped[int]
    book_version: Mapped[int]
    payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str]
    import_id: Mapped[str] = mapped_column(unique=True)


class ReferenceSearch(Entity):
    __tablename__ = 'reference_searches'
    book_id: Mapped[str] = mapped_column(index=True)
    book_version: Mapped[int]
    book_revision: Mapped[int]
    framework: Mapped[str]
    period: Mapped[str]
    query: Mapped[str]
    registry_key: Mapped[str]
    manifest: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default='queued')
    worker_token: Mapped[str | None]
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    result_digest: Mapped[str | None]
    error: Mapped[str | None]


class ReferenceChoice(Entity):
    __tablename__ = 'reference_choices'
    __table_args__ = (UniqueConstraint('book_id', 'version'), UniqueConstraint('parent_id'),)
    book_id: Mapped[str] = mapped_column(index=True)
    book_version: Mapped[int]
    version: Mapped[int]
    parent_id: Mapped[str | None]
    payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str]
    snapshot_id: Mapped[str | None]


class Record(Entity):
    __tablename__ = 'records'
    __table_args__ = (UniqueConstraint('book_id', 'kind', 'source_id'),)
    book_id: Mapped[str] = mapped_column(index=True)
    document_id: Mapped[str]
    source_id: Mapped[str]
    kind: Mapped[str]
    evidence_id: Mapped[str] = mapped_column(unique=True)
    fingerprint: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON)
    locator: Mapped[dict] = mapped_column(JSON)


class Snapshot(Entity):
    __tablename__ = 'snapshots'
    book_id: Mapped[str] = mapped_column(index=True)
    revision: Mapped[int]
    policy: Mapped[dict] = mapped_column(JSON)
    record_ids: Mapped[list] = mapped_column(JSON)
    digest: Mapped[str]


class CheckRun(Entity):
    __tablename__ = 'check_runs'
    __table_args__ = (UniqueConstraint('snapshot_id', 'rule_version'),)
    book_id: Mapped[str]
    snapshot_id: Mapped[str]
    rule_version: Mapped[str] = mapped_column(default='0.1.0')
    status: Mapped[str] = mapped_column(default='complete')


class Case(Entity):
    __tablename__ = 'cases'
    book_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str]
    check_run_id: Mapped[str]
    finding: Mapped[dict] = mapped_column(JSON)


class Run(Entity):
    __tablename__ = 'agent_runs'
    case_id: Mapped[str] = mapped_column(index=True)
    mode: Mapped[str]
    status: Mapped[str] = mapped_column(default='queued')
    active_slot: Mapped[str | None] = mapped_column(unique=True)
    worker_token: Mapped[str | None]
    heartbeat: Mapped[str | None]
    steps: Mapped[list] = mapped_column(JSON, default=list)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None]


class Simulation(Entity):
    __tablename__ = 'simulations'
    case_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str]
    adjustments: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)


class Review(Entity):
    __tablename__ = 'reviews'
    case_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str]
    decision: Mapped[str]
    comment: Mapped[str]


class Audit(Entity):
    __tablename__ = 'audit_events'
    book_id: Mapped[str] = mapped_column(index=True)
    action: Mapped[str]
    details: Mapped[dict] = mapped_column(JSON)


class Evaluation(Entity):
    __tablename__ = 'evaluations'
    result: Mapped[dict] = mapped_column(JSON)


class PolicyObservationVersion(Entity):
    __tablename__ = 'policy_observation_versions'
    __table_args__ = (UniqueConstraint('book_id', 'snapshot_id', 'logical_key', 'version'),
                     UniqueConstraint('parent_version_id'))
    book_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str] = mapped_column(index=True)
    logical_key: Mapped[str]
    version: Mapped[int]
    parent_version_id: Mapped[str | None]
    payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str]
    comment: Mapped[str]


class PolicyCheckRun(Entity):
    __tablename__ = 'policy_check_runs'
    __table_args__ = (UniqueConstraint('observation_id', 'pack_key'),)
    book_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str]
    observation_id: Mapped[str] = mapped_column(index=True)
    pack_key: Mapped[str]
    observation_digest: Mapped[str]
    artifact_digest: Mapped[str]
    binding: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)


class InvestigationPolicyContext(Entity):
    __tablename__ = 'investigation_policy_contexts'
    __table_args__ = (UniqueConstraint('run_id'),)
    run_id: Mapped[str]
    book_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str]


class EvidenceRequest(Entity):
    """An immutable request for evidence; status comes only from appended events."""
    __tablename__ = 'evidence_requests'
    book_id: Mapped[str] = mapped_column(index=True)
    case_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str]
    kind: Mapped[str]
    reference: Mapped[str | None]
    description: Mapped[str]
    origin: Mapped[str]


class EvidenceRequestEvent(Entity):
    __tablename__ = 'evidence_request_events'
    request_id: Mapped[str] = mapped_column(index=True)
    action: Mapped[str]
    snapshot_id: Mapped[str | None]
    record_ids: Mapped[list] = mapped_column(JSON, default=list)
    comment: Mapped[str]


engine = create_engine(settings.database_url, connect_args={'check_same_thread': False, 'timeout': 30}
                       if settings.database_url.startswith('sqlite') else {}, pool_pre_ping=True)
if settings.database_url.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def sqlite_pragmas(connection, _):
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA foreign_keys=ON')

Session = sessionmaker(engine, expire_on_commit=False)

# LangGraph's PostgreSQL checkpointer creates and migrates these itself through
# saver.setup(); Alembic must neither report nor autogenerate drops for them.
LANGGRAPH_TABLES = frozenset({'checkpoints', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations'})


def alembic_include_object(obj, name, type_, reflected, compare_to):
    table = name if type_ == 'table' else getattr(getattr(obj, 'table', None), 'name', None)
    return not (reflected and compare_to is None and table in LANGGRAPH_TABLES)


def session():
    with Session() as s:
        yield s


def serialize(obj):
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}

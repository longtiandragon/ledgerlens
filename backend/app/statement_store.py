"""Append-only public-report observations, separate from accounting books.

The probe and acquisition manifest are preserved as input bytes. Projection and
arithmetic are checked on every load; PDF bytes and locators are checked before
import and whenever evidence or the original file is requested. This verifies
local consistency, not financial truth or publisher authentication.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, field_validator

VERSION = 'statement-snapshot-store-v1'
PROBE_VERSION = 'annual-statement-readonly-probe-v1'
SNAPSHOT_ID = re.compile(r'statement_snapshot_[0-9a-f]{64}')
REPORT_ID = re.compile(r'annual-\d{6}-20\d{2}')
MAX_JSON_BYTES = 16_000_000
MAX_PDF_BYTES = 32_000_000
METRICS = ('total_assets', 'total_liabilities', 'total_equity')
SHA = r'^[0-9a-f]{64}$'


class StatementModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class TextLine(StatementModel):
    line: int = Field(ge=1)
    text: str = Field(max_length=10000)
    bbox: list[float] = Field(min_length=4, max_length=4)

    @field_validator('bbox')
    @classmethod
    def geometry(cls, value):
        if any(not Decimal(str(n)).is_finite() for n in value) or value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError('Invalid evidence geometry')
        return value


class SourceLine(TextLine):
    pdf_page: int = Field(ge=1, le=5000)


class StatementContext(StatementModel):
    statement_title: SourceLine
    statement_date: SourceLine
    unit: Literal['元', '千元']
    currency: Literal['CNY']
    unit_factor_cny: Literal['1', '1000']
    unit_source: SourceLine
    section_stop: SourceLine


class ColumnHeader(StatementModel):
    pdf_page: int = Field(ge=1, le=5000)
    cells: list[str | None] = Field(min_length=4, max_length=4)
    current_column_zero_based: int = Field(ge=2, le=3)
    previous_column_zero_based: int = Field(ge=2, le=3)
    current_column_bbox: list[float] = Field(min_length=4, max_length=4)
    previous_column_bbox: list[float] = Field(min_length=4, max_length=4)
    table_header_bbox: list[float] = Field(min_length=4, max_length=4)

    @field_validator('current_column_bbox', 'previous_column_bbox', 'table_header_bbox')
    @classmethod
    def geometry(cls, value):
        return TextLine.geometry(value)


class LabelFragment(StatementModel):
    table_row: int = Field(ge=1)
    table_column_zero_based: int = Field(ge=0)
    text: str
    bbox: list[float] = Field(min_length=4, max_length=4)

    @field_validator('bbox')
    @classmethod
    def geometry(cls, value):
        return TextLine.geometry(value)


class StatementObservation(StatementModel):
    evidence_id: str = Field(pattern=r'^statement_evidence_[0-9a-f]{64}$')
    metric: Literal['total_assets', 'total_liabilities', 'total_equity']
    amount_reported: str
    previous_amount_reported: str
    normalized_cny: str
    unit: Literal['元', '千元']
    currency: Literal['CNY']
    pdf_page: int = Field(ge=1, le=5000)
    table: int = Field(ge=1)
    table_row: int = Field(ge=1)
    current_table_column_zero_based: int = Field(ge=0)
    previous_table_column_zero_based: int = Field(ge=0)
    current_cell: str
    previous_cell: str
    label_cell: str
    current_cell_bbox: list[float] = Field(min_length=4, max_length=4)
    previous_cell_bbox: list[float] = Field(min_length=4, max_length=4)
    text_lines: list[TextLine] = Field(min_length=1, max_length=20)
    label_fragments: list[LabelFragment] | None = Field(default=None, min_length=2, max_length=2)
    table_rows: list[int] | None = Field(default=None, min_length=2, max_length=2)
    semantic_status: Literal['unverified_extraction'] = 'unverified_extraction'

    @field_validator('amount_reported', 'previous_amount_reported', 'normalized_cny')
    @classmethod
    def money(cls, value):
        if len(value) > 100 or re.fullmatch(r'-?\d+(?:\.\d+)?', value) is None:
            raise ValueError('Money must be a finite decimal string')
        return value

    @field_validator('current_cell_bbox', 'previous_cell_bbox')
    @classmethod
    def geometry(cls, value):
        return TextLine.geometry(value)


class BalanceObservation(StatementModel):
    difference_reported_unit: str
    equal_as_reported: bool


class StatementReport(StatementModel):
    report_id: str = Field(pattern=r'^annual-\d{6}-20\d{2}$')
    company_code: str = Field(pattern=r'^\d{6}$')
    company_name: str = Field(min_length=1, max_length=200)
    report_year: int = Field(ge=2000, le=2099)
    status: Literal['observed', 'rejected']
    error_code: str | None
    raw_sha256: str = Field(pattern=SHA)
    url: str
    source_license: Literal['unspecified'] = 'unspecified'
    observations: list[StatementObservation]
    balance_check: BalanceObservation | None


class StatementDataset(StatementModel):
    dataset_scope_id: str = Field(pattern=r'^annual_reports_[0-9a-f]{64}$')
    snapshot_id: str = Field(pattern=r'^statement_snapshot_[0-9a-f]{64}$')
    title: str
    observed_at: datetime
    report_count: int = Field(ge=1, le=300)
    observed_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    parser_source_sha256: str = Field(pattern=SHA)
    scope: Literal['public_consolidated_balance_sheet'] = 'public_consolidated_balance_sheet'
    read_only: Literal[True] = True


class StatementSnapshot(StatementModel):
    dataset: StatementDataset
    reports: list[StatementReport]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def safe_path(root: Path, *parts: str) -> Path:
    resolved = root.resolve()
    path = resolved.joinpath(*parts)
    if not path.resolve().is_relative_to(resolved):
        raise ValueError('Statement path escapes configured storage')
    return path


def read_bounded(path: Path, limit: int = MAX_JSON_BYTES) -> bytes:
    if path.stat().st_size > limit:
        raise ValueError('Statement source exceeds read budget')
    with path.open('rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Statement source exceeds read budget')
    return data


def read_json(data: bytes) -> dict:
    if len(data) > MAX_JSON_BYTES:
        raise ValueError('Statement JSON exceeds read budget')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if not isinstance(value, dict):
        raise ValueError('Statement JSON root must be an object')
    return value


def _cell_number(value: str) -> Decimal:
    compact = re.sub(r'\s+', '', value).replace('−', '-').replace('－', '-').replace('（', '(').replace('）', ')')
    if re.fullmatch(r'(?:-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\))', compact) is None:
        raise ValueError('Malformed source amount')
    if compact.startswith('('):
        compact = '-' + compact[1:-1]
    return Decimal(compact.replace(',', ''))


def _compact(value: str) -> str:
    return re.sub(r'\s+', '', value)


def validated_context(row: dict) -> tuple[StatementContext, ColumnHeader]:
    context = StatementContext.model_validate(row['context'])
    header = ColumnHeader.model_validate(row['column_header'])
    year = row['report_year']
    if re.fullmatch(r'(?:\d+[、.．])?合并资产负债表(?:[（(]续[）)])?', _compact(context.statement_title.text)) is None:
        raise ValueError('Statement is not explicitly consolidated')
    if _compact(context.statement_date.text) != f'{year}年12月31日':
        raise ValueError('Statement date differs from report year')
    unit_text = _compact(context.unit_source.text)
    currency_stated = re.search(r'币种[:：]人民币(?:$|[）)])', unit_text) or re.search(
        r'(?:单位[:：]|金额单位(?:均)?为)人民币(?:千元|元)(?=$|[）)])', unit_text)
    if not currency_stated or re.search(
            r'(?:单位[:：]|金额单位(?:均)?为)(?:人民币)?' + context.unit + r'(?=$|[）)])',
            unit_text.replace('币种:人民币', '').replace('币种：人民币', '')) is None:
        raise ValueError('Currency or unit is not stated in the cited source')
    if context.unit_factor_cny != ('1000' if context.unit == '千元' else '1'):
        raise ValueError('Statement unit factor differs from literal unit')
    current, previous = header.current_column_zero_based, header.previous_column_zero_based
    if current == previous or (_compact(header.cells[current] or '') not in (f'{year}年12月31日', '期末余额') or
                               _compact(header.cells[previous] or '') not in (f'{year - 1}年12月31日', '上年年末余额')):
        raise ValueError('Current and previous column semantics are unconfirmed')
    return context, header


def project(probe_bytes: bytes, coverage_bytes: bytes, *, observed_at: datetime) -> StatementSnapshot:
    """Validate an observation artifact; no hidden answers or runtime rules."""
    probe, coverage = read_json(probe_bytes), read_json(coverage_bytes)
    coverage_hash, probe_hash = digest(coverage_bytes), digest(probe_bytes)
    if probe.get('schema_version') != PROBE_VERSION or probe.get('coverage_manifest_sha256') != coverage_hash:
        raise ValueError('Unsupported or unbound statement probe')
    for flag in ('runtime_agent_used', 'ocr_used', 'rule_coverage_evidence', 'financial_accuracy_benchmark'):
        if probe.get(flag) is not False:
            raise ValueError('Probe cannot assert unsupported capabilities')
    if type(probe.get('network_or_model_calls')) is not int or probe['network_or_model_calls'] != 0:
        raise ValueError('Unexpected model activity')
    sources = {r['id']: r for r in coverage['reports']}
    if len(sources) != len(coverage['reports']):
        raise ValueError('Duplicate source report identity')
    requested = probe['requested_ids']
    rows = probe['reports']
    if not 1 <= len(rows) <= 300 or len(set(requested)) != len(requested) or [r['id'] for r in rows] != requested:
        raise ValueError('Probe report membership is inconsistent')
    if observed_at.tzinfo is None:
        raise ValueError('Snapshot observation time must contain timezone')
    snapshot_id = 'statement_snapshot_' + digest(canonical([VERSION, coverage_hash, probe_hash]))
    reports = []
    for row in rows:
        source = sources.get(row['id'])
        if source is None or source.get('status') != 'verified_full_report' or source.get('validation', {}).get('valid') is not True:
            raise ValueError('Report source is not a verified acquisition')
        code, year, sha = source['company_code'], source['report_year'], source['sha256']
        if row['id'] != f'annual-{code}-{year}' or not REPORT_ID.fullmatch(row['id']):
            raise ValueError('Report identity does not match company/year')
        for key, expected in (('raw_sha256', sha), ('raw_bytes', source['bytes']), ('company_code', code),
                              ('company_name', source['company_name']), ('report_year', year), ('url', source['url']),
                              ('local_relative_path', source['local_relative_path']), ('coverage_manifest_sha256', coverage_hash)):
            if row.get(key) != expected:
                raise ValueError('Report projection differs from acquisition source')
        if source['local_relative_path'] != f'raw/{code}/{year}/{sha}.pdf':
            raise ValueError('Unexpected report source path')
        address = urlsplit(source['url'])
        if address.scheme != 'https' or address.hostname != 'static.cninfo.com.cn' or address.username or address.password:
            raise ValueError('Unsupported public report origin')
        if row.get('independent_ground_truth_checked') is not False:
            raise ValueError('Independent evaluation truth must not enter runtime observations')
        observations, balance = [], None
        if row['status'] == 'observed':
            if set(row['metrics']) != set(METRICS) or row.get('error_code') is not None:
                raise ValueError('Observed report must contain exactly three totals')
            context, header = validated_context(row)
            for metric in METRICS:
                raw = row['metrics'][metric]
                selected = {name: raw[name] for name in StatementObservation.model_fields
                            if name not in ('evidence_id', 'metric', 'semantic_status') and name in raw}
                observation = StatementObservation(
                    evidence_id='statement_evidence_' + digest(canonical([snapshot_id, row['id'], metric])),
                    metric=metric, **selected)
                if observation.currency != context.currency or observation.unit != context.unit:
                    raise ValueError('Metric unit differs from statement')
                amount, prior = _cell_number(observation.current_cell), _cell_number(observation.previous_cell)
                if amount != Decimal(observation.amount_reported) or prior != Decimal(observation.previous_amount_reported):
                    raise ValueError('Amount differs from literal source cell')
                normalized = amount * Decimal(context.unit_factor_cny)
                if normalized != normalized.quantize(Decimal('.01')) or observation.normalized_cny != format(normalized, '.2f'):
                    raise ValueError('Normalized amount or precision is inconsistent')
                if observation.current_table_column_zero_based == observation.previous_table_column_zero_based:
                    raise ValueError('Current and previous amounts use the same column')
                if observation.pdf_page == header.pdf_page:
                    for bbox, target in ((observation.current_cell_bbox, header.current_column_bbox),
                                         (observation.previous_cell_bbox, header.previous_column_bbox)):
                        if any(abs(bbox[axis] - target[axis]) > 3 for axis in (0, 2)):
                            raise ValueError('Amount column differs from current/previous header semantics')
                aliases = {'total_assets': {'资产总计'}, 'total_liabilities': {'负债合计'},
                           'total_equity': {'所有者权益合计', '所有者权益（或股东权益）合计', '股东权益合计'}}
                if _compact(observation.label_cell) not in aliases[metric]:
                    raise ValueError('Metric label differs from its semantic identity')
                if observation.label_fragments is None and observation.table_rows is not None:
                    raise ValueError('Row span without source label fragments')
                if observation.label_fragments is not None:
                    first, second = observation.label_fragments
                    if (observation.table_rows != [first.table_row, second.table_row] or
                            first.table_row != observation.table_row or second.table_row != first.table_row + 1 or
                            first.table_column_zero_based != second.table_column_zero_based or
                            _compact(first.text + second.text) != _compact(observation.label_cell) or
                            any(abs(first.bbox[axis] - second.bbox[axis]) > 1 for axis in (0, 2)) or
                            abs(first.bbox[3] - second.bbox[1]) > 1):
                        raise ValueError('Split label source fragments are not adjacent')
                observations.append(observation)
            difference = Decimal(observations[0].amount_reported) - Decimal(observations[1].amount_reported) - Decimal(observations[2].amount_reported)
            original_check = row['balance_check']
            if Decimal(original_check['difference_reported_unit']) != difference or original_check['equal_as_reported'] is not (difference == 0):
                raise ValueError('Arithmetic observation is inconsistent')
            balance = BalanceObservation(difference_reported_unit=str(difference), equal_as_reported=difference == 0)
        elif row['status'] != 'rejected' or row.get('metrics') != {} or not row.get('error_code'):
            raise ValueError('Rejected report must preserve failure without amounts')
        reports.append(StatementReport(report_id=row['id'], company_code=code, company_name=source['company_name'],
                       report_year=year, status=row['status'], error_code=row.get('error_code'), raw_sha256=sha,
                       url=source['url'], observations=observations, balance_check=balance))
    observed = sum(r.status == 'observed' for r in reports)
    for key, expected in (('reports_attempted', len(rows)), ('reports_observed', observed),
                          ('reports_rejected', len(rows) - observed), ('total_available_reports', len(sources)),
                          ('unprobed_reports', len(sources) - len(rows))):
        if type(probe.get(key)) is not int or probe[key] != expected:
            raise ValueError('Probe summary counts are inconsistent')
    return StatementSnapshot(dataset=StatementDataset(dataset_scope_id='annual_reports_' + coverage_hash,
                             snapshot_id=snapshot_id, title='公开年报 · 合并资产负债表观察', observed_at=observed_at,
                             report_count=len(reports), observed_count=observed, rejected_count=len(reports) - observed,
                             parser_source_sha256=probe['parser_source_sha256']), reports=reports)


def source_pdf(raw_root: Path, report: StatementReport, coverage: dict) -> bytes:
    source = next(r for r in coverage['reports'] if r['id'] == report.report_id)
    data = read_bounded(safe_path(raw_root, source['local_relative_path']), MAX_PDF_BYTES)
    if len(data) != source['bytes'] or digest(data) != report.raw_sha256 or not data.startswith(b'%PDF-'):
        raise ValueError('Original PDF bytes have changed')
    return data


def verify_locator(data: bytes, observation: StatementObservation, *, report_row: dict | None = None) -> dict:
    """Check cells, exact line numbers and PDF geometry, not financial validity."""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[observation.pdf_page - 1]
        table = page.find_tables()[observation.table - 1]
        row = table.extract()[observation.table_row - 1]
        geometry = table.rows[observation.table_row - 1]
        if observation.label_fragments:
            for fragment in observation.label_fragments:
                label_row = table.extract()[fragment.table_row - 1]
                label_geometry = table.rows[fragment.table_row - 1]
                if label_row[fragment.table_column_zero_based] != fragment.text or (
                        list(label_geometry.cells[fragment.table_column_zero_based] or []) != fragment.bbox):
                    raise ValueError('Split label locator does not match original')
        elif observation.label_cell not in row:
            raise ValueError('Metric label locator is not in its original source row')
        for index, text, bbox in ((observation.current_table_column_zero_based, observation.current_cell, observation.current_cell_bbox),
                                  (observation.previous_table_column_zero_based, observation.previous_cell, observation.previous_cell_bbox)):
            if row[index] != text or list(geometry.cells[index] or []) != bbox:
                raise ValueError('PDF cell locator does not match original')
        lines = page.extract_text_lines()
        for saved in observation.text_lines:
            actual = lines[saved.line - 1]
            if actual['text'] != saved.text or [actual['x0'], actual['top'], actual['x1'], actual['bottom']] != saved.bbox:
                raise ValueError('PDF text line locator does not match original')
        if report_row is not None:
            context, header = validated_context(report_row)
            raw_metric = report_row['metrics'][observation.metric]
            header_page = raw_metric.get('column_header_pdf_page', header.pdf_page)
            start, stop = context.statement_title, context.section_stop
            if not start.pdf_page <= header_page <= observation.pdf_page <= stop.pdf_page:
                raise ValueError('Amount or header locator is outside the scoped statement')
            lower = start.bbox[1] if observation.pdf_page == start.pdf_page else 0
            upper = stop.bbox[1] if observation.pdf_page == stop.pdf_page else page.height
            if geometry.bbox[1] < lower or geometry.bbox[3] > upper:
                raise ValueError('Amount row locator crosses the statement boundary')
            year = report_row['report_year']
            current_name = _compact(raw_metric.get('current_column_header') or header.cells[header.current_column_zero_based])
            previous_name = '上年年末余额' if current_name == '期末余额' else f'{year - 1}年12月31日'
            if current_name not in ('期末余额', f'{year}年12月31日'):
                raise ValueError('Current amount column period differs from the report year')
            bindings = []
            for candidate in pdf.pages[header_page - 1].find_tables():
                for index, cells in enumerate(candidate.extract()):
                    if len(cells) != 4 or _compact(cells[0] or '') not in ('项目', '资产', '负债和所有者权益'):
                        continue
                    normalized = [_compact(c or '') for c in cells]
                    if current_name not in normalized or previous_name not in normalized:
                        continue
                    current, previous = normalized.index(current_name), normalized.index(previous_name)
                    geometry = candidate.rows[index].cells
                    header_box = candidate.rows[index].bbox
                    lower = start.bbox[1] if header_page == start.pdf_page else 0
                    upper = stop.bbox[1] if header_page == stop.pdf_page else pdf.pages[header_page - 1].height
                    if header_box[1] < lower or header_box[3] > upper:
                        continue
                    if current < 2 or previous < 2 or current == previous or geometry[current] is None or geometry[previous] is None:
                        raise ValueError('Current/previous column locator has missing geometry')
                    bindings.append((geometry[current], geometry[previous]))
            if len(bindings) != 1:
                raise ValueError('Current/previous PDF column header locator is not unique')
            for bbox, target in zip((observation.current_cell_bbox, observation.previous_cell_bbox), bindings[0]):
                if any(abs(bbox[axis] - target[axis]) > 3 for axis in (0, 2)):
                    raise ValueError('Amount column locator disagrees with its original period header')
        return {'width': page.width, 'height': page.height, 'rotation': page.rotation}


def verify_context(data: bytes, row: dict) -> tuple[StatementContext, ColumnHeader]:
    context, header = validated_context(row)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for saved in (context.statement_title, context.statement_date, context.unit_source, context.section_stop):
            actual = pdf.pages[saved.pdf_page - 1].extract_text_lines()[saved.line - 1]
            if actual['text'] != saved.text or [actual['x0'], actual['top'], actual['x1'], actual['bottom']] != saved.bbox:
                raise ValueError('Statement context locator does not match original')
        found = False
        for table in pdf.pages[header.pdf_page - 1].find_tables():
            for index, cells in enumerate(table.extract()):
                geometry = table.rows[index]
                if cells == header.cells and list(geometry.bbox) == header.table_header_bbox and (
                        list(geometry.cells[header.current_column_zero_based] or []) == header.current_column_bbox and
                        list(geometry.cells[header.previous_column_zero_based] or []) == header.previous_column_bbox):
                    found = True
        if not found:
            raise ValueError('Statement header locator does not match original')
    return context, header


def load_statement_snapshot(root: Path, snapshot_id: str) -> tuple[StatementSnapshot, dict, dict]:
    if not SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError('Invalid statement snapshot identifier')
    directory = safe_path(root, snapshot_id)
    manifest = read_json(read_bounded(safe_path(directory, 'manifest.json')))
    if manifest.get('schema_version') != VERSION or manifest.get('snapshot_id') != snapshot_id:
        raise ValueError('Statement manifest identity mismatch')
    inputs = {}
    for name in ('probe', 'coverage', 'snapshot'):
        data = read_bounded(safe_path(directory, name + '.json'))
        if digest(data) != manifest[name + '_sha256']:
            raise ValueError('Statement artifact bytes changed')
        inputs[name] = data
    timestamp = datetime.fromisoformat(manifest['observed_at'])
    expected = project(inputs['probe'], inputs['coverage'], observed_at=timestamp)
    stored = StatementSnapshot.model_validate_json(inputs['snapshot'])
    if stored != expected or expected.dataset.snapshot_id != snapshot_id:
        raise ValueError('Statement projection is inconsistent')
    return stored, read_json(inputs['probe']), read_json(inputs['coverage'])


def list_statement_snapshots(root: Path) -> list[StatementDataset]:
    if not root.exists():
        return []
    datasets = [load_statement_snapshot(root, path.name)[0].dataset for path in sorted(root.iterdir(), key=lambda p: p.name)
                if SNAPSHOT_ID.fullmatch(path.name)]
    return sorted(datasets, key=lambda d: (d.observed_at, d.snapshot_id), reverse=True)


def save_statement_snapshot(root: Path, probe_path: Path, coverage_path: Path, raw_root: Path) -> dict:
    probe_bytes, coverage_bytes = read_bounded(probe_path), read_bounded(coverage_path)
    snapshot = project(probe_bytes, coverage_bytes, observed_at=datetime.now(timezone.utc))
    destination = safe_path(root, snapshot.dataset.snapshot_id)
    coverage = read_json(coverage_bytes)
    probe = read_json(probe_bytes)
    for report in snapshot.reports:
        data = source_pdf(raw_root, report, coverage)
        if report.status == 'observed':
            verify_context(data, next(r for r in probe['reports'] if r['id'] == report.report_id))
        for observation in report.observations:
            verify_locator(data, observation, report_row=next(r for r in probe['reports'] if r['id'] == report.report_id))
    if destination.exists():
        existing = load_statement_snapshot(root, snapshot.dataset.snapshot_id)[0]
        return {'status': 'existing_verified', 'dataset': existing.dataset.model_dump(mode='json')}
    snapshot_bytes = canonical(snapshot.model_dump(mode='json'))
    root.mkdir(parents=True, exist_ok=True)
    staging = safe_path(root, '.pending-' + uuid4().hex)
    staging.mkdir()
    manifest = {'schema_version': VERSION, 'snapshot_id': snapshot.dataset.snapshot_id,
                'observed_at': snapshot.dataset.observed_at.isoformat(), 'probe_sha256': digest(probe_bytes),
                'coverage_sha256': digest(coverage_bytes), 'snapshot_sha256': digest(snapshot_bytes),
                'raw_source_checked_at_import': True, 'pdf_locators_checked_at_import': True,
                'financial_truth_verified': False}
    for name, data in (('probe', probe_bytes), ('coverage', coverage_bytes), ('snapshot', snapshot_bytes), ('manifest', canonical(manifest))):
        (staging / (name + '.json')).write_bytes(data)
    try:
        staging.rename(destination)
    except FileExistsError:
        existing = load_statement_snapshot(root, snapshot.dataset.snapshot_id)[0]
        return {'status': 'existing_verified', 'dataset': existing.dataset.model_dump(mode='json')}
    return {'status': 'created', 'dataset': snapshot.dataset.model_dump(mode='json')}

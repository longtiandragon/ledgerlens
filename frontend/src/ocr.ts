import type { OcrReviewInput, OcrBatchReviewInput, LedgerInput } from './generated/contracts'

export interface OcrTableCandidate {
  candidate_id: string; observation_ids: string[]; record: Partial<LedgerInput>
  field_evidence: Record<string, string[]>; issues: string[]; copyable: boolean
  cells: Record<string, { text: string; observation_ids: string[] }>
}
export interface OcrTables {
  schema_version: string; run_id: string; result_digest: string; page: number; status: string
  tables: { table_id: string; headers: { field: string; text: string; observation_id: string }[]; rows: OcrTableCandidate[] }[]
  unassigned: { observation_ids: string[]; reason: string }[]; issues: string[]
}

export interface OcrLine {
  observation_id: string; text: string; confidence: number
  locator: { page: number; line: number; polygon_px: number[][]; polygon_pdf_user_space?: number[][] }
}
export interface OcrPage {
  page: number; status: string; observations?: OcrLine[]; image?: { width: number; height: number }
  error?: { type: string; message: string }
}
export interface OcrRun {
  id: string; document_id: string; document_name: string; media_type: string; status: string
  result_digest: string; book_revision: number; stale: boolean; error?: string
  result: { observations?: OcrLine[]; image?: { width: number; height: number }; pages?: OcrPage[] }
  reviews: { id: string; version: number; created_at: string; import_id: string; import_status: string; payload: OcrReviewInput | OcrBatchReviewInput }[]
}

import type { OcrReviewInput } from './generated/contracts'

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
  reviews: { id: string; version: number; created_at: string; import_id: string; import_status: string; payload: OcrReviewInput }[]
}

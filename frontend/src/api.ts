import type { BookInput } from './generated/contracts'
export interface Book extends Required<BookInput> { id:string; version:number }
export interface Case { id:string; title:string; rule_id:string; severity:string; status:string; snapshot_id:string; book_id:string; explanation:string; evidence_ids:string[]; record_ids:string[]; missing_evidence:string[]; metrics:Record<string,unknown>; scenario?:string|null; investigations?:Run[]; runs?:Run[]; reviews?:ReviewRecord[]; stale?:boolean; lineage?:Lineage; open_items?:OpenItem[]; treatment?:{status:string;summary:string;digest:string}|null }
export interface OpenItem { kind:string; ref:string; text:string; case_id?:string; candidates?:number; rule_id?:string }
export interface ReviewRecord { id:string; created_at:string; decision:string; comment:string; snapshot_id:string; user_id?:string|null; simulation_id?:string|null; open_items?:OpenItem[]|null }
export interface LineageCase { case_id:string; snapshot_id:string; created_at:string; rule_id:string; title:string; status:string; runs:number; simulations:number; reviews:number; evidence_requests:number; match:string }
export interface Lineage { state:'current'|'continued'|'not_reproduced'|'not_rechecked'; prior:LineageCase[]; successors:LineageCase[]; next_check:{check_run_id:string;snapshot_id:string}|null }
export interface DraftLine { voucher_id:string; account:string; debit:string; credit:string; business_date:string; period:string; invoice_id:string; order_id:string; counterparty:string; description:string }
export interface TreatmentDraft { version:string; source_sha256:string; case_id:string; snapshot_id:string; rule_id:string; scenario:string|null; status:'proposed'|'needs_input'|'conditional'|'needs_evidence'|'not_applicable'; summary:string; reason:string; framework:string; framework_note:string; lines:DraftLine[]; line_basis:{source_record_ids:string[];input_required:string[]}[]; alternatives:{summary:string;voucher_id:string}[]; warnings:string[]; needs:string[]; evidence_kind:string|null; investigation:{run_id:string;mode:string;status:string;warning:string|null}|null; notice:string; digest:string }
export interface SimulationBasis { source:'treatment_draft'|'manual'; summary?:string; draft_status?:string; draft_digest?:string; edited?:boolean; completed_inputs?:{line:number;field:string;submitted:string}[]; edits?:Record<string,unknown>[]; investigation?:TreatmentDraft['investigation'] }
export interface Overview { book:Book; counts:{records:number; cases:number; documents:number; snapshots:number}; capabilities:{name:string; available:boolean; missing:string[]}[]; balances:Record<string,string>; latest_snapshot_id:string|null; cases:Case[] }
export interface Evidence { id:string; record_id:string; source_id:string; kind:string; locator:string|Record<string,unknown>; document_id:string; document_name:string; record:Record<string,unknown> }
export interface ImportPreview { id:string; status:string; rows:Record<string,unknown>[]; errors:{row:number; field:string; message:string}[]; columns:string[]; mapping:Record<string,string>; record_count:number; filename?:string }
export interface Claim {text:string;rule_id:string;evidence_ids:string[];calculation?:Record<string,unknown>;origin?:string;assessment?:string}
export interface PolicyInterpretation {text:string;policy_check_ids:string[];semantic_status?:string}
export interface Run { id:string; mode?:string; created_at?:string; status:string; steps:Record<string,unknown>[]; result?:{policy_interpretations?:PolicyInterpretation[];claims:Claim[];proposed_claims?:Claim[]; hypotheses:{explanation:string;status:string;evidence_ids?:string[];missing_evidence?:string[]}[]; missing_evidence:string[]; status:string;model?:string|null;prompt_version?:string;usage?:Record<string,number>}; error?:unknown; usage?:Record<string,unknown> }
export interface Simulation { id:string; case_id?:string; snapshot_id?:string; created_at?:string; before:Record<string,string>; after:Record<string,string>; delta:Record<string,string>; balanced:boolean; violations:unknown[]; policy_note:string; new_findings:Case[]; basis?:SimulationBasis|null; comparison?:{resolved:Case[];persisting:Case[];introduced:Case[];normal:Case[]}; target?:{rule_id:string;status:string;record_ids:string[]} }
export interface Evaluation { runs:Record<string,unknown>[]; limitations:string[]; dataset_summary?:Record<string,unknown> }
export interface SessionUser { id:string; username:string; display_name:string; roles:string[] }
export class ApiError extends Error { constructor(message:string,public status:number) { super(message) } }
// Multi-user sessions live in sessionStorage (this tab only); single-user mode never sets one.
const SESSION_KEY = 'ledgerlens.session'
export function sessionToken():string { try { return sessionStorage.getItem(SESSION_KEY) || '' } catch { return '' } }
export function setSessionToken(token:string) { try { if (token) sessionStorage.setItem(SESSION_KEY, token); else sessionStorage.removeItem(SESSION_KEY) } catch { /* storage unavailable: the session ends with this page */ } }
const authHeaders = ():Record<string,string> => { const token = sessionToken(); return token ? { Authorization: `Bearer ${token}` } : {} }
export async function api<T>(path:string, options:RequestInit = {}):Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 120000)
  try {
    const response = await fetch(`/api/v1${path}`, { ...options, signal: options.signal || controller.signal, headers: { ...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}), ...authHeaders(), ...options.headers } })
    if (!response.ok) {
      if (response.status === 401) window.dispatchEvent(new CustomEvent('ledgerlens:unauthorized'))
      const body = await response.json().catch(() => null)
      const detail = body?.detail ?? body?.message
      throw new ApiError(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `请求失败（${response.status}）`, response.status)
    }
    return response.status === 204 ? undefined as T : await response.json() as T
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw new Error('请求超时，后台任务可能仍在运行。请刷新任务状态。')
    throw error
  } finally { clearTimeout(timer) }
}
export const post = <T>(path:string, body?:unknown) => api<T>(path, {method:'POST',body:body === undefined ? undefined : JSON.stringify(body)})
export async function download(path:string,filename:string) {
  const response=await fetch(`/api/v1${path}`,{headers:authHeaders()})
  if(!response.ok) { const data=await response.json().catch(()=>null); throw new Error(data?.detail || `导出失败（${response.status}）`) }
  const url=URL.createObjectURL(await response.blob()); const anchor=document.createElement('a'); anchor.href=url; anchor.download=filename; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),1000)
}

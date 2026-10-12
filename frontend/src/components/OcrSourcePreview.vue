<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { OcrLine } from '../ocr'

GlobalWorkerOptions.workerSrc = workerUrl
const props = defineProps<{ documentId: string; mediaType: string; page: number; line?: OcrLine; image?: { width: number; height: number } }>()
const url = computed(() => `/api/v1/documents/${encodeURIComponent(props.documentId)}/file`)
const canvas = ref<HTMLCanvasElement>()
const size = ref({ width: 1, height: 1 })
const pdfPoints = ref<number[][]>([])
const busy = ref(false), error = ref('')
let loading: PDFDocumentLoadingTask | undefined, pdf: PDFDocumentProxy | undefined, task: RenderTask | undefined
let generation = 0, disposed = false
let toViewport: ((x: number, y: number) => number[]) | undefined
const isPdf = computed(() => props.mediaType === 'application/pdf')
const viewBox = computed(() => isPdf.value ? `0 0 ${size.value.width} ${size.value.height}` : `0 0 ${props.image?.width || 1} ${props.image?.height || 1}`)
const points = computed(() => {
  if (busy.value || error.value || props.line?.locator.page !== props.page) return ''
  return (isPdf.value ? pdfPoints.value : props.line.locator.polygon_px).map(p => p.join(',')).join(' ')
})
function locate() { pdfPoints.value = props.line?.locator.page === props.page && toViewport ? (props.line.locator.polygon_pdf_user_space || []).map(p => toViewport!(p[0]!, p[1]!)) : [] }
async function render() {
  if (!pdf || !canvas.value) return
  const current = ++generation
  busy.value = true; error.value = ''; toViewport = undefined; pdfPoints.value = []
  const previous = task; previous?.cancel(); await previous?.promise.catch(() => undefined)
  if (disposed || current !== generation) return
  try {
    const page = await pdf.getPage(props.page)
    if (disposed || current !== generation || !canvas.value) return
    const base = page.getViewport({ scale: 1 })
    const viewport = page.getViewport({ scale: Math.min(1.5, 1600 / Math.max(base.width, base.height)) })
    size.value = { width: viewport.width, height: viewport.height }
    canvas.value.width = Math.ceil(viewport.width); canvas.value.height = Math.ceil(viewport.height)
    task = page.render({ canvas: canvas.value, viewport }); await task.promise
    if (disposed || current !== generation) return
    toViewport = (x, y) => viewport.convertToViewportPoint(x, y); locate()
  } catch (cause) { if (!disposed && current === generation) error.value = String(cause) }
  finally { if (!disposed && current === generation) busy.value = false }
}
onMounted(async () => {
  if (!isPdf.value) return
  busy.value = true
  try {
    loading = getDocument({ url: url.value, enableXfa: false })
    pdf = await loading.promise
    if (!disposed) await render()
  } catch (cause) { if (!disposed) { error.value = String(cause); busy.value = false } }
})
watch(() => props.page, () => { void render() })
watch(() => props.line, locate)
onBeforeUnmount(() => { disposed = true; generation++; task?.cancel(); void loading?.destroy().catch(() => undefined) })
</script>

<template>
  <div class="ocr-source" :aria-busy="busy">
    <p v-if="busy" role="status">正在读取原件…</p>
    <p v-if="error" class="notice error" role="alert">原件预览失败：{{ error }}</p>
    <div class="source-surface" :style="isPdf ? { aspectRatio: `${size.width}/${size.height}` } : undefined">
      <canvas v-if="isPdf" ref="canvas" :aria-label="`扫描原件第 ${page} 页`"/>
      <img v-else :src="url" alt="待人工核对的扫描原件" @error="error='无法读取已校验的原始图像'"/>
      <svg v-if="points" :viewBox="viewBox" aria-label="所选原文行定位框" preserveAspectRatio="none"><polygon :points="points"/></svg>
    </div>
  </div>
  <a class="text-button original-link" :href="`${url}#page=${page}`" target="_blank" rel="noopener noreferrer">打开已校验原件 ↗</a>
</template>

<style scoped>
.ocr-source{background:#e9ede4;padding:12px;max-height:560px;overflow:auto;min-height:180px;border:1px solid #dbe3d1;border-radius:5px}.source-surface{position:relative;width:100%;background:white}.source-surface canvas,.source-surface img{display:block;width:100%;height:auto}.source-surface svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.source-surface polygon{fill:#f2c34242;stroke:#b77b17;stroke-width:2;vector-effect:non-scaling-stroke}.original-link{display:inline-block;margin-top:10px;font-size:12px}.ocr-source p{font-size:12px;overflow-wrap:anywhere}a:focus-visible{outline:2px solid #246454;outline-offset:3px}
</style>

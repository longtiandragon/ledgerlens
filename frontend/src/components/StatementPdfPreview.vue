<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { getDocument, GlobalWorkerOptions, type PDFDocumentLoadingTask, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import Icon from './Icon.vue'

GlobalWorkerOptions.workerSrc = workerUrl
const props = defineProps<{
  datasetScopeId: string
  snapshotId: string
  reportId: string
  name: string
  initialPage: number
  highlight: number[]
  pageInfo: { width: number; height: number; rotation: number }
}>()
const filePath = computed(() => `/api/v1/statement-datasets/${encodeURIComponent(props.datasetScopeId)}/snapshots/${encodeURIComponent(props.snapshotId)}/reports/${encodeURIComponent(props.reportId)}/file`)
const host = ref<HTMLElement>()
const canvas = ref<HTMLCanvasElement>()
const page = ref(props.initialPage)
const pages = ref<number>()
const busy = ref(true)
const error = ref('')
const locationWarning = ref('')
const surfaceSize = ref({ width: 0, height: 0 })
const highlightBox = ref<{ left: number; top: number; width: number; height: number }>()
let pdf: PDFDocumentProxy | undefined
let loadingTask: PDFDocumentLoadingTask | undefined
let renderTask: RenderTask | undefined
let generation = 0
let disposed = false
let resizeObserver: ResizeObserver | undefined
let resizeTimer: ReturnType<typeof setTimeout> | undefined

function describe(cause: unknown) {
  return cause instanceof Error ? cause.message : '原 PDF 读取失败'
}

async function render(number: number, centerLocation = false) {
  if (!pdf || !host.value || !canvas.value) return
  const requested = ++generation
  busy.value = true
  error.value = ''
  highlightBox.value = undefined
  locationWarning.value = ''
  const previous = renderTask
  previous?.cancel()
  await previous?.promise.catch(() => undefined)
  if (disposed || requested !== generation) return
  try {
    const originalPage = await pdf.getPage(number)
    if (disposed || requested !== generation || !canvas.value || !host.value) return
    const base = originalPage.getViewport({ scale: 1 })
    const scale = Math.min(1.45, Math.max(0.2, (host.value.clientWidth - 24) / base.width))
    const viewport = originalPage.getViewport({ scale })
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    canvas.value.width = Math.ceil(viewport.width * ratio)
    canvas.value.height = Math.ceil(viewport.height * ratio)
    canvas.value.style.width = `${viewport.width}px`
    canvas.value.style.height = `${viewport.height}px`
    surfaceSize.value = { width: viewport.width, height: viewport.height }
    const task = originalPage.render({ canvas: canvas.value, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] })
    renderTask = task
    await task.promise
    if (disposed || requested !== generation) return
    page.value = number
    if (number === props.initialPage) {
      const [x0, top, x1, bottom] = props.highlight
      const info = props.pageInfo
      // Extraction boxes use the displayed page's top-left coordinate system.
      // Require matching dimensions/rotation before overlaying, rather than
      // implying a reliable location on a different crop or orientation.
      const validBox = props.highlight.length === 4 && props.highlight.every(Number.isFinite)
        && Number.isFinite(info.width) && Number.isFinite(info.height) && info.width > 0 && info.height > 0
        && x0 !== undefined && top !== undefined && x1 !== undefined && bottom !== undefined
        && x0 >= 0 && top >= 0 && x1 > x0 && bottom > top && x1 <= info.width + 1 && bottom <= info.height + 1
      const dimensionsMatch = Math.abs(base.width - info.width) < 2 && Math.abs(base.height - info.height) < 2
        && ((originalPage.rotate % 360) + 360) % 360 === ((info.rotation % 360) + 360) % 360
      if (validBox && dimensionsMatch) {
        highlightBox.value = { left: x0 * viewport.width / info.width, top: top * viewport.height / info.height,
          width: (x1 - x0) * viewport.width / info.width, height: (bottom - top) * viewport.height / info.height }
        if (centerLocation) {
          await nextTick()
          if (disposed || requested !== generation || !host.value || !highlightBox.value) return
          host.value.scrollTop = Math.max(0, highlightBox.value.top - host.value.clientHeight / 3)
        }
      } else {
        locationWarning.value = '页面尺寸、方向或单元格坐标不匹配，未绘制定位框。请按页码和原始文字人工核对。'
      }
    }
  } catch (cause) {
    if (!disposed && requested === generation) error.value = describe(cause)
  } finally {
    if (!disposed && requested === generation) busy.value = false
  }
}

onMounted(async () => {
  const requested = generation
  try {
    loadingTask = getDocument({ url: filePath.value, enableXfa: false })
    const document = await loadingTask.promise
    if (disposed || requested !== generation) return
    pdf = document
    pages.value = document.numPages
    if (!Number.isInteger(props.initialPage) || props.initialPage < 1 || props.initialPage > document.numPages) {
      throw new Error(`证据页码 ${props.initialPage} 不在原 PDF 的 ${document.numPages} 页范围内`)
    }
    await render(props.initialPage, true)
    if (disposed || !host.value) return
    let lastWidth = host.value.clientWidth
    resizeObserver = new ResizeObserver(() => {
      if (disposed || !host.value || Math.abs(host.value.clientWidth - lastWidth) < 2) return
      lastWidth = host.value.clientWidth
      clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => { if (!disposed) void render(page.value) }, 120)
    })
    resizeObserver.observe(host.value)
  } catch (cause) {
    if (!disposed) { error.value = describe(cause); busy.value = false }
  }
})

onBeforeUnmount(() => {
  disposed = true
  generation++
  clearTimeout(resizeTimer)
  resizeObserver?.disconnect()
  renderTask?.cancel()
  void loadingTask?.destroy().catch(() => undefined)
})
</script>

<template>
  <section class="statement-pdf" aria-label="年报原 PDF 预览">
    <div class="statement-pdf-toolbar">
      <div class="statement-pdf-pages">
        <button type="button" class="button" :disabled="busy || !!error || page <= 1" @click="render(page - 1)">上一页</button>
        <span aria-live="polite">第 {{ page }} / {{ pages ?? '—' }} 页</span>
        <button type="button" class="button" :disabled="busy || !!error || !pages || page >= pages" @click="render(page + 1)">下一页</button>
      </div>
      <button type="button" class="text-button" :disabled="busy || !!error" @click="render(initialPage, true)"><Icon name="search" :size="14"/>回到证据位置</button>
    </div>
    <p class="statement-pdf-caption">{{ name }} · 黄色框仅定位本期提取单元格，请对照原文复核。</p>
    <div v-if="error" class="notice error" role="alert">无法读取原 PDF：{{ error }}</div>
    <p v-if="locationWarning" class="notice warning" role="status">{{ locationWarning }}</p>
    <div ref="host" class="statement-pdf-scroll" :aria-busy="busy">
      <span v-if="busy" class="statement-pdf-loading" role="status">正在读取并校验原始页面…</span>
      <div class="statement-pdf-surface" :style="{ width: `${surfaceSize.width}px`, height: `${surfaceSize.height}px` }">
        <canvas ref="canvas" :aria-label="`年报原 PDF 第 ${page} 页`"/>
        <div v-if="highlightBox" class="statement-cell-highlight" aria-label="本期单元格定位框"
          :style="{ left: `${highlightBox.left}px`, top: `${highlightBox.top}px`, width: `${highlightBox.width}px`, height: `${highlightBox.height}px` }"/>
      </div>
    </div>
    <a class="text-button statement-pdf-download" :href="`${filePath}#page=${initialPage}`" target="_blank" rel="noopener noreferrer"><Icon name="file" :size="14"/>在新窗口打开已校验原 PDF</a>
  </section>
</template>

<style scoped>
.statement-pdf{min-width:0}.statement-pdf-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.statement-pdf-pages{display:flex;align-items:center;gap:14px}.statement-pdf-pages span{font:11px Consolas,monospace;white-space:nowrap;color:#526b53}.statement-pdf .button{font-size:11px;padding:7px 11px;min-height:32px}.statement-pdf-toolbar .text-button{display:inline-flex;align-items:center;gap:5px;font-size:11px}.statement-pdf-caption{font-size:11px;line-height:1.8;color:#788574;margin:12px 0}.statement-pdf-scroll{position:relative;min-height:220px;height:58vh;max-height:720px;overflow:auto;background:#e8ebe2;padding:12px;border:1px solid #dbe3d1;border-radius:5px}.statement-pdf-loading{display:block;padding:14px;text-align:center;font-size:12px;color:#647858}.statement-pdf-surface{position:relative;margin:0 auto;background:white;box-shadow:0 2px 12px #30442b15}.statement-pdf-surface canvas{display:block}.statement-cell-highlight{position:absolute;border:2px solid #bd801a;background:#f2c34242;box-shadow:0 0 0 2px #fff8ce88;pointer-events:none}.statement-pdf-download{display:inline-flex;align-items:center;gap:6px;font-size:11px;margin-top:14px}.statement-pdf .notice{font-size:11px;line-height:1.8;margin:12px 0;overflow-wrap:anywhere}button:focus-visible,a:focus-visible{outline:2px solid #246454;outline-offset:3px}@media(max-width:760px){.statement-pdf-toolbar{gap:10px}.statement-pdf-pages{gap:10px}.statement-pdf-scroll{height:52vh;min-height:200px}.statement-pdf-caption{font-size:10px}}
</style>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
GlobalWorkerOptions.workerSrc=workerUrl
const props=defineProps<{documentId:string;name:string;extracted:{page:number;text:string;status:string}[]}>()
const host=ref<HTMLElement>();const canvas=ref<HTMLCanvasElement>();const page=ref(1);const pages=ref(0);const busy=ref(true);const error=ref('')
let document:PDFDocumentProxy|undefined;let rendering:RenderTask|undefined;let disposed=false
const loading=getDocument({url:`/api/v1/documents/${encodeURIComponent(props.documentId)}/file`,enableXfa:false})
async function render(number:number){
 if(!document||!canvas.value||!host.value)return
 busy.value=true;error.value=''
 try{
  if(rendering){rendering.cancel();await rendering.promise.catch(()=>{})}
  const pdfPage=await document.getPage(number)
  if(disposed)return
  const base=pdfPage.getViewport({scale:1});const scale=Math.min(1.4,Math.max(0.3,(host.value.clientWidth-28)/base.width));const viewport=pdfPage.getViewport({scale})
  const ratio=Math.min(window.devicePixelRatio||1,2)
  canvas.value.width=Math.floor(viewport.width*ratio);canvas.value.height=Math.floor(viewport.height*ratio)
  canvas.value.style.width=`${viewport.width}px`;canvas.value.style.height=`${viewport.height}px`
  rendering=pdfPage.render({canvas:canvas.value,viewport,transform:ratio===1?undefined:[ratio,0,0,ratio,0,0]})
  await rendering.promise;page.value=number
 }catch(e){if(!disposed)error.value=e instanceof Error?e.message:String(e)}finally{busy.value=false}
}
onMounted(async()=>{try{document=await loading.promise;if(disposed)return;pages.value=document.numPages;await render(1)}catch(e){if(!disposed)error.value=e instanceof Error?e.message:String(e);busy.value=false}})
onBeforeUnmount(()=>{disposed=true;rendering?.cancel();void loading.destroy()})
</script>
<template>
 <section class="pdf-preview" aria-label="来源 PDF 预览">
  <div class="pdf-controls"><button class="button" :disabled="busy||page<=1" @click="render(page-1)">上一页</button><span aria-live="polite">第 {{ page }} / {{ pages }} 页</span><button class="button" :disabled="busy||page>=pages" @click="render(page+1)">下一页</button></div>
  <p class="caption">{{ name }} · 页码对应原文件。解析文字属于未复核资料，不会自动转成账务记录。</p>
  <div v-if="error" class="notice error" role="alert">无法预览：{{ error }}</div>
  <div ref="host" class="pdf-canvas" :aria-busy="busy"><span v-if="busy">正在读取原始页面…</span><canvas ref="canvas" :aria-label="`原始 PDF 第 ${page} 页`"/></div>
  <details class="pdf-text"><summary>本页解析文字</summary><p v-if="extracted.find(row=>row.page===page)?.status==='ocr_required'">此页需要 OCR，当前没有可用文字。</p><pre v-else>{{ extracted.find(row=>row.page===page)?.text||'未取得本页文字' }}</pre></details>
 </section>
</template>
<style scoped>
.pdf-controls{display:flex;align-items:center;justify-content:center;gap:22px;margin-bottom:16px}.pdf-controls span{font-family:Consolas,monospace}.pdf-canvas{background:#ebece6;min-height:180px;max-height:64vh;overflow:auto;padding:14px;text-align:center;margin-top:16px}.pdf-canvas canvas{display:block;margin:auto;background:white;max-width:100%}.pdf-text{margin-top:16px}.pdf-text summary{cursor:pointer}.pdf-text pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:200px;overflow:auto}
</style>

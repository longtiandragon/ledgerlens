<script setup lang="ts">
import {onMounted,onBeforeUnmount,watch,ref} from 'vue'
import cytoscape, {type Core} from 'cytoscape'
import type {Evidence} from '../api'
const props=defineProps<{evidence:Evidence[];caseId:string}>();const emit=defineEmits<{select:[evidence:Evidence]}>();const host=ref<HTMLElement>();let cy:Core|undefined
function draw(){if(!cy)return;cy.elements().remove();cy.add([{data:{id:'case',label:'当前案件',kind:'case'}},...props.evidence.map((e,i)=>({data:{id:`e-${i}`,label:String(e.record?.source_id || e.record?.voucher_id || e.source_id || e.kind || '证据'),kind:e.kind,evidenceId:e.id}})),...props.evidence.map((e,i)=>({data:{id:`edge-${i}`,source:'case',target:`e-${i}`,label:'引用'}}))]);cy.layout({name:'breadthfirst',directed:true,padding:40,spacingFactor:1.2}).run()}
onMounted(()=>{if(!host.value)return;cy=cytoscape({container:host.value,style:[{selector:'node',style:{'background-color':'#e6eee9','border-width':1,'border-color':'#b9cec3',label:'data(label)',color:'#254d43','font-size':11,'text-valign':'center','text-halign':'center',width:105,height:40,shape:'roundrectangle'}},{selector:'node[kind = "case"]',style:{'background-color':'#204f44',color:'#fff'}},{selector:'edge',style:{width:1.2,'line-color':'#bdcfc5','curve-style':'bezier','target-arrow-shape':'triangle','target-arrow-color':'#bdcfc5'}}],minZoom:.5,maxZoom:2});cy.on('tap','node',e=>{const found=props.evidence.find(x=>x.id===e.target.data('evidenceId'));if(found)emit('select',found)});draw()});watch(()=>[props.evidence,props.caseId],draw);onBeforeUnmount(()=>cy?.destroy())
</script>
<template><div ref="host" class="evidence-graph" aria-label="案件与引用证据关系图"/><p class="caption">连线表示案件引用关系。点击节点查看来源；图中不推断业务因果。</p></template>

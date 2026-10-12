<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
use([BarChart,GridComponent,TooltipComponent,CanvasRenderer])
const props=defineProps<{balances:Record<string,string>}>();const host=ref<HTMLElement>();let chart:EChartsType|undefined;let observer:ResizeObserver|undefined
const draw=()=> {if(!chart)return; const entries=Object.entries(props.balances); chart.setOption({animation:false,grid:{left:60,right:24,top:12,bottom:28},tooltip:{trigger:'axis',valueFormatter:(v:unknown)=>`${Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2})} 元`},xAxis:{type:'value',axisLabel:{color:'#85908c',fontSize:10},splitLine:{lineStyle:{color:'#edf0eb'}}},yAxis:{type:'category',data:entries.map(([k])=>k),axisTick:{show:false},axisLine:{show:false},axisLabel:{color:'#5f7068',fontFamily:'Consolas'}},series:[{type:'bar',barWidth:13,data:entries.map(([,v])=>({value:Number(v),itemStyle:{color:Number(v)<0?'#c5a675':'#267065',borderRadius:3}}))}]},true)}
watch(()=>props.balances,draw,{deep:true});onMounted(()=>{if(host.value){chart=init(host.value);draw();observer=new ResizeObserver(()=>chart?.resize());observer.observe(host.value)}});onBeforeUnmount(()=>{observer?.disconnect();chart?.dispose()})
</script>
<template><div ref="host" class="balance-chart" role="img" aria-label="各科目借方净余额图，精确数值见余额明细"/></template>

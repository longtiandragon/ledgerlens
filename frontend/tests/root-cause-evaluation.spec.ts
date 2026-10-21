import { test, expect } from '@playwright/test'

test('评测中心展示原因调查真实摘要：负面结论、失败计数、作废运行均可见且只读',async({page})=>{
 const writes:string[]=[],errors:string[]=[]
 page.on('request',request=>{if(!['GET','HEAD'].includes(request.method()))writes.push(request.url())})
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto('/')
 await page.getByRole('button',{name:'评测中心',exact:true}).click()
 const panel=page.getByRole('region',{name:'原因调查评测'})
 await expect(panel).toContainText('未获支持')
 // Since the v4 sealed run (2026-10-22) the final card merges its two completed batches: hybrid with
 // the baselines, and the single agent (one structural failure each). The v3 runs, the interrupted
 // active and multi runs and the earlier v2 sealed run stay listed in the run table.
 const final=panel.getByLabel('封存测试集最终对比')
 await expect(final).toContainText('确定性优先混合（模型兜底）')
 await expect(final).toContainText('单Agent（模型）')
 await expect(final).toContainText('1/27')
 await expect(final).toContainText('关键词补证')
 await expect(panel).toContainText('root-cause-v4-sealed-hybrid.json')
 await expect(panel).toContainText('作废：')
 await expect(panel).toContainText('中断：')
 await expect(panel).toContainText('root-cause-v3-sealed-single.json')
 await expect(panel).toContainText('root-cause-v3-sealed-multi.json')
 await expect(panel).toContainText('root-cause-v2-sealed-final.json')
 await page.setViewportSize({width:390,height:844})
 await expect(final).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true)
 expect(writes).toEqual([])
 expect(errors).toEqual([])
})

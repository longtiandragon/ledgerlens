<script setup lang="ts">
import { computed, ref } from 'vue'
import { post, type SessionUser } from '../api'

interface ReviewRow { id:string; decision:string; comment:string; created_at:string; snapshot_id:string; user_id?:string|null }
interface ApprovalRow { id:string; review_id:string; decision:string; comment:string; created_at:string; user_id:string }
const props = defineProps<{ caseId:string; snapshotId:string; stale:boolean; reviews:ReviewRow[]; approvals:ApprovalRow[];
  actors:Record<string, { username:string; display_name:string }>; user:SessionUser|null }>()
const emit = defineEmits<{ (event:'changed'):void }>()
const comment = ref('')
const busy = ref(false)
const message = ref('')
const failure = ref('')

const decisionNames:Record<string,string> = { accepted:'接受', returned:'退回补充', reserved:'保留意见', approved:'审批签发', rejected:'驳回' }
const who = (id?:string|null) => id ? (props.actors[id]?.display_name || id.slice(0, 8)) : '单用户本地记录'
const ordered = computed(() => [...props.reviews].sort((a, b) => a.created_at.localeCompare(b.created_at)))
const latest = computed(() => [...ordered.value].reverse().find(r => r.snapshot_id === props.snapshotId))
const approvalsFor = (reviewId:string) => props.approvals.filter(a => a.review_id === reviewId)
const isApprover = computed(() => !!props.user?.roles.includes('approver'))
const blocker = computed(() => {
  if (!isApprover.value) return '当前账户没有审批签发职责。'
  if (props.stale) return '资料或政策已更新，需在最新快照重新复核后才能审批。'
  if (!latest.value) return '当前快照尚无复核意见。'
  if (!latest.value.user_id) return '该复核来自单用户模式，没有可追溯的复核人，不能审批。'
  if (latest.value.decision === 'returned') return '最新复核为退回补充，不能审批签发。'
  if (latest.value.user_id === props.user?.id) return '复核人不能审批自己的复核（不相容职务分离）。'
  return ''
})

async function sign(decision:'approved'|'rejected') {
  if (!latest.value || blocker.value || busy.value) return
  busy.value = true; failure.value = ''; message.value = ''
  try {
    await post(`/cases/${props.caseId}/approvals`, { review_id: latest.value.id, expected_snapshot_id: props.snapshotId, decision, comment: comment.value })
    message.value = decision === 'approved' ? '已审批签发，签署记录已追加。' : '已驳回，签署记录已追加。'
    comment.value = ''
    emit('changed')
  } catch (error) {
    failure.value = error instanceof Error ? error.message : String(error)
  } finally { busy.value = false }
}
</script>

<template>
  <section class="signoff" aria-labelledby="signoff-title">
    <h3 id="signoff-title">复核与审批签署链</h3>
    <p class="caption">调查、复核、审批须由不同账户完成；签署只追加，不改写历史。</p>
    <ol v-if="ordered.length" class="signoff-chain">
      <li v-for="r in ordered" :key="r.id">
        <span class="badge neutral">复核 · {{ decisionNames[r.decision] || r.decision }}</span>
        <strong>{{ who(r.user_id) }}</strong><small>{{ r.created_at }}</small>
        <p v-if="r.comment">{{ r.comment }}</p>
        <ul v-if="approvalsFor(r.id).length">
          <li v-for="a in approvalsFor(r.id)" :key="a.id">
            <span :class="['badge', a.decision === 'approved' ? 'success' : 'danger']">{{ decisionNames[a.decision] }}</span>
            <strong>{{ who(a.user_id) }}</strong><small>{{ a.created_at }}</small><p v-if="a.comment">{{ a.comment }}</p>
          </li>
        </ul>
      </li>
    </ol>
    <div v-else class="small-empty">尚无复核记录。</div>
    <div v-if="user" class="signoff-action">
      <div v-if="blocker" class="notice">{{ blocker }}</div>
      <template v-else>
        <label class="form-label">审批意见<textarea v-model="comment" rows="3" maxlength="4000" placeholder="说明签发或驳回依据…"/></label>
        <div class="button-row">
          <button class="button primary" :disabled="busy" @click="sign('approved')">审批签发</button>
          <button class="button" :disabled="busy" @click="sign('rejected')">驳回</button>
        </div>
      </template>
      <div v-if="message" class="notice success" role="status">{{ message }}</div>
      <div v-if="failure" class="notice error" role="alert">{{ failure }}</div>
    </div>
  </section>
</template>

<style scoped>
.signoff { margin-top: 18px; display: grid; gap: 10px; }
.signoff-chain { margin: 0; padding-left: 18px; display: grid; gap: 10px; }
.signoff-chain li { display: grid; gap: 4px; }
.signoff-chain ul { list-style: none; padding-left: 12px; border-left: 2px solid rgba(36, 100, 84, .25); }
.signoff-chain p { margin: 0; overflow-wrap: anywhere; }
.signoff-action { display: grid; gap: 10px; }
</style>

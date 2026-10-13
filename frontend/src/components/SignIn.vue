<script setup lang="ts">
import { ref } from 'vue'
import { post, setSessionToken, type SessionUser } from '../api'

const emit = defineEmits<{ (event:'signed-in', user:SessionUser):void }>()
const username = ref('')
const password = ref('')
const busy = ref(false)
const failure = ref('')

async function submit() {
  if (busy.value) return
  busy.value = true
  failure.value = ''
  try {
    const result = await post<{ token:string; user:SessionUser }>('/auth/login', { username: username.value.trim(), password: password.value })
    setSessionToken(result.token)
    password.value = ''
    emit('signed-in', result.user)
  } catch (error) {
    failure.value = error instanceof Error ? error.message : String(error)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="signin-shell">
    <form class="signin-card panel" aria-labelledby="signin-title" @submit.prevent="submit">
      <div class="eyebrow">MULTI-USER SIGN-OFF</div>
      <h2 id="signin-title">登录账证智核</h2>
      <p class="caption">多用户审签模式：调查、复核与审批分别由不同账户完成，账户与职责由管理员分配。</p>
      <label class="form-label">用户名<input v-model="username" autocomplete="username" required minlength="3" maxlength="32"/></label>
      <label class="form-label">密码<input v-model="password" type="password" autocomplete="current-password" required maxlength="256"/></label>
      <div v-if="failure" class="notice error" role="alert">{{ failure }}</div>
      <button class="button primary" type="submit" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button>
    </form>
  </div>
</template>

<style scoped>
.signin-shell { min-height: 100vh; display: grid; place-items: center; padding: 16px; background: #f5f5ef; }
.signin-card { width: min(420px, 100%); display: grid; gap: 14px; }
.signin-card h2 { margin: 0; }
.signin-card input { width: 100%; }
</style>

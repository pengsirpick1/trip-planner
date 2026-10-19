<template>
  <div id="app">
    <a-layout style="min-height: 100vh">
      <a-layout-header style="background: #001529; padding: 0 50px">
        <div class="header-row">
          <div style="color: white; font-size: 24px; font-weight: bold">🌍 智能旅行规划 Agent 图编排系统</div>
          <a-space>
            <span v-if="user" class="user-name">{{ user.username }}</span>
            <a-button v-if="user" size="small" @click="openKeys">模型 Key</a-button>
            <a-button v-if="user" size="small" @click="logout">退出</a-button>
            <a-button v-else size="small" type="primary" @click="visible = true">登录 / 注册</a-button>
          </a-space>
        </div>
      </a-layout-header>
      <a-layout-content style="padding: 24px"><router-view /></a-layout-content>
      <a-modal v-model:open="visible" title="登录后开始规划" :footer="null">
        <a-tabs v-model:activeKey="mode"><a-tab-pane key="login" tab="登录" /><a-tab-pane key="register" tab="注册" /></a-tabs>
        <a-form layout="vertical" @finish="submit">
          <a-form-item label="用户名" name="username" :rules="[{ required: true, message: '请输入用户名' }]"><a-input v-model:value="form.username" autocomplete="username" /></a-form-item>
          <a-form-item label="密码" name="password" :rules="[{ required: true, min: 8, message: '密码至少 8 位' }]"><a-input-password v-model:value="form.password" autocomplete="current-password" /></a-form-item>
          <a-alert v-if="error" type="error" show-icon :message="error" style="margin-bottom: 12px" />
          <a-button html-type="submit" type="primary" block :loading="loading">{{ mode === 'login' ? '登录' : '注册并登录' }}</a-button>
        </a-form>
      </a-modal>
      <a-modal v-model:open="keysVisible" title="我的模型 Key" :footer="null" @after-open-change="loadKeys">
        <a-alert type="info" show-icon message="Key 仅用于本次账户的模型调用" description="服务端采用 AES-256-GCM 加密保存；列表不会返回 Key 明文。使用自己的 Key 不消耗平台共享额度。" style="margin-bottom: 16px" />
        <a-form layout="vertical" @finish="saveKey">
          <a-form-item label="服务商"><a-select v-model:value="keyForm.provider"><a-select-option value="deepseek">DeepSeek</a-select-option><a-select-option value="openai">OpenAI</a-select-option></a-select></a-form-item>
          <a-form-item label="API Key" :rules="[{ required: true }]" name="apiKey"><a-input-password v-model:value="keyForm.apiKey" autocomplete="off" placeholder="仅在保存时提交，不会再次显示" /></a-form-item>
          <a-form-item label="模型（可选）"><a-input v-model:value="keyForm.model" placeholder="例如 deepseek-chat / gpt-4o-mini" /></a-form-item>
          <a-alert v-if="keyError" type="error" :message="keyError" style="margin-bottom: 12px" />
          <a-button type="primary" html-type="submit" :loading="keyLoading">加密保存</a-button>
        </a-form>
        <a-list size="small" :data-source="credentials" style="margin-top: 16px"><template #renderItem="{ item }"><a-list-item><a-space>{{ item.provider }}<a-tag v-if="item.is_active" color="green">当前使用</a-tag></a-space><template #actions><a-button size="small" :disabled="item.is_active" @click="activateKey(item.provider)">设为当前</a-button><a-button size="small" @click="verifyKey(item.provider)">验证</a-button><a-button danger size="small" @click="removeKey(item.provider)">删除</a-button></template></a-list-item></template></a-list>
      </a-modal>
      <a-layout-footer style="text-align: center">
        智能旅行规划 Agent 图编排系统
      </a-layout-footer>
    </a-layout>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { clearAuth, getAuthUser, login, register, listProviderCredentials, saveProviderCredential, deleteProviderCredential, activateProviderCredential, verifyProviderCredential, type AuthUser, type ProviderCredential, type SupportedProvider } from './services/api'
const user = ref<AuthUser | null>(getAuthUser())
const visible = ref(false); const mode = ref<'login' | 'register'>('login'); const loading = ref(false); const error = ref('')
const form = ref({ username: '', password: '' })
const keysVisible = ref(false); const credentials = ref<ProviderCredential[]>([]); const keyLoading = ref(false); const keyError = ref('')
const keyForm = ref<{ provider: SupportedProvider; apiKey: string; model: string }>({ provider: 'deepseek', apiKey: '', model: '' })
async function submit() { loading.value = true; error.value = ''; try { const response = mode.value === 'login' ? await login(form.value.username, form.value.password) : await register(form.value.username, form.value.password); user.value = response.user; visible.value = false; message.success('登录成功') } catch (e: any) { error.value = e.response?.data?.detail || '登录失败，请稍后重试' } finally { loading.value = false } }
function logout() { clearAuth(); user.value = null; message.success('已退出登录') }
async function loadKeys() { if (!keysVisible.value) return; try { credentials.value = await listProviderCredentials() } catch (e: any) { keyError.value = e?.message || '无法加载模型凭证' } }
function openKeys() { keyError.value = ''; keyForm.value.apiKey = ''; keysVisible.value = true }
async function saveKey() { keyLoading.value = true; keyError.value = ''; try { await saveProviderCredential(keyForm.value.provider, keyForm.value.apiKey, undefined, keyForm.value.model); keyForm.value.apiKey = ''; await loadKeys(); message.success('已加密保存') } catch (e: any) { keyError.value = e?.message || '保存失败' } finally { keyLoading.value = false } }
async function removeKey(provider: SupportedProvider) { try { await deleteProviderCredential(provider); await loadKeys(); message.success('已删除') } catch (e: any) { keyError.value = e?.message || '删除失败' } }
async function activateKey(provider: SupportedProvider) { try { await activateProviderCredential(provider); await loadKeys(); message.success('已切换当前模型服务') } catch (e: any) { keyError.value = e?.message || '切换失败' } }
async function verifyKey(provider: SupportedProvider) { try { await verifyProviderCredential(provider); await loadKeys(); message.success('连接验证成功') } catch (e: any) { keyError.value = e?.message || '验证失败，请检查 Key、地址和网络' } }
</script>

<style>
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif;
}
.header-row { height: 64px; display: flex; align-items: center; justify-content: space-between; }
.user-name { color: rgba(255,255,255,.85); }
</style>



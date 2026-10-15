/**
 * 前端侧的凭据与请求通道。
 *
 * 为什么集中在这儿而不是每个组件各自 fetch：CSRF 头与用户密钥要**每个请求都带**，
 * 散在各处一定会漏；这里给 window.fetch 打一层薄包装，现有组件的 fetch 调用不用改。
 * 单机模式（AUTH_ENABLED=0）时后端不校验这些头，包装是无害的空操作。
 *
 * 密钥去向：默认只存在这台设备的 localStorage 里，服务端不留副本、不进日志；
 * 用户主动勾"加密保存到本账户"才会把明文交给后端做一次 Fernet 加密入库（可随时关掉，
 * 关掉即清空库里的密文）。勾选之后本模块就不再随请求带明文 key，由后端取库里那份。
 */

const KEYS = { ak: 'c15.bmapAk', sk: 'c15.bmapSk', llmKey: 'c15.llmKey',
               llmBase: 'c15.llmBase', llmModel: 'c15.llmModel',
               store: 'c15.storeKeys', demo: 'c15.demoMode' }

const BASE = import.meta.env.VITE_API_BASE || ''

function read(key) {
  try { return localStorage.getItem(key) || '' } catch (e) { return '' }
}

function write(key, value) {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch (e) { /* 隐私模式下 localStorage 会抛，忽略即可 */ }
}

export function localCreds() {
  return { bmapAk: read(KEYS.ak), bmapSk: read(KEYS.sk), llmKey: read(KEYS.llmKey),
           llmBase: read(KEYS.llmBase), llmModel: read(KEYS.llmModel),
           store: read(KEYS.store) === '1', demo: read(KEYS.demo) === '1' }
}

export function saveCreds({ bmapAk, bmapSk, llmKey, llmBase, llmModel, store, demo } = {}) {
  if (bmapAk !== undefined) write(KEYS.ak, bmapAk.trim())
  if (bmapSk !== undefined) write(KEYS.sk, bmapSk.trim())
  if (llmKey !== undefined) write(KEYS.llmKey, llmKey.trim())
  if (llmBase !== undefined) write(KEYS.llmBase, llmBase.trim())
  if (llmModel !== undefined) write(KEYS.llmModel, llmModel.trim())
  if (store !== undefined) write(KEYS.store, store ? '1' : '0')
  if (demo !== undefined) write(KEYS.demo, demo ? '1' : '0')
}

export function clearCreds() {
  Object.values(KEYS).forEach((k) => write(k, ''))
}

function cookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]) : ''
}

/** 会话过期/未登录时广播一次，App 收到就弹回登录视图。 */
export const UNAUTHORIZED = 'c15:unauthorized'
function announceUnauthorized() {
  window.dispatchEvent(new CustomEvent(UNAUTHORIZED))
}

/**
 * 给每个请求补上 CSRF 与密钥头。
 * 只在 main.js 调用一次；包一层而不是改所有组件，是为了让"忘带头"这件事在结构上不可能发生。
 */
export function installFetchAuth() {
  if (installFetchAuth.done) return
  installFetchAuth.done = true
  const raw = window.fetch.bind(window)
  window.fetch = async (input, init = {}) => {
    const headers = new Headers(init.headers || (input.headers ? input.headers : {}))
    const method = (init.method || (input.method || 'GET')).toUpperCase()
    const c = localCreds()
    if (method !== 'GET' && !headers.has('x-csrf-token')) {
      const t = cookie('csrf')
      if (t) headers.set('X-CSRF-Token', t)
    }
    const map = { 'X-Bmap-Ak': c.bmapAk, 'X-Bmap-Sk': c.bmapSk, 'X-Llm-Key': c.llmKey,
                  'X-Llm-Base': c.llmBase, 'X-Llm-Model': c.llmModel }
    for (const [h, v] of Object.entries(map)) if (v && !headers.has(h.toLowerCase())) headers.set(h, v)
    // 勾了"加密保存到本账户"就别再每请求带上明文：让后端用库里那份
    if (c.store) ['x-bmap-ak', 'x-bmap-sk', 'x-llm-key'].forEach((h) => headers.delete(h))
    // 多用户下演示模式是请求级的（后端不因此改全局配置），所以用一个头表达
    if (c.demo) headers.set('X-Demo-Mode', '1')
    const res = await raw(input, { ...init, headers, credentials: 'same-origin' })
    if (res.status === 401) announceUnauthorized()
    return res
  }
}

async function json(res) {
  const d = await res.json().catch(() => ({}))
  return { ok: res.ok, status: res.status, ...(typeof d === 'object' && d !== null ? d : {}) }
}

export const api = (path) => `${BASE}${path}`

/** 登录态：auth_enabled / 用户 / 剩余预算。401 在这里不算失败——它就是"还没登录"。 */
export async function me() {
  try {
    const res = await fetch(api('/api/auth/me'), { headers: { Accept: 'application/json' } })
    return { ok: res.ok, status: res.status, ...(await res.json().catch(() => ({}))) }
  } catch (e) {
    return { ok: false, status: 0, auth_enabled: false, user: null, offline: true }
  }
}

export async function login(email, password) {
  return json(await fetch(api('/api/auth/login'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  }))
}

export async function register(email, password, nickname) {
  return json(await fetch(api('/api/auth/register'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, nickname })
  }))
}

/** 登出清的是浏览器里的钥匙：服务端那份会话记录同时作废，回来必须重新登录。 */
export async function logout() {
  const res = await fetch(api('/api/auth/logout'), { method: 'POST' })
  clearCreds()
  return json(res)
}

/** 改显示名（邮箱是登录凭据，不在这里动）。 */
export async function updateNickname(nickname) {
  return json(await fetch(api('/api/auth/profile'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nickname })
  }))
}

/** 后端在 401/403/429 时用 detail 说人话，前端别只显示"请求失败"。 */
export async function errorMessage(res) {
  if (res.ok) return ''
  const d = await res.json().catch(() => ({}))
  // 参数校验失败时 FastAPI 的 detail 是数组，不能直接塞进界面
  const text = typeof d.detail === 'string' ? d.detail : ''
  if (res.status === 401) return '登录已过期，请重新登录'
  if (res.status === 403) return text || 'CSRF 校验未通过，请刷新页面重试'
  if (res.status === 429) return text || '操作过于频繁，请稍后再试'
  return text || `请求失败（${res.status}）`
}

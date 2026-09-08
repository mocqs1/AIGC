const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function request(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options)
  if (response.status === 204) return null
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload.detail
    const message = typeof detail === 'string' ? detail : detail?.message || '请求失败，请稍后重试。'
    throw new Error(message)
  }
  return payload
}

export function fetchProviders() {
  return request('/api/providers')
}

export function fetchModuleSettings() {
  return request('/api/settings/modules')
}

export function saveModuleSettings(moduleId, body) {
  return request(`/api/settings/modules/${encodeURIComponent(moduleId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function testModuleSettings(moduleId, body) {
  return request(`/api/settings/modules/${encodeURIComponent(moduleId)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function discoverModuleModels(moduleId, body) {
  return request(`/api/settings/modules/${encodeURIComponent(moduleId)}/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function detectGateway(body) {
  return request('/api/settings/gateways/detect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function createCustomModule(body) {
  return request('/api/settings/modules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deleteCustomModule(moduleId) {
  return request(`/api/settings/modules/${encodeURIComponent(moduleId)}`, {
    method: 'DELETE',
  })
}

export function fetchHermesSettings() {
  return request('/api/settings/hermes')
}

export function saveHermesSettings(body) {
  return request('/api/settings/hermes', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function testHermesSettings(body) {
  return request('/api/settings/hermes/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function createGenerationBatch(body) {
  return request('/api/generation-batches', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function fetchGenerationBatch(batchId) {
  return request(`/api/generation-batches/${batchId}`)
}

export function importMedia(files) {
  const body = new FormData()
  files.forEach((file) => {
    body.append('files', file, file.name)
    body.append('relative_paths', file.webkitRelativePath || file.name)
  })
  return request('/api/media/import', { method: 'POST', body })
}

export function createMix(body) {
  return request('/api/mixes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function planMix(body) {
  return request('/api/mixes/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function fetchMix(mixId) {
  return request(`/api/mixes/${mixId}`)
}

export function fetchJobs() {
  return request('/api/jobs?limit=20')
}

export function fetchAssets() {
  return request('/api/assets')
}

export function fetchR2Status() {
  return request('/api/storage/r2')
}

export function previewPrompt(body) {
  return request('/api/prompts/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function createGeneration(body) {
  return request('/api/generations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function fetchJob(jobId) {
  return request(`/api/jobs/${jobId}`)
}

export function openOutputDirectory(assetId) {
  return request(`/api/assets/${assetId}/open-directory`, { method: 'POST' })
}

export function uploadAssetToR2(assetId) {
  return request(`/api/storage/r2/assets/${assetId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
}

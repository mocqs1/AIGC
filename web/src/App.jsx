import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Aperture,
  ArrowDownToLine,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  CircleAlert,
  Clapperboard,
  CloudUpload,
  Copy,
  ExternalLink,
  FolderOpen,
  Images,
  Image as ImageIcon,
  Link2,
  Layers3,
  LoaderCircle,
  Palette,
  Settings,
  Play,
  Plus,
  RefreshCw,
  Sparkles,
  ShieldCheck,
  WandSparkles,
  Trash2,
  X,
} from 'lucide-react'
import {
  createCustomModule,
  createGeneration,
  createGenerationBatch,
  createMix,
  deleteCustomModule,
  detectGateway,
  discoverModuleModels,
  planMix,
  fetchGenerationBatch,
  fetchModuleSettings,
  fetchAssets,
  fetchJob,
  fetchJobs,
  fetchMix,
  fetchProviders,
  fetchR2Status,
  openOutputDirectory,
  previewPrompt,
  importMedia,
  saveModuleSettings,
  testModuleSettings,
  uploadAssetToR2,
} from './api'

const MODES = [
  { id: 'image', label: '图片', icon: ImageIcon, description: '使用已配置图片模型生成商品宣传图' },
  { id: 'poster', label: '海报', icon: Palette, description: '使用 mono-color 生成单色或双色编辑海报' },
  { id: 'video', label: '视频', icon: Clapperboard, description: '使用已配置视频模型生成短片' },
  { id: 'tiktok_10s', label: 'TikTok 10s', icon: Sparkles, description: '塑身衣广告预设' },
]

const WORKFLOW_OPTIONS = {
  image: [
    { value: 'image', label: '通用商品图' },
    { value: 'shapewear_image', label: '塑身衣商品图' },
    { value: 'clothing_image_to_image', label: '服装图生图' },
    { value: 'tiktok_clothing_image', label: 'TikTok 服装主图' },
    { value: 'model_outfit_swap', label: '模特换装' },
  ],
  video: [
    { value: 'video', label: '通用视频' },
    { value: 'shapewear_video', label: '塑身衣视频素材快速生成' },
  ],
  tiktok_10s: [{ value: 'tiktok_10s', label: 'TikTok 10s 广告' }],
  poster: [{ value: 'poster', label: 'Mono-color 海报' }],
}

const POSTER_PALETTES = [
  { value: 'palette_cobalt', label: 'Cobalt（单色）' },
  { value: 'palette_terracotta', label: 'Terracotta（单色）' },
  { value: 'palette_signal_red', label: 'Signal Red（单色）' },
  { value: 'palette_aubergine', label: 'Aubergine（单色）' },
  { value: 'palette_charcoal', label: 'Charcoal（单色）' },
  { value: 'palette_cobalt_terracotta', label: 'Cobalt + Terracotta' },
  { value: 'palette_charcoal_signal_red', label: 'Charcoal + Signal Red' },
  { value: 'palette_botanical_oxblood', label: 'Botanical Green + Oxblood' },
  { value: 'palette_mint_charcoal', label: 'Mint Green + Charcoal' },
  { value: 'palette_ultramarine_safety_orange', label: 'Ultramarine + Safety Orange' },
  { value: 'palette_cyan_brick_red', label: 'Cyan + Brick Red' },
]
const POSTER_LAYOUTS = [
  { value: 'composition_editorial_cover', label: 'Editorial cover' },
  { value: 'composition_image_field', label: 'Image field' },
  { value: 'composition_specimen_annotation', label: 'Specimen annotation' },
  { value: 'composition_type_declaration', label: 'Type-led declaration' },
  { value: 'composition_ruled_information', label: 'Ruled information poster' },
  { value: 'composition_archival_plate', label: 'Archival plate' },
  { value: 'composition_object_field', label: 'Object field' },
  { value: 'composition_overprint_collage', label: 'Overprint collage' },
  { value: 'composition_editorial_journal', label: 'Editorial journal' },
]

const SHAPEWEAR_STYLES = [
  { value: 'luxury_fashion', label: '高端棚拍' },
  { value: 'fashion_campaign', label: '高级服装广告' },
  { value: 'tiktok_ugc', label: 'TikTok UGC 真人试穿' },
  { value: 'product_detail', label: '面料工艺特写' },
]

const SHAPEWEAR_STYLE_DEMANDS = {
  product_detail: {
    scene: '干净中性棚拍，商品完整居中',
    style: '商业商品静物摄影，面料与结构特写',
  },
  luxury_fashion: {
    scene: '干净中性棚拍，浅灰无缝背景，商品完整居中',
    style: '高端商业棚拍，纯商品或无头模特',
  },
  fashion_campaign: {
    scene: '高端时装棚或干净建筑空间，全身可见，杂志光影',
    style: '高级服装广告，成年模特全身着装，时装大片构图',
  },
  tiktok_ugc: {
    scene: '明亮公寓更衣区，全身镜前，自然窗光，竖构图',
    style: 'TikTok UGC 真人试穿，手机竖拍，成年模特全身着装展示',
  },
}

const SHAPEWEAR_STYLE_HINTS = {
  product_detail: '面料工艺特写：商品完整居中，突出压缩结构和接缝。',
  luxury_fashion: '高端棚拍模板：纯商品或无头模特，无卧室生活方式。',
  fashion_campaign: '高级服装广告：成年模特全身着装，杂志大片光影，不是手机 UGC。',
  tiktok_ugc: '真人试穿模板：成年模特全身着装，竖构图手机视角。',
}

function shapewearDemandFor(styleId, workflow) {
  const resolved = workflow === 'tiktok_10s' ? 'tiktok_ugc' : (SHAPEWEAR_STYLE_DEMANDS[styleId] ? styleId : 'product_detail')
  const demand = SHAPEWEAR_STYLE_DEMANDS[resolved]
  return { style_id: resolved, scene: demand.scene, style: demand.style }
}

const SHAPEWEAR_VIDEO_CLIPS = [
  {
    id: 'tryon_mirror',
    label: '试穿全身镜',
    hint: '更衣区站立试穿，竖版手机机位。',
    duration_seconds: 8,
    aspect_ratio: '9:16',
    resolution: '1080x1920',
    style_id: 'tiktok_ugc',
    scene: '明亮公寓更衣区，全身镜前，自然窗光，竖构图',
    style: '站立试穿短片，手持手机机位，成年模特全身着装',
  },
  {
    id: 'fashion_walk',
    label: '时装走步',
    hint: '杂志大片走步，竖版 8 秒。',
    duration_seconds: 8,
    aspect_ratio: '9:16',
    resolution: '1080x1920',
    style_id: 'fashion_campaign',
    scene: '高端时装棚或干净建筑空间，全身可见，杂志光影',
    style: '高级服装广告走步，成年模特全身着装',
  },
  {
    id: 'studio_walk',
    label: '棚拍走秀',
    hint: '棚内走步展示轮廓和压缩分区。',
    duration_seconds: 8,
    aspect_ratio: '9:16',
    resolution: '1080x1920',
    style_id: 'luxury_fashion',
    scene: '干净高端时装棚，可控灯光，商品完整可见',
    style: '棚拍走步短片，展示轮廓与面料',
  },
  {
    id: 'fabric_macro',
    label: '面料特写',
    hint: '5 秒接缝和压缩结构特写。',
    duration_seconds: 5,
    aspect_ratio: '9:16',
    resolution: '1080x1920',
    style_id: 'product_detail',
    scene: '中性棚拍，面料与接缝特写，商品居中',
    style: '面料工艺短片，突出结构和压缩',
  },
  {
    id: 'landscape_showcase',
    label: '横版展示',
    hint: '16:9 横版 8 秒时装展示。',
    duration_seconds: 8,
    aspect_ratio: '16:9',
    resolution: '1920x1080',
    style_id: 'fashion_campaign',
    scene: '宽幅高端棚或建筑空间，全身构图',
    style: '横版时装展示，成年模特全身着装',
  },
]

function resolutionForAspect(aspect) {
  return { '9:16': '1080x1920', '16:9': '1920x1080', '1:1': '1080x1080' }[aspect] || '1080x1920'
}

function shapewearVideoClipFor(clipId) {
  const clip = SHAPEWEAR_VIDEO_CLIPS.find((item) => item.id === clipId) || SHAPEWEAR_VIDEO_CLIPS[2]
  return {
    clip_id: clip.id,
    duration_seconds: clip.duration_seconds,
    aspect_ratio: clip.aspect_ratio,
    resolution: clip.resolution,
    style_id: clip.style_id,
    scene: clip.scene,
    style: clip.style,
  }
}

const TIKTOK_CLOTHING_PURPOSES = [
  { value: 'shop_listing', label: 'Shop listing 主图' },
  { value: 'photo_carousel', label: 'Photo carousel 轮播' },
  { value: 'video_cover', label: 'Video cover 封面' },
  { value: 'ugc_variant', label: 'Creator UGC 变体' },
]

const TIKTOK_CLOTHING_STYLES = [
  { value: 'studio_detail', label: 'Studio detail 工艺细节' },
  { value: 'creator_ugc', label: 'Creator UGC 原生感' },
]
const GENERIC_IMAGE_DEFAULTS = {
  product: '哑光玻璃香水瓶',
  scene: '干净中性棚拍，浅灰无缝背景',
  style: '商业静物摄影',
  color: '',
  material: '',
  target_market: '',
  style_id: '',
  clip_id: '',
  duration_seconds: '',
  aspect_ratio: '',
  resolution: '',
  prompt: '',
}

const SHAPEWEAR_FORM_DEFAULTS = {
  product: '黑色高腰塑身衣',
  color: '黑色',
  material: '无缝高弹塑形面料',
  target_market: '美国',
  style_id: 'product_detail',
  ...SHAPEWEAR_STYLE_DEMANDS.product_detail,
}

const INITIAL_FORM = {
  ...GENERIC_IMAGE_DEFAULTS,
  imageProvider: '',
  outfitProvider: 'hermes',
  provider: 'veo',
  referenceKind: 'url',
  reference: '',
  referenceImages: [],
  outfitModelImage: '',
  outfitImages: [],
  tiktokPurpose: 'shop_listing',
  tiktokStyle: 'studio_detail',
  tiktokAspectRatio: '9:16',
  tiktokClaims: '',
  tiktokMarket: 'US',
  tiktokLocale: 'en-US',
  tiktokPresentation: 'product_only',
  tiktokMasterUrl: '',
  tiktokDetailUrls: '',
  posterSubject: '城市夜行中的一盏路灯',
  posterIntent: 'an observed cultural note',
  posterText: 'NIGHT WALK',
  posterRatio: '3:4',
  posterPalette: 'palette_cobalt_terracotta',
  posterSubstrate: 'substrate_neutral_white',
  posterLayout: 'composition_editorial_cover',
  posterTypeRole: 'type_cultural_grotesk',
  posterTension: 'relaxed',
  posterReferenceImage: '',
}

function classNames(...values) {
  return values.filter(Boolean).join(' ')
}

function relativeTime(value) {
  if (!value) return ''
  const delta = Math.max(0, Date.now() - new Date(value).getTime())
  const minutes = Math.round(delta / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  return `${Math.round(minutes / 60)} 小时前`
}

function isVideoAsset(url = '') {
  return /\.(mp4|webm|mov|m4v)(?:$|\?)/i.test(url)
}

function useDialogFocus(open, onClose) {
  const dialogRef = useRef(null)
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose }, [onClose])
  useEffect(() => {
    if (!open) return undefined
    const previousFocus = document.activeElement
    const dialog = dialogRef.current
    const focusableSelector = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
    const focusable = () => Array.from(dialog?.querySelectorAll(focusableSelector) || [])
    ;(focusable()[0] || dialog)?.focus()
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const controls = focusable()
      if (!controls.length) { event.preventDefault(); return }
      const first = controls[0]
      const last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus()
    }
  }, [open])
  return dialogRef
}

function Header({ providers, r2Status, onSettings }) {
  const ready = providers.filter((item) => item.available).length
  return (
    <header className="topbar">
      <div className="brand" aria-label="AIGC Studio">
        <span className="brand-mark"><Aperture size={18} strokeWidth={2.25} /></span>
        <span>AIGC Studio</span>
      </div>
      <div className="topbar-status" aria-label="Provider 状态">
        <span className={classNames('status-dot', ready ? 'is-ready' : 'is-pending')} />
        <span>{ready}/{providers.length || 3} Provider 已就绪</span>
      </div>
      <div className="storage-status" title={r2Status.available ? r2Status.public_base_url : r2Status.message}>
        <CloudUpload size={15} />
        <span>R2 {r2Status.available ? '已连接' : '未配置'}</span>
      </div>
      <button type="button" className="icon-button" title="API 配置" aria-label="API 配置" onClick={onSettings}><Settings size={16} /></button>
      <a className="text-button" href="#history"><FolderOpen size={16} />输出记录</a>
    </header>
  )
}

function ModeTabs({ mode, onChange }) {
  return (
    <div className="mode-tabs" role="tablist" aria-label="生成模式">
      {MODES.map(({ id, label, icon: Icon }) => (
        <button
          className={classNames('mode-tab', mode === id && 'is-selected')}
          key={id}
          onClick={() => onChange(id)}
          role="tab"
          aria-selected={mode === id}
          type="button"
        >
          <Icon size={16} />{label}
        </button>
      ))}
    </div>
  )
}

function InputField({ label, value, onChange, placeholder, required = false, textarea = false, disabled = false, hint, type = 'text', autoComplete }) {
  const Component = textarea ? 'textarea' : 'input'
  return (
    <label className="field">
      <span className="field-label">{label}{required && <b aria-label="必填">*</b>}</span>
      <Component
        type={!textarea ? type : undefined}
        autoComplete={!textarea ? autoComplete : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        rows={textarea ? 3 : undefined}
      />
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

const MODULE_BY_PROVIDER = {
  hermes: 'image.hermes',
  hermes_volcano: 'image.liblib',
  liblib: 'image.liblib',
  veo: 'video.veo',
  seedance: 'video.seedance',
}


function moduleIdForProvider(provider, modules = []) {
  if (!provider) return undefined
  if (MODULE_BY_PROVIDER[provider]) return MODULE_BY_PROVIDER[provider]
  return modules.some((item) => item.id === provider) ? provider : undefined
}

function availableImageProviders(providers = [], allowedProviders) {
  return providers.filter((item) => item.media_types?.includes('image') && item.available && (!allowedProviders || allowedProviders.includes(item.id)))
}

function firstAvailableImageProvider(providers = [], current, allowedProviders) {
  const available = availableImageProviders(providers, allowedProviders)
  if (current && available.some((item) => item.id === current)) return current
  return available[0]?.id || current || 'hermes'
}


function mixPlannerModule(modules = []) {
  const planners = modules.filter((item) => item.category === 'mix_planner' && item.enabled !== false && item.api_key_configured)
  return planners.find((item) => item.kind === 'custom') || planners.find((item) => item.id === 'mix.codex_terra') || null
}

function moduleModelLabel(module, catalog) {
  const modelId = typeof module?.model === 'string' ? module.model.trim() : ''
  if (!modelId) return '未配置模型'
  const catalogMatches = catalog && (!catalog.api_url || catalog.api_url === module.api_url) && (!catalog.model_id || catalog.model_id === modelId)
  const discovered = catalogMatches && Array.isArray(catalog.models)
    ? catalog.models.find((model) => model?.id === modelId)
    : null
  const name = typeof discovered?.name === 'string' ? discovered.name.trim() : ''
  return name || modelId || '未配置模型'
}

function modelSnapshot(provider, modules, modelCatalog) {
  const moduleId = moduleIdForProvider(provider, modules)
  const module = modules.find((item) => item.id === moduleId)
  const modelId = typeof module?.model === 'string' ? module.model.trim() : ''
  if (!modelId) return {}
  const catalog = modelCatalog?.[moduleId]
  const catalogMatches = catalog && catalog.api_url === module.api_url && catalog.model_id === modelId
  const discovered = catalogMatches && Array.isArray(catalog.models)
    ? catalog.models.find((item) => item?.id === modelId)
    : null
  const modelName = typeof discovered?.name === 'string' ? discovered.name.trim() : ''
  return { model: modelId, ...(modelName && modelName !== modelId ? { model_name: modelName } : {}) }
}

function jobModelLabel(job, modules, modelCatalog) {
  const model = [job?.model, job?.model_id]
    .find((value) => typeof value === 'string' && value.trim())?.trim() || ''
  const name = [job?.model_name, job?.model_display_name]
    .find((value) => typeof value === 'string' && value.trim())?.trim() || ''
  if (name) return name
  if (model) return model
  const moduleId = moduleIdForProvider(job?.provider, modules)
  return moduleModelLabel((modules || []).find((item) => item.id === moduleId), modelCatalog?.[moduleId])
}

function ProviderPicker({ provider, providers, modules = [], modelCatalog, onChange }) {
  const videoProviders = providers.filter((item) => item.media_types.includes('video'))
  return (
    <div className="provider-picker">
      <span className="field-label">视频模型</span>
      <div className="provider-options">
        {videoProviders.map((item) => {
          const moduleId = moduleIdForProvider(item.id, modules) || item.module_id
          return (
            <button
              type="button"
              key={item.id}
              className={classNames('provider-option', provider === item.id && 'is-selected', !item.available && 'is-unavailable')}
              onClick={() => item.available && onChange(item.id)}
              disabled={!item.available}
              title={item.available ? item.name || item.id : item.reason}
            >
              <span className="provider-radio" />
              <span className="provider-option-label"><strong>{moduleModelLabel(modules.find((module) => module.id === moduleId), modelCatalog?.[moduleId])}</strong><small>{item.name || item.id}</small></span>
              {!item.available && <small>未配置</small>}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function ImageProviderPicker({ provider, providers, modules = [], modelCatalog, onChange, label = '图片 Provider', allowedProviders }) {
  const imageProviders = providers.filter((item) => item.media_types.includes('image') && (!allowedProviders || allowedProviders.includes(item.id)))
  return <div className="provider-picker">
    <span className="field-label">{label}</span>
    <div className="provider-options">
      {imageProviders.map((item) => {
        const moduleId = moduleIdForProvider(item.id, modules) || item.module_id
        return <button type="button" key={item.id} className={classNames('provider-option', provider === item.id && 'is-selected', !item.available && 'is-unavailable')} onClick={() => item.available && onChange(item.id)} disabled={!item.available} title={item.available ? item.name || item.id : item.reason}>
          <span className="provider-radio" /><span className="provider-option-label"><strong>{moduleModelLabel(modules.find((module) => module.id === moduleId), modelCatalog?.[moduleId])}</strong><small>{item.name || item.id}</small></span>{!item.available && <small>未配置</small>}
        </button>
      })}
    </div>
  </div>
}

const API_CATEGORY_META = {
  image: { label: '图片生成模型', description: '管理用于商品图、参考图和批量出图的模型', icon: ImageIcon },
  video: { label: '视频生成模型', description: '管理文生视频、图生视频和短片生成模型', icon: Clapperboard },
  mix_planner: { label: '智能混剪模型', description: '管理素材分析、节奏规划与剪辑计划模型', icon: WandSparkles },
}

function apiCategoryMeta(category) {
  return API_CATEGORY_META[category] || { label: category || '其他模型', description: '管理此能力所需的模型连接', icon: Settings }
}

function ApiSettingsDialog({ open, modules, onClose, onSave, onTest, onDiscover, onCreate, onDelete }) {
  const [selectedId, setSelectedId] = useState('')
  const [activeCategory, setActiveCategory] = useState('image')
  const [drafts, setDrafts] = useState({})
  const [busy, setBusy] = useState('')
  const [messages, setMessages] = useState({})
  const [createForm, setCreateForm] = useState({ name: '', api_url: '', api_key: '', model: '' })
  const [createDetection, setCreateDetection] = useState(null)
  const dialogRef = useDialogFocus(open, onClose)
  useEffect(() => {
    if (!open) return
    setDrafts((current) => {
      const next = { ...current }
      for (const module of modules) {
        if (!next[module.id]) next[module.id] = { ...module, api_key: '', clear_api_key: false, options: { ...(module.options || {}) } }
      }
      for (const id of Object.keys(next)) {
        if (!modules.some((module) => module.id === id)) delete next[id]
      }
      return next
    })
    setSelectedId((current) => modules.some((module) => module.id === current) ? current : (modules[0]?.id || ''))
    setActiveCategory((current) => API_CATEGORY_META[current] ? current : (modules[0]?.category || 'image'))
  }, [open, modules])
  useEffect(() => {
    if (!open) {
      setCreateForm({ name: '', api_url: '', api_key: '', model: '' })
      setCreateDetection(null)
      setMessages({})
    }
  }, [open])
  if (!open) return null
  const groupedModules = modules.reduce((groups, module) => {
    const category = module.category || 'other'
    if (!groups[category]) groups[category] = []
    groups[category].push(module)
    return groups
  }, {})
  const categories = [...Object.keys(API_CATEGORY_META), ...Object.keys(groupedModules).filter((category) => !API_CATEGORY_META[category])]
  const selectedCategory = API_CATEGORY_META[activeCategory] ? activeCategory : (categories[0] || 'image')
  const categoryModules = groupedModules[selectedCategory] || []
  const selected = drafts[selectedId] && drafts[selectedId].category === selectedCategory ? drafts[selectedId] : null
  const updateCreate = (key, value) => {
    setCreateForm((current) => ({ ...current, [key]: value }))
    if (key === 'api_url' || key === 'api_key') setCreateDetection(null)
  }
  const handleDetectCreate = async () => {
    setBusy('detect')
    setMessages((current) => ({ ...current, create: '' }))
    try {
      const detected = await detectGateway({
        api_url: createForm.api_url.trim(),
        api_key: createForm.api_key,
        category: selectedCategory,
        model: createForm.model.trim(),
      })
      setCreateDetection(detected)
      setCreateForm((current) => ({
        ...current,
        name: current.name.trim() || detected.suggested_name || '',
        model: detected.selected_model || current.model,
      }))
      setMessages((current) => ({ ...current, create: { ok: true, text: detected.message || '已识别生产网关' } }))
    } catch (error) {
      setCreateDetection(null)
      setMessages((current) => ({ ...current, create: { ok: false, text: error.message } }))
    } finally { setBusy('') }
  }
  const handleCreate = async () => {
    if (!createDetection) return
    setBusy('create')
    setMessages((current) => ({ ...current, create: '' }))
    try {
      const created = await onCreate({
        name: createForm.name.trim() || createDetection.suggested_name,
        slug: createDetection.suggested_slug,
        category: selectedCategory,
        protocol: createDetection.protocol,
        api_url: createForm.api_url.trim(),
        api_key: createForm.api_key || undefined,
        model: createForm.model.trim() || createDetection.selected_model,
        enabled: true,
      })
      setCreateForm({ name: '', api_url: '', api_key: '', model: '' })
      setCreateDetection(null)
      setSelectedId(created.id)
      setMessages((current) => ({ ...current, [created.id]: { ok: true, text: '自定义模块已添加' } }))
    } catch (error) {
      setMessages((current) => ({ ...current, create: { ok: false, text: error.message } }))
    } finally { setBusy('') }
  }
  const handleDelete = async () => {
    if (!selected || selected.kind !== 'custom') return
    if (!window.confirm(`删除自定义模块「${selected.name}」？此操作不可恢复。`)) return
    setBusy('delete')
    try {
      await onDelete(selected.id)
      setMessages({})
    } catch (error) {
      setMessages((current) => ({ ...current, [selected.id]: { ok: false, text: error.message } }))
    } finally { setBusy('') }
  }
  const update = (key, value) => setDrafts((current) => {
    const selectedDraft = current[selectedId]
    if (!selectedDraft) return current
    const invalidatesCatalog = key === 'api_url' || key === 'api_key' || key === 'model' || key === 'clear_api_key'
    return {
      ...current,
      [selectedId]: {
        ...selectedDraft,
        [key]: value,
        ...(invalidatesCatalog ? { discovered_models: [] } : {}),
      },
    }
  })
  const action = async (kind) => {
    if (!selected) return
    setBusy(kind)
    setMessages((current) => ({ ...current, [selectedId]: '' }))
    try {
      const payload = { api_url: selected.api_url || '', api_key: selected.api_key || undefined, clear_api_key: Boolean(selected.clear_api_key), model: selected.model || '', enabled: selected.enabled !== false, options: selected.options || {} }
      const result = kind === 'test'
        ? await onTest(selectedId, payload)
        : kind === 'discover'
          ? await onDiscover(selectedId, payload)
          : await onSave(selectedId, payload)
      if (kind === 'save') setDrafts((current) => ({ ...current, [selectedId]: { ...result, api_key: '', clear_api_key: false, options: { ...(result.options || {}) } } }))
      if (kind === 'discover') {
        const discoveredModels = result.models || []
        setDrafts((current) => ({
          ...current,
          [selectedId]: {
            ...current[selectedId],
            model: result.selected_model || current[selectedId].model || '',
            discovered_models: discoveredModels,
          },
        }))
      }
      setMessages((current) => ({ ...current, [selectedId]: { ok: true, text: result.message || (kind === 'test' ? '连接成功' : kind === 'discover' ? '模型识别完成' : '配置已保存') } }))
    } catch (error) {
      setMessages((current) => ({ ...current, [selectedId]: { ok: false, text: error.message } }))
    } finally { setBusy('') }
  }
  const discoveredModels = selected?.discovered_models || []
  const createModels = createDetection?.models || []
  const moduleMessage = selected ? messages[selectedId] : null
  const createMessage = messages.create
  const statusText = moduleMessage ? (moduleMessage.ok ? '连接成功' : '连接失败') : selected?.api_key_configured ? '已配置' : '未配置'
  const tabId = (id) => `api-tab-${id.replace(/[^a-z0-9_-]/gi, '-')}`
  const panelId = (id) => `api-panel-${id.replace(/[^a-z0-9_-]/gi, '-')}`
  const handleTabKeyDown = (event) => {
    const currentIndex = categoryModules.findIndex((module) => module.id === selectedId)
    if (currentIndex < 0 || !categoryModules.length) return
    let nextIndex = currentIndex
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % categoryModules.length
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + categoryModules.length) % categoryModules.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = categoryModules.length - 1
    else return
    event.preventDefault()
    setSelectedId(categoryModules[nextIndex].id)
    event.currentTarget.parentElement?.querySelectorAll('[role="tab"]')[nextIndex]?.focus()
  }
  const categoryMeta = apiCategoryMeta(selectedCategory)
  const CategoryIcon = categoryMeta.icon
  const handleCategoryKeyDown = (event) => {
    const currentIndex = categories.indexOf(selectedCategory)
    if (currentIndex < 0) return
    let nextIndex = currentIndex
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % categories.length
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + categories.length) % categories.length
    else if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = categories.length - 1
    else return
    event.preventDefault()
    const nextCategory = categories[nextIndex]
    setActiveCategory(nextCategory)
    const nextModule = groupedModules[nextCategory]?.[0]
    if (nextModule) setSelectedId(nextModule.id)
    setCreateDetection(null)
    event.currentTarget.parentElement?.querySelectorAll('[role="tab"]')[nextIndex]?.focus()
  }

  return <div ref={dialogRef} tabIndex={-1} className="prompt-sheet" role="dialog" aria-modal="true" aria-labelledby="api-settings-title">
    <div className="prompt-dialog api-settings-dialog">
      <div className="dialog-heading"><div><h2 id="api-settings-title">API 配置</h2><p>粘贴生产网关的 API 地址和密钥即可自动匹配协议。密钥仅发送给本地 AIGC 服务。</p></div><button className="icon-button" type="button" onClick={onClose} title="关闭" aria-label="关闭 API 配置"><X size={17} /></button></div>
      <div className="api-category-tabs" role="tablist" aria-label="模型能力分类">
        {categories.map((category) => {
          const meta = apiCategoryMeta(category)
          const Icon = meta.icon
          const categoryModulesForTab = groupedModules[category] || []
          const count = categoryModulesForTab.length
          const ready = categoryModulesForTab.filter((module) => drafts[module.id]?.api_key_configured).length
          const categoryId = `api-category-${category.replace(/[^a-z0-9_-]/gi, '-')}`
          return <button key={category} id={categoryId} type="button" role="tab" aria-controls="api-category-panel" aria-selected={selectedCategory === category} className={classNames('api-category-tab', selectedCategory === category && 'is-selected')} onClick={() => { setActiveCategory(category); if (categoryModulesForTab[0]) setSelectedId(categoryModulesForTab[0].id); setCreateDetection(null) }} onKeyDown={handleCategoryKeyDown}><span className="api-category-icon"><Icon size={17} /></span><span className="api-category-copy"><strong>{meta.label}</strong><small>{count ? `${ready}/${count} 已配置` : '可新增模块'}</small></span></button>
        })}
      </div>
      <section id="api-category-panel" role="tabpanel" aria-labelledby={`api-category-${selectedCategory.replace(/[^a-z0-9_-]/gi, '-')}`}>
        <div className="api-category-heading"><div><div className="api-category-kicker"><CategoryIcon size={15} />{categoryMeta.label}</div><p>{categoryMeta.description}</p></div><span>{categoryModules.length} 个模块</span></div>
        <div className="api-settings-body">
          <div className="api-module-list" role="tablist" aria-label={`${categoryMeta.label}模块`}>
            {categoryModules.map((module) => <button key={module.id} id={tabId(module.id)} type="button" role="tab" aria-controls={panelId(module.id)} aria-selected={selectedId === module.id} tabIndex={selectedId === module.id ? 0 : -1} className={classNames('api-module-tab', selectedId === module.id && 'is-selected')} onClick={() => setSelectedId(module.id)} onKeyDown={handleTabKeyDown}><span>{module.name}</span><small><span className={classNames('api-module-dot', drafts[module.id]?.api_key_configured && 'is-ready')} />{drafts[module.id]?.api_key_configured ? '已配置' : '未配置'}</small></button>)}
            <div className="api-create-module">
              <strong>新增自定义模块</strong>
              <p className="field-hint">只需填写 API 地址和密钥，系统会识别生产网关并列出可用模型。</p>
              <InputField label="API 地址" value={createForm.api_url} onChange={(value) => updateCreate('api_url', value)} placeholder="https://ark.cn-beijing.volces.com/api/v3" required />
              <InputField type="password" autoComplete="new-password" label="API Key" value={createForm.api_key} onChange={(value) => updateCreate('api_key', value)} placeholder="粘贴 API Key" required />
              <button className="secondary-button" type="button" onClick={handleDetectCreate} disabled={Boolean(busy) || !createForm.api_url.trim() || !createForm.api_key.trim()}>{busy === 'detect' ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{busy === 'detect' ? '正在识别' : '识别网关'}</button>
              {createDetection && <div className="api-detect-result">
                <p className="api-detect-protocol">{createDetection.label || createDetection.protocol}</p>
                {createModels.length > 0 ? <label className="field model-select-field"><span className="field-label">可用模型</span><select value={createForm.model || ''} onChange={(event) => updateCreate('model', event.target.value)}>{createForm.model && !createModels.some((model) => model.id === createForm.model) && <option value={createForm.model}>{createForm.model}</option>}{createModels.map((model) => <option key={model.id} value={model.id}>{model.name === model.id ? model.id : `${model.name} (${model.id})`}</option>)}</select></label> : <InputField label="模型 ID" value={createForm.model} onChange={(value) => updateCreate('model', value)} placeholder="网关未返回目录时手动填写" />}
                <InputField label="显示名称（可选）" value={createForm.name} onChange={(value) => updateCreate('name', value)} placeholder={createDetection.suggested_name || '自动命名'} />
              </div>}
              {createMessage && <p className={classNames('dialog-warning', !createMessage.ok && 'is-error')} role="status"><CircleAlert size={16} />{createMessage.text}</p>}
              <button className="secondary-button" type="button" onClick={handleCreate} disabled={Boolean(busy) || !createDetection || !createForm.api_url.trim() || !createForm.api_key.trim() || !(createForm.model || '').trim()}>{busy === 'create' ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}{busy === 'create' ? '正在添加' : '添加模块'}</button>
            </div>
          </div>
          {selected ? <section id={panelId(selectedId)} role="tabpanel" aria-labelledby={tabId(selectedId)} className="api-module-form">
          <div className="api-module-title"><div><h3>{selected.name}</h3><p>{selected.id} · {selected.kind === 'custom' ? '自定义' : '内置'} · {(selected.capabilities || []).join(' · ') || '模型连接'}</p></div><span className={classNames('technical-status', moduleMessage?.ok || (!moduleMessage && selected.api_key_configured) ? 'is-passed' : 'is-failed')}>{statusText}</span></div>
          <div className="settings-grid">
            <InputField label="API 地址" value={selected.api_url || ''} onChange={(value) => update('api_url', value)} placeholder="https://api.example.com/v1" required />
            <div className="model-config-field">
              {discoveredModels.length > 0 && <label className="field model-select-field"><span className="field-label">可用模型</span><select value={selected.model || ''} onChange={(event) => update('model', event.target.value)}>{selected.model && !discoveredModels.some((model) => model.id === selected.model) && <option value={selected.model}>{selected.model}</option>}{discoveredModels.map((model) => <option key={model.id} value={model.id}>{model.name === model.id ? model.id : `${model.name} (${model.id})`}</option>)}</select></label>}
              <InputField label={discoveredModels.length ? '模型 ID（可手动修改）' : '模型 ID'} value={selected.model || ''} onChange={(value) => update('model', value)} placeholder="输入模型 ID" />
              <button className="secondary-button discover-models-button" type="button" onClick={() => action('discover')} disabled={Boolean(busy)}>{busy === 'discover' ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{busy === 'discover' ? '正在识别' : '自动识别模型'}</button>
            </div>
            <InputField type="password" autoComplete="new-password" label="API Key" value={selected.api_key || ''} onChange={(value) => update('api_key', value)} placeholder={selected.api_key_configured ? '已配置，留空保持不变' : '粘贴 API Key'} />
            <label className="checkbox-field api-enabled"><input type="checkbox" checked={selected.enabled !== false} onChange={(event) => update('enabled', event.target.checked)} /> <span>启用此模块</span></label>
          </div>
          <label className="checkbox-field"><input type="checkbox" checked={selected.clear_api_key || false} onChange={(event) => update('clear_api_key', event.target.checked)} /> <span>清除已保存的 Key</span></label>
          {moduleMessage && <p className={classNames('dialog-warning', !moduleMessage.ok && 'is-error')} role="status"><CircleAlert size={16} />{moduleMessage.text}</p>}
            <div className="dialog-actions"><button className="secondary-button" type="button" onClick={onClose} disabled={Boolean(busy)}>取消</button>{selected.kind === 'custom' && <button className="secondary-button danger-button" type="button" onClick={handleDelete} disabled={Boolean(busy)}>{busy === 'delete' ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}{busy === 'delete' ? '正在删除' : '删除模块'}</button>}<button className="secondary-button" type="button" onClick={() => action('test')} disabled={Boolean(busy)}>{busy === 'test' ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}测试连接</button><button className="primary-button" type="button" onClick={() => action('save')} disabled={Boolean(busy)}>{busy === 'save' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}保存配置</button></div>
          </section> : <section className="api-module-form"><p className="field-hint">选择左侧模块编辑配置，或粘贴 URL 和密钥新增一个生产网关。</p></section>}
        </div>
      </section>
    </div>
  </div>
}


function BatchDialog({ open, form, assets, onClose, onCreate }) {
  const [prompt, setPrompt] = useState(form.prompt || form.product || '')
  const [aspectRatio, setAspectRatio] = useState('square')
  const [selected, setSelected] = useState([])
  const [count, setCount] = useState(3)
  const [message, setMessage] = useState('')
  const [creating, setCreating] = useState(false)
  const [idempotencyKey, setIdempotencyKey] = useState('')
  const dialogRef = useDialogFocus(open, onClose)
  useEffect(() => { if (open) { setPrompt(form.prompt || form.product || ''); setSelected([]); setMessage(''); setCreating(false); setIdempotencyKey(globalThis.crypto?.randomUUID?.() || `batch-${Date.now()}`) } }, [open, form.prompt, form.product])
  if (!open) return null
  const imageAssets = assets.filter((asset) => asset.media_type === 'image')
  const create = async () => {
    if (!prompt.trim()) { setMessage('请先填写 Prompt'); return }
    setCreating(true)
    try { await onCreate({ prompt, aspectRatio, selected, count, idempotencyKey }); onClose() } catch (error) { setMessage(error.message); setCreating(false) }
  }
  return <div ref={dialogRef} tabIndex={-1} className="prompt-sheet" role="dialog" aria-modal="true" aria-label="批量生成">
    <div className="prompt-dialog batch-dialog">
      <div className="dialog-heading"><div><h2>批量生成图片</h2><p>同一 Prompt 可结合不同本地素材批量生成。</p></div><button className="icon-button" type="button" onClick={onClose} title="关闭"><X size={17} /></button></div>
      <InputField label="Prompt 模板" value={prompt} onChange={setPrompt} textarea placeholder="描述你要生成的画面" required />
      <div className="field-grid"><label className="field"><span className="field-label">生成数量</span><input type="number" min="1" max="20" value={count} onChange={(event) => setCount(Math.max(1, Math.min(20, Number(event.target.value) || 1)))} /></label><label className="field"><span className="field-label">画幅</span><select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}><option value="square">1:1</option><option value="portrait">竖版</option><option value="landscape">横版</option></select></label></div>
      <div className="asset-select-list"><span className="field-label">复用本地图片（可多选）</span>{imageAssets.length ? imageAssets.slice(0, 80).map((asset) => <label key={asset.id} className="asset-select-item"><input type="checkbox" checked={selected.includes(asset.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, asset.id].slice(0, 16) : current.filter((id) => id !== asset.id))} /><img src={asset.url} alt="" /><span>{asset.name}</span></label>) : <p className="field-hint">先在素材库导入图片。</p>}</div>
      {message && <p className="form-error" role="alert"><CircleAlert size={16} />{message}</p>}
      <div className="dialog-actions"><button className="secondary-button" type="button" onClick={onClose} disabled={creating}>取消</button><button className="primary-button" type="button" onClick={create} disabled={creating}>{creating ? <LoaderCircle className="spin" size={16} /> : <Layers3 size={16} />}{creating ? '正在创建' : '创建批量任务'}</button></div>
    </div>
  </div>
}

function MixDialog({ open, assets, modules = [], onClose, onPlan, onCreate }) {
  const [selected, setSelected] = useState([])
  const [aspectRatio, setAspectRatio] = useState('portrait')
  const [imageDuration, setImageDuration] = useState(3)
  const [objective, setObjective] = useState('节奏紧凑、突出主体的短视频')
  const [transitionMode, setTransitionMode] = useState('auto')
  const [targetDuration, setTargetDuration] = useState(15)
  const [plan, setPlan] = useState(null)
  const [message, setMessage] = useState('')
  const [creating, setCreating] = useState(false)
  const dialogRef = useDialogFocus(open, onClose)
  useEffect(() => { if (open) { setSelected([]); setMessage(''); setCreating(false); setPlan(null); setObjective('节奏紧凑、突出主体的短视频'); setTransitionMode('auto'); setTargetDuration(15) } }, [open])
  if (!open) return null
  const resetPlan = () => { setPlan(null); setMessage('') }
  const toggle = (id, checked) => { resetPlan(); setSelected((current) => checked ? [...current, id].slice(0, 50) : current.filter((item) => item !== id)) }
  const clips = selected.map((assetId) => ({ asset_id: assetId, duration_ms: assets.find((asset) => asset.id === assetId)?.media_type === 'image' ? imageDuration * 1000 : null }))
  const preview = async () => {
    if (selected.length < 2) { setMessage('至少选择两个图片或视频素材'); return }
    setCreating(true)
    try {
      setPlan(await onPlan({ clips, objective, targetDuration, transitionMode }))
    } catch (error) { setMessage(error.message); setCreating(false) }
    finally { setCreating(false) }
  }
  const accept = async () => {
    if (!plan) return
    setCreating(true)
    try { await onCreate({ clips, aspectRatio, objective, transitionMode, plan }); onClose() } catch (error) { setMessage(error.message) } finally { setCreating(false) }
  }
  const plannerModule = mixPlannerModule(modules)
  const plannerName = plan?.planner === 'codex_terra' ? (plannerModule?.name || 'Codex 5.6 Terra') : '本地规则'
  const durationLabel = (clip) => `${Math.round(((clip.end_ms ? clip.end_ms - clip.start_ms : clip.duration_ms) || 0) / 100) / 10} 秒`
  return <div ref={dialogRef} tabIndex={-1} className="prompt-sheet" role="dialog" aria-modal="true" aria-label="智能混剪">
    <div className="prompt-dialog batch-dialog">
      <div className="dialog-heading"><div><h2>智能混剪</h2><p>根据目标与素材顺序生成剪辑计划，再由本地 FFmpeg 执行。{plannerModule ? `当前规划器：${plannerModule.name}` : '未配置智能规划时将使用本地规则。'}</p></div><button className="icon-button" type="button" onClick={onClose} title="关闭"><X size={17} /></button></div>
      <InputField label="剪辑目标" value={objective} onChange={(value) => { resetPlan(); setObjective(value) }} placeholder="例如：节奏紧凑，突出新品细节" />
      <div className="field-grid"><label className="field"><span className="field-label">画幅</span><select value={aspectRatio} onChange={(event) => { resetPlan(); setAspectRatio(event.target.value) }}><option value="portrait">9:16 竖版</option><option value="landscape">16:9 横版</option><option value="square">1:1 方形</option></select></label><label className="field"><span className="field-label">图片时长（秒）</span><input type="number" min="1" max="60" value={imageDuration} onChange={(event) => { resetPlan(); setImageDuration(Math.max(1, Math.min(60, Number(event.target.value) || 3))) }} /></label><label className="field"><span className="field-label">目标时长（秒）</span><input type="number" min="1" max="300" value={targetDuration} onChange={(event) => { resetPlan(); setTargetDuration(Math.max(1, Math.min(300, Number(event.target.value) || 15))) }} /></label><label className="field"><span className="field-label">转场策略</span><select value={transitionMode} onChange={(event) => { resetPlan(); setTransitionMode(event.target.value) }}><option value="auto">自动规划</option><option value="hard_cut">硬切</option><option value="fade">淡入淡出（不可用时回退硬切）</option></select></label></div>
      <div className="asset-select-list"><span className="field-label">素材顺序：{selected.length ? selected.map((id) => assets.find((asset) => asset.id === id)?.name).join(' → ') : '尚未选择'}</span>{assets.length ? assets.slice(0, 100).map((asset) => <label key={asset.id} className="asset-select-item"><input type="checkbox" checked={selected.includes(asset.id)} onChange={(event) => toggle(asset.id, event.target.checked)} />{asset.media_type === 'video' ? <video src={asset.url} muted preload="metadata" /> : <img src={asset.url} alt="" />}<span>{asset.name}</span></label>) : <p className="field-hint">暂无可用素材。请先导入图片或视频，再选择至少两个素材生成计划。</p>}</div>
      {plan && <section className="mix-plan-preview" aria-label="智能混剪计划预览"><div className="api-module-title"><div><h3>剪辑计划预览</h3><p>{plannerName}，共 {Math.round((plan.target_duration_ms || 0) / 100) / 10} 秒</p></div><span className="technical-status is-passed">{plannerName}</span></div>{plan.planner !== 'codex_terra' && <p className="field-hint">当前使用确定性的本地规则，按素材顺序生成计划；无需 Codex 5.6 Terra 也可继续。</p>}<ol>{plan.clips?.map((clip, index) => <li key={`${clip.asset_id}-${index}`}><strong>{index + 1}. {assets.find((asset) => asset.id === clip.asset_id)?.name || clip.asset_id}</strong><span>{durationLabel(clip)} · {clip.transition === 'hard_cut' ? '硬切' : clip.transition}</span></li>)}</ol>{plan.warnings?.map((warning, index) => <p className="dialog-warning" key={`${warning}-${index}`}><CircleAlert size={16} />{warning}</p>)}</section>}
      {message && <p className="form-error" role="alert"><CircleAlert size={16} />{message}</p>}
      <div className="dialog-actions"><button className="secondary-button" type="button" onClick={onClose} disabled={creating}>取消</button>{plan && <button className="secondary-button" type="button" onClick={() => { setPlan(null); setMessage('') }} disabled={creating}>重新规划</button>}<button className="primary-button" type="button" onClick={plan ? accept : preview} disabled={creating}>{creating ? <LoaderCircle className="spin" size={16} /> : <Clapperboard size={16} />}{creating ? '正在处理' : plan ? '确认并开始智能混剪' : '生成计划预览'}</button></div>
    </div>
  </div>
}

function BackgroundTasks({ batch, mix }) {
  if (!batch && !mix) return null
  return <section className="background-tasks" aria-live="polite">
    {batch && <div><Layers3 size={16} /><span><strong>批量任务</strong>{batch.status === 'running' ? ` ${batch.items?.filter((item) => item.status === 'succeeded').length || 0}/${batch.items?.length || batch.total}` : ` ${batch.status}`}</span></div>}
    {mix && <div><Clapperboard size={16} /><span><strong>智能混剪</strong> {mix.phase || mix.status}</span>{mix.output && <a href={mix.output} download>下载</a>}</div>}
  </section>
}

function ReferenceImage({ form, assets, onChange, onImport }) {
  const imageAssets = assets.filter((asset) => asset.media_type === 'image')
  const r2ImageAssets = imageAssets.filter((asset) => asset.r2_url)
  const [uploading, setUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState('')
  const handleLocalUpload = async (event) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length) return
    setUploading(true)
    setUploadMessage('')
    try {
      const imported = await onImport(files)
      const firstImage = imported?.find((asset) => asset.media_type === 'image')
      if (!firstImage) throw new Error('未找到可用的图片文件')
      onChange('referenceKind', 'asset')
      onChange('reference', firstImage.id)
    } catch (uploadError) {
      setUploadMessage(uploadError.message)
    } finally {
      setUploading(false)
    }
  }
  return (
    <section className="field-section reference-section">
      <div className="section-heading-row">
        <div>
          <h3>参考图</h3>
          <p>可选，用于图生视频</p>
        </div>
      </div>
      <div className="reference-toggle" role="group" aria-label="参考图来源">
        <button type="button" className={classNames(form.referenceKind === 'url' && 'is-selected')} onClick={() => onChange('referenceKind', 'url')}>公网链接</button>
        <button type="button" className={classNames(form.referenceKind === 'r2' && 'is-selected')} onClick={() => onChange('referenceKind', 'r2')}>R2 已上传</button>
        <button type="button" className={classNames(form.referenceKind === 'asset' && 'is-selected')} onClick={() => onChange('referenceKind', 'asset')}>本地输出</button>
      </div>
      {form.referenceKind === 'asset' ? (
        <>
          <label className="field">
            <span className="field-label">本地图片</span>
            <select value={form.reference} onChange={(event) => onChange('reference', event.target.value)}>
              <option value="">选择一张已有图片</option>
              {imageAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
            </select>
            <span className="field-hint">可选择素材库中的图片，或直接导入一张本地图片。</span>
          </label>
          <label className="secondary-button media-import-button reference-local-upload">
            <Images size={16} />{uploading ? '正在导入' : '选择本地图片'}
            <input type="file" hidden accept="image/*" disabled={uploading} onChange={handleLocalUpload} />
          </label>
        </>
      ) : form.referenceKind === 'r2' ? (
        <label className="field">
          <span className="field-label">R2 公网图片</span>
          <select value={form.reference} onChange={(event) => onChange('reference', event.target.value)}>
            <option value="">{r2ImageAssets.length ? '选择已上传图片' : '暂无已上传图片'}</option>
            {r2ImageAssets.map((asset) => <option key={asset.id} value={asset.r2_url}>{asset.name}</option>)}
          </select>
          <span className="field-hint">使用 Cloudflare R2 的公网 HTTPS URL，适用于支持图生视频的模型。</span>
        </label>
      ) : (
        <InputField
          label="图片 URL"
          value={form.reference}
          onChange={(value) => onChange('reference', value)}
          placeholder="https://example.com/product.jpg"
          hint={form.provider === 'seedance' ? '当前模型接收公网 HTTPS 图片 URL，也支持直接导入本地图片。' : '当前模型支持公网链接、R2 图片或本地输出。'}
        />
      )}
      {uploadMessage && <p className="form-error" role="alert"><CircleAlert size={16} />{uploadMessage}</p>}
    </section>
  )
}

function OutfitSwapPicker({ form, assets, onChange }) {
  const imageAssets = assets.filter((asset) => asset.media_type === 'image')
  const modelImage = typeof form.outfitModelImage === 'string' ? form.outfitModelImage : ''
  const outfitImages = Array.isArray(form.outfitImages) ? form.outfitImages : []
  const availableOutfits = imageAssets.filter((asset) => asset.id !== modelImage)
  const setModelImage = (assetId) => {
    onChange('outfitModelImage', assetId)
    if (outfitImages.includes(assetId)) onChange('outfitImages', outfitImages.filter((id) => id !== assetId))
  }
  const toggleOutfit = (assetId, checked) => onChange(
    'outfitImages',
    checked
      ? [...outfitImages.filter((id) => id !== assetId), assetId].slice(0, 9)
      : outfitImages.filter((id) => id !== assetId),
  )
  const assetButton = (asset, role, selected, onSelect, disabled = false) => (
    <button key={`${role}-${asset.id}`} type="button" className={classNames('outfit-asset-button', selected && 'is-selected')} aria-pressed={selected} onClick={onSelect} disabled={disabled}>
      <img src={asset.url} alt="" />
      <span><strong>{asset.name}</strong><small>{selected ? (role === 'model' ? '已设为模特主图' : '已选服装图') : (role === 'model' ? '选择为模特主图' : '选择为服装图')}</small></span>
      {selected && <Check size={16} aria-hidden="true" />}
    </button>
  )
  return (
    <section className="field-section outfit-swap-picker" aria-labelledby="outfit-swap-title">
      <div className="section-heading-row">
        <div>
          <h3 id="outfit-swap-title">选择换装素材</h3>
          <p>先选一张模特主图，再选择需要换上的服装图。</p>
        </div>
        <span className={classNames('reference-count', modelImage && outfitImages.length && 'is-ready')}>{modelImage ? 1 : 0} + {outfitImages.length}</span>
      </div>
      {imageAssets.length ? (
        <div className="outfit-role-groups">
          <section className="outfit-role-group" aria-labelledby="model-image-title">
            <div className="outfit-role-heading"><span className="outfit-step">1</span><div><h4 id="model-image-title">模特主图</h4><p>只能选择一张，人物、姿势和背景保持不变。</p></div></div>
            <div className="outfit-asset-list" role="group" aria-label="选择模特主图">{imageAssets.slice(0, 80).map((asset) => assetButton(asset, 'model', modelImage === asset.id, () => setModelImage(asset.id)))}</div>
          </section>
          <section className="outfit-role-group" aria-labelledby="outfit-images-title">
            <div className="outfit-role-heading"><span className="outfit-step">2</span><div><h4 id="outfit-images-title">服装图</h4><p>可多选；所有图片应是同一件服装的不同视角或细节，最多 9 张。</p></div></div>
            {modelImage ? <div className="outfit-asset-list" role="group" aria-label="选择服装图">{availableOutfits.slice(0, 80).map((asset) => assetButton(asset, 'outfit', outfitImages.includes(asset.id), () => toggleOutfit(asset.id, !outfitImages.includes(asset.id)), outfitImages.length >= 9 && !outfitImages.includes(asset.id)))}</div> : <p className="outfit-empty-state">请先完成第 1 步，选择模特主图。</p>}
          </section>
        </div>
      ) : (
        <p className="outfit-empty-state">暂无本地图片。请先点击下方“导入图片”添加模特和服装素材。</p>
      )}
    </section>
  )
}

function ClothingReferencePicker({ form, assets, onChange }) {
  const imageAssets = assets.filter((asset) => asset.media_type === 'image')
  const selected = Array.isArray(form.referenceImages) ? form.referenceImages[0] || '' : ''
  return (
    <section className="field-section reference-section image-reference-section clothing-reference-section">
      <div className="section-heading-row">
        <div>
          <h3>服装基准图</h3>
          <p>只选择一张服装图片。生成时锁定颜色、面料、工艺和全部可见细节。</p>
        </div>
        <span className={classNames('reference-count', selected && 'is-ready')}>{selected ? '1/1' : '0/1'}</span>
      </div>
      {imageAssets.length ? (
        <div className="asset-select-list image-reference-list" aria-label="选择服装基准图">
          {imageAssets.slice(0, 80).map((asset) => (
            <label key={asset.id} className={classNames('asset-select-item', 'reference-asset-item', selected === asset.id && 'is-selected')}>
              <input type="radio" name="clothing-reference" checked={selected === asset.id} onChange={() => onChange('referenceImages', [asset.id])} />
              <img src={asset.url} alt="" />
              <span className="reference-asset-copy"><strong>{asset.name}</strong>{selected === asset.id && <small>服装唯一基准</small>}</span>
            </label>
          ))}
        </div>
      ) : (
        <p className="field-hint">暂无本地图片。请先通过“导入文件夹”添加服装图片。</p>
      )}
      {!selected && <p className="field-hint reference-warning">请先选择一张服装基准图。</p>}
    </section>
  )
}

function ShapewearProductReferencePicker({ form, assets, onChange }) {
  const imageAssets = assets.filter((asset) => asset.media_type === 'image')
  const selected = Array.isArray(form.referenceImages) ? form.referenceImages : []
  const toggleReference = (assetId, checked) => {
    const next = checked
      ? [...selected.filter((id) => id !== assetId), assetId].slice(0, 10)
      : selected.filter((id) => id !== assetId)
    onChange('referenceImages', next)
  }
  return (
    <section className="field-section reference-section image-reference-section shapewear-reference-section" aria-labelledby="shapewear-reference-title">
      <div className="section-heading-row">
        <div>
          <h3 id="shapewear-reference-title">塑身衣商品参考图</h3>
          <p>可选 1–10 张图片；第一张作为主商品图，其余用于锁定面料、工艺和细节。</p>
        </div>
        <span className={classNames('reference-count', selected.length > 0 && 'is-ready')}>{selected.length}/10</span>
      </div>
      {imageAssets.length ? (
        <div className="asset-select-list image-reference-list" role="group" aria-label="选择塑身衣商品参考图">
          {imageAssets.slice(0, 80).map((asset) => {
            const order = selected.indexOf(asset.id)
            const isSelected = order >= 0
            return (
              <label key={asset.id} className={classNames('asset-select-item', 'reference-asset-item', isSelected && 'is-selected')}>
                <input
                  type="checkbox"
                  checked={isSelected}
                  disabled={!isSelected && selected.length >= 10}
                  onChange={(event) => toggleReference(asset.id, event.target.checked)}
                />
                <img src={asset.url} alt="" />
                <span className="reference-asset-copy">
                  <strong>{asset.name}</strong>
                  <small>{isSelected ? `${order === 0 ? '主商品图' : '细节/角度参考'} · 顺序 ${order + 1}` : '点击加入参考图'}</small>
                </span>
              </label>
            )
          })}
        </div>
      ) : (
        <p className="outfit-empty-state">暂无图片素材。请先通过“导入文件夹”添加塑身衣商品图。</p>
      )}
      <p className="field-hint">未选择参考图时仍可使用文字需求生成；选择后系统会按顺序还原商品外观和结构细节。</p>
    </section>
  )
}

function TikTokClothingReferencePicker({ form, assets, onChange }) {
  const imageAssets = assets.filter((asset) => asset.media_type === 'image')
  const selected = Array.isArray(form.referenceImages) ? form.referenceImages : []
  const masterUrl = (form.tiktokMasterUrl || '').trim()
  const detailUrls = (form.tiktokDetailUrls || '')
    .split(/[\n,]+/)
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 9)
  const totalReferences = (masterUrl ? 1 : 0) + detailUrls.length + selected.length
  const toggleReference = (assetId, checked) => {
    const next = checked
      ? [...selected.filter((id) => id !== assetId), assetId].slice(0, 10)
      : selected.filter((id) => id !== assetId)
    onChange('referenceImages', next)
  }
  const promoteReference = (assetId) => onChange('referenceImages', [assetId, ...selected.filter((id) => id !== assetId)])
  return (
    <section className="field-section reference-section image-reference-section tiktok-clothing-reference-section" aria-labelledby="tiktok-clothing-reference-title">
      <div className="section-heading-row">
        <div>
          <h3 id="tiktok-clothing-reference-title">TikTok 商品参考图</h3>
          <p>先绑定一张主图，再补充同款背面、面料、走线和扣件细节图，最多 10 张。</p>
        </div>
        <span className={classNames('reference-count', totalReferences > 0 && 'is-ready')}>{totalReferences}/10</span>
      </div>
      <div className="tiktok-reference-url-fields">
        <InputField
          label="公网主图 URL（可选）"
          value={form.tiktokMasterUrl || ''}
          onChange={(value) => onChange('tiktokMasterUrl', value)}
          placeholder="https://cdn.example.com/product-master.jpg"
          hint="适合直接使用供应商或 CDN 图片；URL 必须是公开 HTTPS 且带图片扩展名。"
        />
        <InputField
          label="公网细节图 URL（可选）"
          value={form.tiktokDetailUrls || ''}
          onChange={(value) => onChange('tiktokDetailUrls', value)}
          placeholder="每行一个 URL，可填写最多 9 张"
          textarea
        />
      </div>
      {selected.length > 0 && !masterUrl && (
        <div className="tiktok-selected-order" aria-label="已选本地主图顺序">
          <div className="tiktok-selected-order-heading"><strong>本地参考图顺序</strong><span>第 1 张自动作为主图</span></div>
          <div className="tiktok-selected-order-list">
            {selected.map((assetId, index) => {
              const asset = imageAssets.find((item) => item.id === assetId)
              if (!asset) return null
              return <div className="tiktok-order-item" key={assetId}>
                <span className="tiktok-order-index">{index + 1}</span>
                <img src={asset.url} alt="" />
                <span>{asset.name}</span>
                {index > 0 && <button type="button" className="icon-button" title="设为主图" aria-label={`将 ${asset.name} 设为主图`} onClick={() => promoteReference(assetId)}><ArrowUp size={14} /></button>}
                <button type="button" className="icon-button" title="移除参考图" aria-label={`移除 ${asset.name}`} onClick={() => toggleReference(assetId, false)}><X size={14} /></button>
              </div>
            })}
          </div>
        </div>
      )}
      {imageAssets.length ? (
        <div className="asset-select-list image-reference-list" role="group" aria-label="选择 TikTok 服装商品参考图">
          {imageAssets.slice(0, 80).map((asset) => {
            const order = selected.indexOf(asset.id)
            const isSelected = order >= 0
            const isMasterFromLocal = !masterUrl && order === 0
            return (
              <label key={asset.id} className={classNames('asset-select-item', 'reference-asset-item', isSelected && 'is-selected')}>
                <input type="checkbox" checked={isSelected} disabled={!isSelected && totalReferences >= 10} onChange={(event) => toggleReference(asset.id, event.target.checked)} />
                <img src={asset.url} alt="" />
                <span className="reference-asset-copy"><strong>{asset.name}</strong><small>{isSelected ? `${isMasterFromLocal ? '商品主图' : '同款细节图'} · 顺序 ${order + 1}` : masterUrl ? '点击加入细节图' : '点击加入参考图'}</small></span>
              </label>
            )
          })}
        </div>
      ) : (
        <p className="outfit-empty-state">暂无图片素材，请先导入商品主图和细节图。</p>
      )}
      {!totalReferences && <p className="field-hint reference-warning">请选择一张商品主图，或粘贴公网主图 URL。</p>}
      {masterUrl && <p className="field-hint reference-ready"><Link2 size={13} />公网主图已绑定；本地图片和其他 URL 将作为同款细节参考。</p>}
    </section>
  )
}

function TikTokClothingFields({ form, onChange }) {
  return (
    <section className="field-section tiktok-clothing-fields" aria-labelledby="tiktok-clothing-fields-title">
      <div className="section-heading-row"><div><h3 id="tiktok-clothing-fields-title">TikTok 服装商品图</h3><p>以主图锁定商品身份，画面重点放在面料、工艺和可验证细节。</p></div></div>
      <div className="field-grid">
        <label className="field"><span className="field-label">使用场景</span><select value={form.tiktokPurpose} onChange={(event) => onChange('tiktokPurpose', event.target.value)}>{TIKTOK_CLOTHING_PURPOSES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
        <label className="field"><span className="field-label">视觉风格</span><select value={form.tiktokStyle} onChange={(event) => onChange('tiktokStyle', event.target.value)}>{TIKTOK_CLOTHING_STYLES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
      </div>
      <div className="field-grid">
        <label className="field"><span className="field-label">画幅</span><select value={form.tiktokAspectRatio} onChange={(event) => onChange('tiktokAspectRatio', event.target.value)}><option value="9:16">9:16 竖版（推荐）</option><option value="portrait">Portrait</option><option value="square">1:1 方形</option><option value="landscape">Landscape</option></select></label>
        <InputField label="目标市场" value={form.tiktokMarket} onChange={(value) => onChange('tiktokMarket', value)} placeholder="US" />
      </div>
      <div className="field-grid">
        <InputField label="语言区域" value={form.tiktokLocale} onChange={(value) => onChange('tiktokLocale', value)} placeholder="en-US" hint="例如 en-US、en-GB、zh-CN" />
        <label className="field"><span className="field-label">人物展示</span><select value={form.tiktokPresentation} onChange={(event) => onChange('tiktokPresentation', event.target.value)}><option value="product_only">仅商品（推荐）</option><option value="adult_model_fully_covered">成年模特 · 完整遮盖</option></select></label>
      </div>
      <InputField label="场景补充" value={form.scene} onChange={(value) => onChange('scene', value)} placeholder="例如：干净中性棚拍，商品完整居中" />
      <InputField label="事实属性（可选）" value={form.tiktokClaims} onChange={(value) => onChange('tiktokClaims', value)} placeholder="仅填写已核实的面料、成分、尺码或洗护信息" />
      <InputField label="展示简述（可选）" value={form.prompt} onChange={(value) => onChange('prompt', value)} placeholder="只描述镜头、光线和展示方式，不要改动服装" textarea />
    </section>
  )
}

function PosterReferencePicker({ form, assets, onChange }) {
  const imageAssets = assets.filter((asset) => asset.media_type === 'image')
  const selected = typeof form.posterReferenceImage === 'string' ? form.posterReferenceImage : ''
  return (
    <section className="field-section reference-section image-reference-section poster-reference-section" aria-labelledby="poster-reference-title">
      <div className="section-heading-row">
        <div>
          <h3 id="poster-reference-title">可选参考图</h3>
          <p>选择一张主体照片，海报会保留其身份与核心内容。</p>
        </div>
        <span className={classNames('reference-count', selected && 'is-ready')}>{selected ? '1/1' : '0/1'}</span>
      </div>
      {imageAssets.length ? (
        <div className="asset-select-list image-reference-list" role="group" aria-label="选择海报参考图">
          <label className={classNames('asset-select-item', 'reference-asset-item', !selected && 'is-selected')}>
            <input type="radio" name="poster-reference" checked={!selected} onChange={() => onChange('posterReferenceImage', '')} />
            <span className="reference-asset-copy"><strong>不使用参考图</strong><small>使用主题锚点生成</small></span>
          </label>
          {imageAssets.slice(0, 80).map((asset) => (
            <label key={asset.id} className={classNames('asset-select-item', 'reference-asset-item', selected === asset.id && 'is-selected')}>
              <input type="radio" name="poster-reference" checked={selected === asset.id} onChange={() => onChange('posterReferenceImage', asset.id)} />
              <img src={asset.url} alt="" />
              <span className="reference-asset-copy"><strong>{asset.name}</strong>{selected === asset.id && <small>主体来源已绑定</small>}</span>
            </label>
          ))}
        </div>
      ) : (
        <p className="field-hint">暂无本地图片。可以直接生成主题海报，或先导入一张参考图。</p>
      )}
    </section>
  )
}

function PosterFields({ form, onChange }) {
  return (
    <section className="field-section poster-fields" aria-labelledby="poster-fields-title">
      <div className="section-heading-row"><div><h3 id="poster-fields-title">海报配方</h3><p>来自已安装的 mono-color 设计系统，配方保持确定性。</p></div></div>
      <InputField label="主题" value={form.posterSubject} onChange={(value) => onChange('posterSubject', value)} placeholder="例如：城市夜行中的一盏路灯" required />
      <InputField label="意图" value={form.posterIntent} onChange={(value) => onChange('posterIntent', value)} placeholder="例如：an observed cultural note" />
      <InputField label="主标题（可选）" value={form.posterText} onChange={(value) => onChange('posterText', value)} placeholder="2–8 words, exact text" />
      <div className="field-grid">
        <label className="field"><span className="field-label">画幅</span><select value={form.posterRatio} onChange={(event) => onChange('posterRatio', event.target.value)}><option value="3:4">3:4 竖版</option><option value="2:3">2:3 竖版</option><option value="4:5">4:5 竖版</option><option value="1:1">1:1 方形</option><option value="4:3">4:3 横版</option></select></label>
        <label className="field"><span className="field-label">视觉张力</span><select value={form.posterTension} onChange={(event) => onChange('posterTension', event.target.value)}><option value="relaxed">Relaxed</option><option value="balanced">Balanced</option><option value="assertive">Assertive</option></select></label>
      </div>
      <label className="field"><span className="field-label">印刷配色</span><select value={form.posterPalette} onChange={(event) => onChange('posterPalette', event.target.value)}>{POSTER_PALETTES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
      <div className="field-grid">
        <label className="field"><span className="field-label">纸张基底</span><select value={form.posterSubstrate} onChange={(event) => onChange('posterSubstrate', event.target.value)}><option value="substrate_neutral_white">Neutral White</option><option value="substrate_cool_gray">Cool Gray</option><option value="substrate_pale_beige">Pale Beige</option></select></label>
        <label className="field"><span className="field-label">版式</span><select value={form.posterLayout} onChange={(event) => onChange('posterLayout', event.target.value)}>{POSTER_LAYOUTS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
      </div>
      <label className="field"><span className="field-label">主体表现</span><select value={form.posterTypeRole} onChange={(event) => onChange('posterTypeRole', event.target.value)}><option value="type_cultural_grotesk">Cultural Grotesk</option><option value="type_literary">Literary</option><option value="type_condensed_civic">Condensed Civic</option><option value="type_programmatic">Programmatic</option><option value="type_rotated_display">Rotated Display</option><option value="type_typographic_object">Typographic Object</option></select></label>
    </section>
  )
}

function FormPanel({ mode, form, providers, modules, modelCatalog, assets, loading, error, onFieldChange, onPreview, onGenerate, onBatch, onImport, onMix }) {
  const activeWorkflow = form.workflow || mode
  const isVideo = activeWorkflow === 'video' || activeWorkflow === 'shapewear_video' || activeWorkflow === 'tiktok_10s'
  const isOutfitSwap = activeWorkflow === 'model_outfit_swap'
  const isClothingImage = activeWorkflow === 'clothing_image_to_image'
  const isTikTokClothing = activeWorkflow === 'tiktok_clothing_image'
  const isShapewearImage = activeWorkflow === 'shapewear_image'
  const isPoster = activeWorkflow === 'poster'
  const isShapewear = activeWorkflow === 'shapewear_image' || activeWorkflow === 'shapewear_video' || activeWorkflow === 'tiktok_10s'
  const directPrompt = Boolean(form.prompt.trim())
  const tiktokHasMaster = Boolean(form.tiktokMasterUrl?.trim?.() || (Array.isArray(form.referenceImages) && form.referenceImages.length))
  return (
    <aside className="composer-panel">
      <div className="composer-heading">
        <div>
          <h1>创建生成</h1>
          <p>{MODES.find((item) => item.id === mode)?.description}</p>
        </div>
        <button type="button" className="icon-button" title="重置表单" onClick={() => window.location.reload()}><RefreshCw size={17} /></button>
      </div>
      <ModeTabs mode={mode} onChange={(value) => onFieldChange('mode', value)} />
      <form onSubmit={onGenerate}>
        <div className="workflow-switcher" role="group" aria-label="工作流">
          {WORKFLOW_OPTIONS[mode].map((option) => <button type="button" key={option.value} className={classNames(activeWorkflow === option.value && 'is-selected')} onClick={() => onFieldChange('workflow', option.value)}>{option.label}</button>)}
        </div>
        {isPoster && <PosterFields form={form} onChange={onFieldChange} />}
        {isTikTokClothing && <TikTokClothingFields form={form} onChange={onFieldChange} />}
        {!isOutfitSwap && !isClothingImage && !isTikTokClothing && !isPoster && <section className="field-section">
          <div className="section-heading-row">
            <div><h3>生成需求</h3><p>{isShapewear ? '广告模板会同步改写场景和风格；改模板后请先预览 Prompt。' : '填写结构化信息，系统将优化为英文 Prompt。'}</p></div>
          </div>
          <InputField label="产品描述" value={form.product} onChange={(value) => onFieldChange('product', value)} placeholder={isShapewear ? '例如：黑色高腰塑身衣' : '例如：哑光玻璃香水瓶'} required={!directPrompt} disabled={directPrompt} />
          <InputField label="场景" value={form.scene} onChange={(value) => onFieldChange('scene', value)} placeholder={isShapewear ? (SHAPEWEAR_STYLE_DEMANDS[form.style_id]?.scene || SHAPEWEAR_STYLE_DEMANDS.product_detail.scene) : '例如：干净中性棚拍，浅灰无缝背景'} required={!directPrompt} disabled={directPrompt} />
          <InputField label="风格" value={form.style} onChange={(value) => onFieldChange('style', value)} placeholder={isShapewear ? (SHAPEWEAR_STYLE_DEMANDS[form.style_id]?.style || SHAPEWEAR_STYLE_DEMANDS.product_detail.style) : '例如：商业静物摄影'} required={!directPrompt} disabled={directPrompt} />
        </section>}
        {isShapewear && <section className="field-section shapewear-fields">
          <div className="section-heading-row"><div><h3>塑身衣预设</h3><p>{SHAPEWEAR_STYLE_HINTS[form.style_id] || SHAPEWEAR_STYLE_HINTS.product_detail}</p></div></div>
          <div className="field-grid">
            <InputField label="颜色" value={form.color} onChange={(value) => onFieldChange('color', value)} placeholder="黑色" />
            <InputField label="材质" value={form.material} onChange={(value) => onFieldChange('material', value)} placeholder="无缝高弹面料" />
          </div>
          <InputField label="目标市场" value={form.target_market} onChange={(value) => onFieldChange('target_market', value)} placeholder="美国" />
          <label className="field"><span className="field-label">广告模板</span>
            <select value={form.style_id} onChange={(event) => onFieldChange('style_id', event.target.value)}>
              {SHAPEWEAR_STYLES.filter((style) => activeWorkflow === 'shapewear_image' || activeWorkflow === 'shapewear_video' || style.value !== 'product_detail').map((style) => <option value={style.value} key={style.value}>{style.label}</option>)}
            </select>
          </label>
          {activeWorkflow === 'shapewear_video' && <div className="clip-preset-grid" role="group" aria-label="视频镜头预设">
            {SHAPEWEAR_VIDEO_CLIPS.map((clip) => {
              const selected = form.clip_id === clip.id
              return (
                <button type="button" key={clip.id} className={classNames('clip-preset', selected && 'is-selected')} onClick={() => onFieldChange('clip_id', clip.id)}>
                  <strong>{clip.label}</strong>
                  <small>{clip.hint}</small>
                  <span className="clip-spec"><span>{clip.duration_seconds}s</span><span>{clip.aspect_ratio}</span><span>{clip.resolution}</span></span>
                </button>
              )
            })}
          </div>}
        </section>}
        {!isVideo && !isOutfitSwap && <section className="field-section"><ImageProviderPicker provider={form.imageProvider || 'hermes'} providers={providers} modules={modules} modelCatalog={modelCatalog} allowedProviders={(isTikTokClothing || isClothingImage || isShapewearImage) ? ['hermes', 'hermes_volcano'] : undefined} onChange={(value) => onFieldChange('imageProvider', value)} /></section>}

        {isOutfitSwap && <section className="field-section outfit-provider-section" aria-label="模特换装图片模型"><div className="section-heading-row"><div><h3>换装图片模型</h3><p>手动选择生成模型；素材顺序和服装细节锁定不变。</p></div><span className="technical-status is-passed">可切换</span></div><ImageProviderPicker provider={form.outfitProvider || 'hermes'} providers={providers} modules={modules} modelCatalog={modelCatalog} allowedProviders={['hermes', 'hermes_volcano']} label="生成模型" onChange={(value) => onFieldChange('outfitProvider', value)} /></section>}

        {isOutfitSwap && <OutfitSwapPicker form={form} assets={assets} onChange={onFieldChange} />}
         {isClothingImage && <ClothingReferencePicker form={form} assets={assets} onChange={onFieldChange} />}
         {isTikTokClothing && <TikTokClothingReferencePicker form={form} assets={assets} onChange={onFieldChange} />}
        {isShapewearImage && <ShapewearProductReferencePicker form={form} assets={assets} onChange={onFieldChange} />}
        {isPoster && <PosterReferencePicker form={form} assets={assets} onChange={onFieldChange} />}
        {isVideo && <section className="field-section">
          <ProviderPicker provider={form.provider} providers={providers} modules={modules} modelCatalog={modelCatalog} onChange={(value) => onFieldChange('provider', value)} />
          <ReferenceImage form={form} assets={assets} onChange={onFieldChange} onImport={onImport} />
        </section>}
          <section className="field-section media-tools"><div className="section-heading-row"><div><h3>{isOutfitSwap || isPoster ? '添加素材' : '本地素材'}</h3><p>{isOutfitSwap ? '导入本地模特图和服装图后即可选择。' : isPoster ? '可导入一张主体参考图，也可以只用文字主题。' : '导入文件夹后可复用图片或创建智能混剪。'}</p></div></div><div className="media-tool-actions"><label className="secondary-button media-import-button"><Images size={16} />{isOutfitSwap || isPoster ? '导入图片' : '导入文件夹'}<input type="file" hidden multiple {...(!isOutfitSwap && !isPoster ? { webkitdirectory: '' } : {})} accept={isOutfitSwap || isPoster ? 'image/*' : 'image/*,video/*'} onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ''; onImport(files) }} /></label>{!isOutfitSwap && !isPoster && <button type="button" className="secondary-button" onClick={onMix} disabled={assets.length < 2}><Clapperboard size={16} />智能混剪</button>}</div></section>
         {!isOutfitSwap && !isClothingImage && !isTikTokClothing && !isPoster && <section className="field-section prompt-override">
          <div className="section-heading-row"><div><h3>完整英文 Prompt</h3><p>可选。填写后将覆盖上方结构化输入。</p></div></div>
          <InputField label="Prompt" value={form.prompt} onChange={(value) => onFieldChange('prompt', value)} placeholder="Paste an English production prompt…" textarea />
        </section>}
        {error && <div className="form-error" role="alert"><CircleAlert size={17} />{error}</div>}
        <div className="form-actions">
          {!isOutfitSwap && <button type="button" className="secondary-button" onClick={onPreview} disabled={loading}><WandSparkles size={16} />预览 Prompt</button>}
          {!isVideo && !isOutfitSwap && !isClothingImage && <button type="button" className="secondary-button" onClick={onBatch} disabled={loading}><Layers3 size={16} />批量生成</button>}

          <button type="submit" className={classNames('primary-button', isOutfitSwap && 'outfit-generate-button')} disabled={loading || (isOutfitSwap && (!form.outfitModelImage || !form.outfitImages?.length)) || (isTikTokClothing && !tiktokHasMaster)}>
            {loading ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />}{loading ? '正在提交' : isOutfitSwap ? '开始换装' : '开始生成'}
          </button>
        </div>
      </form>
    </aside>
  )
}

function PromptSheet({ prompt, warnings, onClose, onCopy, fixed = false }) {
  const dialogRef = useDialogFocus(Boolean(prompt), onClose)
  if (!prompt) return null
  return <div ref={dialogRef} tabIndex={-1} className="prompt-sheet" role="dialog" aria-modal="true" aria-label="Prompt 预览">
    <div className="prompt-dialog">
      <div className="dialog-heading"><div><h2>{fixed ? '固定换装提示词' : '优化后的 Prompt'}</h2><p>{fixed ? '此提示词由系统固定，换装任务不会使用手动生成需求。' : '可在左侧填写完整英文 Prompt 后覆盖。'}</p></div><button className="icon-button" type="button" onClick={onClose} title="关闭"><X size={17} /></button></div>
      <pre>{prompt}</pre>
      {warnings.map((warning) => <p className="dialog-warning" key={warning}><CircleAlert size={16} />{warning}</p>)}
      <div className="dialog-actions"><button className="secondary-button" type="button" onClick={onCopy}><Copy size={16} />复制 Prompt</button><button className="primary-button" type="button" onClick={onClose}>返回编辑</button></div>
    </div>
  </div>
}

function JobStatus({ job }) {
  const working = job && !['succeeded', 'failed'].includes(job.status)
  if (!job) return <div className="workspace-empty"><span className="empty-icon"><Sparkles size={30} /></span><h2>从一个清晰的需求开始</h2><p>输入产品、场景和风格，或选择塑身衣 TikTok 预设。</p></div>
  if (working) return <div className="workspace-progress"><LoaderCircle className="spin progress-icon" size={40} /><p className="eyebrow">正在处理</p><h2>{job.phase}</h2><p>任务已在本地后台运行。可以继续修改下一条需求。</p><div className="job-id">任务 {job.id.slice(0, 8)}</div></div>
  if (job.status === 'failed') return <div className="workspace-failure"><span className="empty-icon is-error"><CircleAlert size={30} /></span><p className="eyebrow">任务未完成</p><h2>{job.error?.message || '生成失败'}</h2><p>已保留当前表单和 Prompt。检查 Provider 配置或稍后重试。</p></div>
  return null
}

function QualityCheck({ quality }) {
  if (!quality) return null
  const technical = [
    ['文件存在', quality.exists], ['文件非空', quality.non_empty], ['格式正确', quality.extension_ok],
  ]
  return <section className="quality-check"><div className="result-section-title"><h3>质量检查</h3><span className={classNames('technical-status', quality.passed ? 'is-passed' : 'is-failed')}>{quality.passed ? '技术检查通过' : '技术检查未通过'}</span></div>
    <div className="technical-list">{technical.map(([label, passed]) => <span key={label} className={passed ? 'is-passed' : 'is-failed'}>{passed ? <Check size={15} /> : <X size={15} />}{label}</span>)}</div>
    {quality.manual_review?.length > 0 && <div className="manual-list"><p>需要人工确认</p>{quality.manual_review.map((item) => <label key={item}><input type="checkbox" /> <span>{item}</span></label>)}</div>}
  </section>
}

function TikTokMetadata({ job }) {
  if (job?.mode !== 'tiktok_clothing_image' || !job.metadata) return null
  const metadata = job.metadata
  return <section className="tiktok-metadata">
    <div className="result-section-title"><h3><ShieldCheck size={15} /> TikTok 主图记录</h3><span className="technical-status is-passed">人工复核</span></div>
    <div className="tiktok-metadata-grid">
      <span><small>市场</small><strong>{metadata.market || 'US'}</strong></span>
      <span><small>语言</small><strong>{metadata.locale || 'en-US'}</strong></span>
      <span><small>展示</small><strong>{metadata.presentation_mode === 'adult_model_fully_covered' ? '成年模特 · 完整遮盖' : '仅商品'}</strong></span>
      <span><small>来源</small><strong>{metadata.source_count || 0} 张 · 主图优先</strong></span>
    </div>
    <p className="field-hint">模型无法自动确认颜色、结构、透明度、年龄或平台政策；发布前请完成下方人工检查。</p>
  </section>
}

function R2Publish({ asset, status, uploading, message, onUpload, onCopy }) {
  if (!asset) return null
  const supported = /\.(jpg|jpeg|png|webp|gif|mp4|mov|webm)$/i.test(asset.name)
  if (asset.r2_url) {
    return <section className="r2-publish is-published">
      <div className="r2-status"><span className="r2-status-icon"><Check size={16} /></span><div><h3>Cloudflare R2 已发布</h3><p>此公网链接可供已配置的图片或视频模型读取。</p></div></div>
      <div className="r2-url-row"><a className="r2-url" href={asset.r2_url} target="_blank" rel="noreferrer">{asset.r2_url}</a><div className="r2-actions"><button type="button" className="icon-button" title="复制公网链接" onClick={() => onCopy(asset.r2_url)}><Copy size={16} /></button><a className="icon-button" title="打开公网链接" href={asset.r2_url} target="_blank" rel="noreferrer"><ExternalLink size={16} /></a></div></div>
    </section>
  }
  const reason = !status.available ? (status.message || 'Cloudflare R2 尚未配置') : !supported ? '该文件格式暂不支持上传' : ''
  return <section className="r2-publish">
    <div className="r2-status"><span className="r2-status-icon"><CloudUpload size={17} /></span><div><h3>发布到 Cloudflare R2</h3><p>{reason || '生成公网 HTTPS URL，供后续图片或视频模型直接读取。'}</p></div></div>
    <button type="button" className="secondary-button r2-upload-button" onClick={() => onUpload(asset)} disabled={uploading || !status.available || !supported} title={reason || '上传当前生成结果'}>
      {uploading ? <LoaderCircle className="spin" size={16} /> : <CloudUpload size={16} />}{uploading ? '正在上传' : '上传 R2'}
    </button>
    {message && <p className="r2-message" role="alert">{message}</p>}
  </section>
}

function ResultView({ job, modules, modelCatalog, assets, r2Status, r2Uploading, r2Message, onCopy, onOpenFolder, onUploadR2 }) {
  if (!job || job.status !== 'succeeded') return <JobStatus job={job} />
  const output = job.outputs?.[0]
  const video = isVideoAsset(output)
  const identifier = output?.replace(/^\/api\/assets\//, '')
  const asset = assets.find((item) => item.id === identifier)
  return <div className="result-view">
    <div className="result-head"><div><p className="eyebrow">已完成</p><h2>{job.mode_label}</h2><p>{relativeTime(job.updated_at)} · {jobModelLabel(job, modules, modelCatalog)}</p></div><span className="success-mark"><Check size={18} /></span></div>
    <div className={classNames('media-stage', video && 'is-video')}>
      {video ? <video src={output} controls preload="metadata" /> : <img src={output} alt="已生成的 AIGC 素材" />}
    </div>
    <div className="asset-actions">
      <a className="primary-button" href={output} download><ArrowDownToLine size={16} />下载文件</a>
      <button type="button" className="secondary-button" onClick={() => onOpenFolder(output)}><FolderOpen size={16} />打开目录</button>
      <button type="button" className="icon-button" title="复制 Prompt" onClick={() => onCopy(job.prompt)}><Copy size={16} /></button>
    </div>
    <R2Publish asset={asset} status={r2Status} uploading={r2Uploading === asset?.id} message={r2Message} onUpload={onUploadR2} onCopy={onCopy} />
    <section className="prompt-result"><div className="result-section-title"><h3>提交的 Prompt</h3><button type="button" className="text-button" onClick={() => onCopy(job.prompt)}><Copy size={14} />复制</button></div><p>{job.prompt}</p></section>
    <TikTokMetadata job={job} />
    <QualityCheck quality={job.quality} />
  </div>
}

function History({ jobs, activeId, onSelect }) {
  return <section className="history" id="history"><div className="history-heading"><h2>最近输出</h2><span>{jobs.length}</span></div>
    {jobs.length ? <div className="history-list">{jobs.map((job) => {
      const output = job.outputs?.[0]
      const video = isVideoAsset(output)
      return <button type="button" className={classNames('history-item', activeId === job.id && 'is-selected')} key={job.id} onClick={() => onSelect(job)}>
        <span className="history-thumbnail">{output ? video ? <video src={output} muted preload="metadata" /> : <img src={output} alt="" /> : <LoaderCircle className={job.status === 'failed' ? '' : 'spin'} size={16} />}{video && output && <Play size={12} />}</span>
        <span className="history-copy"><strong>{job.mode_label}</strong><small>{job.status === 'succeeded' ? relativeTime(job.updated_at) : job.phase}</small></span>
        <ChevronRight size={16} />
      </button>
    })}</div> : <p className="history-empty">生成结果会出现在这里。</p>}
  </section>
}

export default function App() {
  const [mode, setMode] = useState('image')
  const [form, setForm] = useState(INITIAL_FORM)
  const [providers, setProviders] = useState([])
  const [assets, setAssets] = useState([])
  const [r2Status, setR2Status] = useState({ available: false, message: '正在检查 Cloudflare R2 配置' })
  const [r2Uploading, setR2Uploading] = useState('')
  const [r2Message, setR2Message] = useState('')
  const [jobs, setJobs] = useState([])
  const [activeJob, setActiveJob] = useState(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [promptPreview, setPromptPreview] = useState(null)
  const [moduleSettings, setModuleSettings] = useState([])
  const [modelCatalog, setModelCatalog] = useState({})
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [mixOpen, setMixOpen] = useState(false)
  const [batchTask, setBatchTask] = useState(null)
  const [mixTask, setMixTask] = useState(null)
  const pollTimer = useRef(null)
  const pollFailures = useRef(0)
  const batchPollTimer = useRef(null)
  const mixPollTimer = useRef(null)

  const requestMode = useMemo(() => mode, [mode])
  const refreshData = useCallback(async () => {
    const [providerData, assetData, jobData, storageData, moduleData] = await Promise.all([fetchProviders(), fetchAssets(), fetchJobs(), fetchR2Status(), fetchModuleSettings()])
    setProviders(providerData.providers)
    setAssets(assetData.assets)
    setJobs(jobData.jobs)
    setR2Status(storageData)
    setModuleSettings(moduleData.modules || [])
    setActiveJob((current) => current ? jobData.jobs.find((job) => job.id === current.id) || current : jobData.jobs[0] || null)
  }, [])


  useEffect(() => {
    setForm((current) => {
      const builtinImage = ['clothing_image_to_image', 'tiktok_clothing_image', 'shapewear_image'].includes(current.workflow)
      const nextImage = firstAvailableImageProvider(providers, current.imageProvider, builtinImage ? ['hermes', 'hermes_volcano'] : undefined)
      const nextOutfit = firstAvailableImageProvider(providers, current.outfitProvider, ['hermes', 'hermes_volcano'])
      const videoProviders = providers.filter((item) => item.media_types?.includes('video') && item.available)
      const nextVideo = (current.provider && videoProviders.some((item) => item.id === current.provider))
        ? current.provider
        : (videoProviders[0]?.id || current.provider)
      if (nextImage === current.imageProvider && nextOutfit === current.outfitProvider && nextVideo === current.provider) return current
      return { ...current, imageProvider: nextImage, outfitProvider: nextOutfit, provider: nextVideo }
    })
  }, [providers])


  useEffect(() => { refreshData().catch(() => setError('无法连接本地 AIGC Studio 服务。请先启动 API 服务。')) }, [refreshData])
  useEffect(() => {
    if (!activeJob || ['succeeded', 'failed'].includes(activeJob.status) || pollTimer.current) return undefined
    pollTimer.current = window.setInterval(async () => {
      try {
        const updated = await fetchJob(activeJob.id)
        pollFailures.current = 0
        setActiveJob(updated)
        setJobs((current) => [updated, ...current.filter((item) => item.id !== updated.id)])
        if (['succeeded', 'failed'].includes(updated.status)) {
          window.clearInterval(pollTimer.current)
          pollTimer.current = null
          fetchAssets().then((data) => setAssets(data.assets)).catch(() => {})
        }
      } catch (pollError) {
        pollFailures.current += 1
        if (pollFailures.current >= 3) {
          window.clearInterval(pollTimer.current)
          pollTimer.current = null
          setError(`任务状态暂时无法更新：${pollError.message}`)
        }
      }
    }, 3000)
    return () => {
      if (pollTimer.current) {
        window.clearInterval(pollTimer.current)
        pollTimer.current = null
      }
    }
  }, [activeJob?.id, activeJob?.status])
  useEffect(() => () => {
    for (const timer of [pollTimer, batchPollTimer, mixPollTimer]) if (timer.current) window.clearInterval(timer.current)
  }, [])

  const updateField = useCallback((key, value) => {
    if (key === 'mode') {
      setMode(value)
      setForm((current) => {
        if (value === 'image' || value === 'video') {
          return { ...current, ...GENERIC_IMAGE_DEFAULTS, workflow: value }
        }
        if (value === 'tiktok_10s') {
          return { ...current, ...SHAPEWEAR_FORM_DEFAULTS, ...shapewearDemandFor('tiktok_ugc', value), workflow: value }
        }
        return { ...current, workflow: value }
      })
      setError('')
      return
    }
    if (key === 'workflow' && value === 'model_outfit_swap') {
      setForm((current) => ({
        ...current,
         workflow: value,
         imageProvider: current.imageProvider,
         outfitProvider: current.outfitProvider,

        product: '',
        scene: '',
        style: '',
        color: '',
        material: '',
        target_market: '',
        style_id: '',
        prompt: '',
        outfitModelImage: '',
        outfitImages: [],
      }))
      setError('')
      return
    }
    if (key === 'workflow' && value === 'clothing_image_to_image') {
      setForm((current) => ({
        ...current,
        workflow: value,
        imageProvider: current.imageProvider,

        product: '',
        scene: '',
        style: '',
        color: '',
        material: '',
        target_market: '',
        style_id: '',
        prompt: '',
        referenceImages: [],
      }))
      setError('')
      return
    }
    if (key === 'workflow' && value === 'tiktok_clothing_image') {
      setForm((current) => ({
        ...current,
        workflow: value,
        imageProvider: current.imageProvider || 'hermes',
        product: '',
        style: '',
        color: '',
        material: '',
        target_market: '',
        style_id: '',
        referenceImages: [],
        tiktokPurpose: current.tiktokPurpose || 'shop_listing',
        tiktokStyle: current.tiktokStyle || 'studio_detail',
        tiktokAspectRatio: current.tiktokAspectRatio || '9:16',
        tiktokClaims: '',
        tiktokMarket: current.tiktokMarket || 'US',
        tiktokLocale: current.tiktokLocale || 'en-US',
        tiktokPresentation: current.tiktokPresentation || 'product_only',
        tiktokMasterUrl: '',
        tiktokDetailUrls: '',
        prompt: '',
      }))
      setError('')
      return
    }
    if (key === 'workflow' && (value === 'image' || value === 'video')) {
      setForm((current) => ({
        ...current,
        ...GENERIC_IMAGE_DEFAULTS,
        workflow: value,
        imageProvider: current.imageProvider || 'hermes',
        referenceImages: [],
      }))
      setError('')
      return
    }
    if (key === 'workflow' && (value === 'shapewear_image' || value === 'shapewear_video' || value === 'tiktok_10s')) {
      const styleId = value === 'tiktok_10s' ? 'tiktok_ugc' : (value === 'shapewear_video' ? 'luxury_fashion' : SHAPEWEAR_FORM_DEFAULTS.style_id)
      const clip = value === 'shapewear_video' ? shapewearVideoClipFor('studio_walk') : { clip_id: '', duration_seconds: '', aspect_ratio: '', resolution: '' }
      setForm((current) => ({
        ...current,
        ...SHAPEWEAR_FORM_DEFAULTS,
        ...shapewearDemandFor(styleId, value),
        ...clip,
        workflow: value,
        imageProvider: current.imageProvider || 'hermes',
        referenceImages: [],
      }))
      setError('')
      return
    }
    if (key === 'style_id') {
      setForm((current) => {
        const next = {
          ...current,
          ...shapewearDemandFor(value, current.workflow),
          prompt: '',
        }
        if (current.workflow === 'shapewear_video') {
          const matchingClip = SHAPEWEAR_VIDEO_CLIPS.find((clip) => clip.style_id === value && (current.clip_id ? clip.id === current.clip_id : true))
            || SHAPEWEAR_VIDEO_CLIPS.find((clip) => clip.style_id === value)
          if (matchingClip) Object.assign(next, shapewearVideoClipFor(matchingClip.id))
        }
        return next
      })
      setError('')
      return
    }
    if (key === 'clip_id') {
      setForm((current) => ({
        ...current,
        ...shapewearVideoClipFor(value),
        prompt: '',
      }))
      setError('')
      return
    }
    if (key === 'workflow' && value === 'poster') {
      setForm((current) => ({
        ...current,
        workflow: value,
        referenceImages: [],
        outfitModelImage: '',
        outfitImages: [],
        posterReferenceImage: '',
      }))
      setError('')
      return
    }
    if (key === 'provider' && value === 'seedance') {
      setForm((current) => ({ ...current, provider: value, referenceKind: 'url', reference: '' }))
      setError('')
      return
    }
    if (key === 'referenceKind') {
      setForm((current) => ({ ...current, referenceKind: value, reference: '' }))
      setError('')
      return
    }
    setForm((current) => ({ ...current, [key]: value }))
    setError('')
  }, [])

  const handleUploadR2 = useCallback(async (asset) => {
    setError('')
    setR2Message('')
    setR2Uploading(asset.id)
    try {
      const published = await uploadAssetToR2(asset.id)
      setAssets((current) => current.map((item) => item.id === asset.id ? { ...item, r2_url: published.url, r2_object_key: published.object_key } : item))
    } catch (uploadError) {
      setR2Message(uploadError.message)
    } finally {
      setR2Uploading('')
    }
  }, [])

  const requestPayload = useCallback(() => {
    const workflow = form.workflow || requestMode
    if (workflow === 'poster') {
      const request = {
        subject: form.posterSubject,
        intent: form.posterIntent,
        exact_text: form.posterText,
        ratio: form.posterRatio,
        palette: form.posterPalette,
        substrate: form.posterSubstrate,
        layout: form.posterLayout,
        type_role: form.posterTypeRole,
        tension: form.posterTension,
      }
      const payload = { mode: workflow, provider: form.imageProvider || 'hermes', request }
      if (form.posterReferenceImage) payload.reference_images = [{ kind: 'asset', value: form.posterReferenceImage }]
      return payload
    }
    if (workflow === 'model_outfit_swap') {
      const payload = { mode: workflow, provider: form.outfitProvider || 'hermes', request: {} }
      const outfitImages = Array.isArray(form.outfitImages) ? form.outfitImages : []
      const referenceImages = [form.outfitModelImage, ...outfitImages].filter(Boolean)
      if (referenceImages.length) payload.reference_images = referenceImages.map((value) => ({ kind: 'asset', value }))
      return payload
    }
    if (workflow === 'clothing_image_to_image') {
      const payload = { mode: workflow, provider: form.imageProvider, request: {

        objective: 'Create a premium commercial clothing product image focused on craftsmanship, fabric texture, construction, and functional details.',
      } }
      const referenceImages = Array.isArray(form.referenceImages) ? form.referenceImages : []
      if (referenceImages.length) payload.reference_images = [{ kind: 'asset', value: referenceImages[0] }]
      return payload
    }
    if (workflow === 'tiktok_clothing_image') {
      const remoteMaster = form.tiktokMasterUrl?.trim?.() || ''
      const remoteDetails = (form.tiktokDetailUrls || '')
        .split(/[\n,]+/)
        .map((value) => value.trim())
        .filter(Boolean)
        .slice(0, 9)
      const request = {
        purpose: form.tiktokPurpose || 'shop_listing',
        style: form.tiktokStyle || 'studio_detail',
        aspect_ratio: form.tiktokAspectRatio || '9:16',
        market: form.tiktokMarket || 'US',
        locale: form.tiktokLocale || 'en-US',
        presentation_mode: form.tiktokPresentation || 'product_only',
        scene: form.scene,
        claims: form.tiktokClaims,
        prompt: form.prompt,
      }
      const payload = { mode: workflow, provider: form.imageProvider || 'hermes', request }
      const referenceImages = Array.isArray(form.referenceImages) ? form.referenceImages.slice(0, 10) : []
      const references = remoteMaster
        ? [{ kind: 'url', value: remoteMaster }, ...remoteDetails.map((value) => ({ kind: 'url', value })), ...referenceImages.map((value) => ({ kind: 'asset', value }))]
        : referenceImages.length
          ? [{ kind: 'asset', value: referenceImages[0] }, ...referenceImages.slice(1).map((value) => ({ kind: 'asset', value })), ...remoteDetails.map((value) => ({ kind: 'url', value }))]
          : remoteDetails.map((value) => ({ kind: 'url', value }))
      if (references.length) payload.reference_images = references.slice(0, 10)
      return payload
    }
    const request = Object.fromEntries(Object.entries({
      product: form.product, scene: form.scene, style: form.style, color: form.color, material: form.material,
      target_market: form.target_market, style_id: form.style_id, clip_id: form.clip_id,
      duration_seconds: form.duration_seconds, aspect_ratio: form.aspect_ratio, resolution: form.resolution,
      prompt: form.prompt,
    }).filter(([, value]) => value?.toString?.().trim?.()))
    const payload = { mode: workflow, request }
    if (payload.mode === 'shapewear_image') {
      payload.provider = form.imageProvider || 'hermes'
      const referenceImages = Array.isArray(form.referenceImages) ? form.referenceImages.slice(0, 10) : []
      if (referenceImages.length) payload.reference_images = referenceImages.map((value) => ({ kind: 'asset', value }))
    } else if (payload.mode === 'image') {
      payload.provider = form.imageProvider || 'hermes'
    }
    if (payload.mode === 'video' || payload.mode === 'shapewear_video' || payload.mode === 'tiktok_10s') {
      payload.provider = form.provider
      if (form.reference.trim()) payload.reference_image = { kind: form.referenceKind === 'asset' ? 'asset' : 'url', value: form.reference.trim() }
    }
    return payload
  }, [form, requestMode])

  const handleSaveModule = useCallback(async (moduleId, draft) => {
    const saved = await saveModuleSettings(moduleId, draft)
    setModuleSettings((current) => current.map((item) => item.id === moduleId ? saved : item))
    setModelCatalog((current) => {
      const catalog = current[moduleId]
      const matches = catalog && catalog.api_url === saved.api_url && catalog.model_id === saved.model
      if (matches) return current
      const next = { ...current }
      delete next[moduleId]
      return next
    })
    await refreshData()
    return saved
  }, [refreshData])

  const handleTestModule = useCallback((moduleId, draft) => testModuleSettings(moduleId, draft), [])
  const handleCreateModule = useCallback(async (payload) => {
    const created = await createCustomModule(payload)
    setModuleSettings((current) => [...current.filter((item) => item.id !== created.id), created])
    await refreshData()
    return created
  }, [refreshData])
  const handleDeleteModule = useCallback(async (moduleId) => {
    await deleteCustomModule(moduleId)
    setModuleSettings((current) => current.filter((item) => item.id !== moduleId))
    setModelCatalog((current) => {
      const next = { ...current }
      delete next[moduleId]
      return next
    })
    await refreshData()
  }, [refreshData])
  const handleDiscoverModels = useCallback(async (moduleId, draft) => {
    const result = await discoverModuleModels(moduleId, draft)
    const selectedModel = result.selected_model || draft.model || ''
    // Discovery is a user-visible configuration action. Keep the selected
    // model in root state immediately; the API key remains request-only.
    setModuleSettings((current) => current.map((item) => item.id === moduleId
      ? { ...item, api_url: draft.api_url || item.api_url, model: selectedModel }
      : item))
    setModelCatalog((current) => ({
      ...current,
      [moduleId]: { models: result.models || [], api_url: draft.api_url || '', model_id: selectedModel },
    }))
    return result
  }, [])

  const handleImport = useCallback(async (files) => {
    if (!files.length) return []
    try {
      const data = await importMedia(files)
      setAssets((current) => [...data.assets, ...current])
      return data.assets
    } catch (importError) {
      setError(importError.message)
      throw importError
    }
  }, [])

  const handleCreateBatch = useCallback(async ({ prompt, aspectRatio, selected, count, idempotencyKey }) => {
    const items = Array.from({ length: count }, (_, index) => ({
      client_id: `row-${index + 1}`,
      prompt: prompt.trim(),
      aspect_ratio: aspectRatio,
      image_asset_id: selected[index % selected.length] || null,
      reference_asset_ids: selected.filter((_, itemIndex) => itemIndex !== index % Math.max(1, selected.length)).slice(0, 16),
    }))
    const created = await createGenerationBatch({ items, idempotency_key: idempotencyKey, provider: form.imageProvider })

    setBatchTask({ ...created, status: 'queued' })
    if (batchPollTimer.current) window.clearInterval(batchPollTimer.current)
    batchPollTimer.current = window.setInterval(async () => {
      try {
        const batch = await fetchGenerationBatch(created.batch_id)
        setBatchTask(batch)
        if (['succeeded', 'failed', 'partial'].includes(batch.status)) { window.clearInterval(batchPollTimer.current); batchPollTimer.current = null; fetchAssets().then((data) => setAssets(data.assets)).catch(() => {}) }
      } catch (pollError) { window.clearInterval(batchPollTimer.current); batchPollTimer.current = null; setBatchTask((current) => ({ ...current, status: 'failed', phase: pollError.message })) }
    }, 1800)
  }, [form.imageProvider])


  const handlePlanMix = useCallback(({ clips, objective, targetDuration, transitionMode }) => planMix({ clips, objective, target_duration_ms: targetDuration * 1000, transition_mode: transitionMode }), [])

  const handleCreateMix = useCallback(async ({ clips, aspectRatio, objective, transitionMode, plan }) => {
    const created = await createMix({ clips, aspect_ratio: aspectRatio, objective, transition_mode: transitionMode, plan })
    setMixTask({ ...created, phase: '智能计划完成，等待混剪' })
    if (mixPollTimer.current) window.clearInterval(mixPollTimer.current)
    mixPollTimer.current = window.setInterval(async () => {
      try {
        const mix = await fetchMix(created.mix_id)
        setMixTask(mix)
        if (['succeeded', 'failed'].includes(mix.status)) { window.clearInterval(mixPollTimer.current); mixPollTimer.current = null; fetchAssets().then((data) => setAssets(data.assets)).catch(() => {}) }
      } catch (pollError) { window.clearInterval(mixPollTimer.current); mixPollTimer.current = null; setMixTask((current) => ({ ...current, status: 'failed', phase: pollError.message })) }
    }, 1200)
  }, [])

  const handlePreview = useCallback(async () => {
    setError('')
    try { setPromptPreview(await previewPrompt(requestPayload())) } catch (requestError) { setError(requestError.message) }
  }, [requestPayload])

  const handleGenerate = useCallback(async (event) => {
    event.preventDefault()
    setError('')
    const activeWorkflow = form.workflow || requestMode
    const selectedReferenceImages = Array.isArray(form.referenceImages) ? form.referenceImages : []
    if (activeWorkflow === 'model_outfit_swap' && (!form.outfitModelImage || !form.outfitImages?.length)) {
      setError('请选择一张模特主图和至少一张服装图。')
      return
    }
    if (activeWorkflow === 'clothing_image_to_image' && selectedReferenceImages.length !== 1) {
      setError('服装图生图必须选择一张服装基准图。')
      return
    }
    if (activeWorkflow === 'tiktok_clothing_image') {
      const masterUrl = form.tiktokMasterUrl?.trim?.() || ''
      const detailUrls = (form.tiktokDetailUrls || '').split(/[\n,]+/).map((value) => value.trim()).filter(Boolean)
      if (!masterUrl && selectedReferenceImages.length < 1) {
        setError('TikTok 服装主图需要一张本地主图或公网 HTTPS 主图。')
        return
      }
      if ((masterUrl ? 1 : 0) + selectedReferenceImages.length + detailUrls.length > 10) {
        setError('TikTok 服装主图最多使用 1 张主图和 9 张同款细节图。')
        return
      }
    }
    if (activeWorkflow === 'shapewear_image' && selectedReferenceImages.length > 10) {
      setError('塑身衣商品图最多选择 10 张商品参考图。')
      return
    }
    if (activeWorkflow === 'poster' && selectedReferenceImages.length > 1) {
      setError('海报最多选择一张主体参考图。')
      return
    }
    setIsSubmitting(true)
    try {
      const payload = requestPayload()
      const created = await createGeneration(payload)
      const snapshot = modelSnapshot(payload.provider, moduleSettings, modelCatalog)
      const job = { id: created.job_id, status: 'queued', phase: '等待处理', mode: form.workflow || requestMode, mode_label: MODES.find((item) => item.id === mode)?.label || '塑身衣视频', provider: payload.provider, outputs: [], ...snapshot }
      if (activeWorkflow === 'model_outfit_swap') job.mode_label = '模特换装'
      if (activeWorkflow === 'clothing_image_to_image') job.mode_label = '服装工艺图'
      if (activeWorkflow === 'tiktok_clothing_image') job.mode_label = 'TikTok 服装主图'
      if (activeWorkflow === 'poster') job.mode_label = '单色海报'
      setActiveJob(job)
      setJobs((current) => [job, ...current])
      pollFailures.current = 0
      if (pollTimer.current) window.clearInterval(pollTimer.current)
      pollTimer.current = window.setInterval(async () => {
        try {
          const updated = await fetchJob(created.job_id)
          const merged = { ...updated, model: updated.model || snapshot.model, model_name: updated.model_name || snapshot.model_name }
          setActiveJob(merged)
          setJobs((current) => [merged, ...current.filter((item) => item.id !== merged.id)])
          if (['succeeded', 'failed'].includes(updated.status)) {
            window.clearInterval(pollTimer.current)
            pollTimer.current = null
            fetchAssets().then((data) => setAssets(data.assets)).catch(() => {})
          }
        } catch (pollError) {
          pollFailures.current += 1
          if (pollFailures.current >= 3) {
            window.clearInterval(pollTimer.current)
            pollTimer.current = null
            setError(`任务状态暂时无法更新：${pollError.message}`)
          }
        }
      }, 1800)
    } catch (requestError) { setError(requestError.message) } finally { setIsSubmitting(false) }
  }, [form, mode, moduleSettings, modelCatalog, requestMode, requestPayload])

  const copyText = useCallback(async (value) => { if (value) await navigator.clipboard.writeText(value) }, [])
  const handleOpenFolder = useCallback(async (url) => {
    const id = url.replace(/^\/api\/assets\//, '')
    try { await openOutputDirectory(id) } catch (openError) { setError(openError.message) }
  }, [])

  return <div className="app-shell">
    <Header providers={providers} r2Status={r2Status} onSettings={() => setSettingsOpen(true)} />
    <main className="studio-layout">
      <FormPanel mode={mode} form={form} providers={providers} modules={moduleSettings} modelCatalog={modelCatalog} assets={assets} loading={isSubmitting} error={error} onFieldChange={updateField} onPreview={handlePreview} onGenerate={handleGenerate} onBatch={() => setBatchOpen(true)} onImport={handleImport} onMix={() => setMixOpen(true)} />
      <section className="workspace" aria-live="polite"><div className="workspace-canvas"><ResultView job={activeJob} modules={moduleSettings} modelCatalog={modelCatalog} assets={assets} r2Status={r2Status} r2Uploading={r2Uploading} r2Message={r2Message} onCopy={copyText} onOpenFolder={handleOpenFolder} onUploadR2={handleUploadR2} /></div><BackgroundTasks batch={batchTask} mix={mixTask} /><History jobs={jobs} activeId={activeJob?.id} onSelect={(job) => { setActiveJob(job); setR2Message('') }} /></section>
    </main>
    <PromptSheet prompt={promptPreview?.prompt} warnings={promptPreview?.warnings || []} fixed={form.workflow === 'model_outfit_swap'} onClose={() => setPromptPreview(null)} onCopy={() => copyText(promptPreview?.prompt)} />
    <ApiSettingsDialog open={settingsOpen} modules={moduleSettings} onClose={() => setSettingsOpen(false)} onSave={handleSaveModule} onTest={handleTestModule} onDiscover={handleDiscoverModels} onCreate={handleCreateModule} onDelete={handleDeleteModule} />
    <BatchDialog open={batchOpen} form={form} assets={assets} onClose={() => setBatchOpen(false)} onCreate={handleCreateBatch} />
    <MixDialog open={mixOpen} assets={assets} modules={moduleSettings} onClose={() => setMixOpen(false)} onPlan={handlePlanMix} onCreate={handleCreateMix} />
  </div>
}

import { Braces, Check, ChevronDown, X } from 'lucide-react'
import { AnimatePresence } from 'motion/react'
import * as m from 'motion/react-m'
import { Dialog, DropdownMenu } from 'radix-ui'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, day, invalidate, send, useApi } from './api'
import { ask } from './ask'
import { Avatar } from './Avatar'
import { RailGroup } from './Facets'
import { AgentSpot, BookSpot, BulbSpot, GlobeSpot, MergeSpot, PaperPlane, PeopleSpot, TagSpot } from './Illustrations'
import { pageIn } from './motion'
import { Notice, Section, Skeleton } from './ui'

const ADMIN_PAGES = [
  ['/admin/agent', 'Agent 配置', AgentSpot],
  ['/admin/users', '账户管理', PeopleSpot],
] as const

/** The console's two pages in the rail, each with its own small drawing. */
function AdminNav({ path }: { path: string }) {
  return (
    <RailGroup title="管理后台" open>
      <ul className="space-y-1">
        {ADMIN_PAGES.map(([href, name, Art]) => {
          const current = href === path
          return (
            <li key={href}>
              <a
                href={href}
                aria-current={current ? 'page' : undefined}
                className="group/nav relative -mx-2 flex items-center gap-3 rounded-md px-2 py-1.5 transition-colors duration-(--duration-fast) hover:bg-stone-wash aria-[current]:bg-clay-wash"
              >
                {current && <span className="absolute inset-y-2 -left-0.5 w-0.5 rounded-full bg-clay" />}
                <Art className="size-11 shrink-0 transition-transform duration-(--duration-base) group-hover/nav:-rotate-3" />
                <span className="text-sm text-ink-soft group-aria-[current]/nav:font-medium group-aria-[current]/nav:text-clay-ink">{name}</span>
              </a>
            </li>
          )
        })}
      </ul>
    </RailGroup>
  )
}

/** The admin console's frame, as the profile's: its two pages in the left quarter's rail, the
 * page in the paper column. A member who finds the address, or an admin made a member while
 * here (denied: the page's request got a 403), sees a note instead. */
function AdminFrame({ path, denied, children }: { path: string; denied?: boolean; children: ReactNode }) {
  const me = useApi<{ is_admin: boolean }>('/me')
  useEffect(() => {
    if (denied) invalidate('/me') // made a member meanwhile: the header menu follows too
  }, [denied])
  const title = ADMIN_PAGES.find(([href]) => href === path)?.[1]
  return (
    <main className="frame max-w-page pt-8 pb-16 lg:grid lg:grid-cols-12 lg:gap-x-8">
      <title>{`${title} · ScholarMind`}</title>
      <aside className="relative mb-8 lg:col-span-3 lg:mb-0">
        <div className="mx-auto w-1/2 min-w-40 lg:sticky lg:top-24">
          <AdminNav path={path} />
        </div>
        <span className="hand-rule-v hidden lg:block" aria-hidden="true" />
      </aside>
      <div className="lg:col-span-7 lg:col-start-4">
        {!me.data ? (
          <Skeleton rows={2} />
        ) : !me.data.is_admin || denied ? (
          <Notice art={<PaperPlane />}>
            <p>只有管理员能打开这一页</p>
          </Notice>
        ) : (
          // Each part's list has hairlines of its own, so parts are set apart by space alone.
          <m.div {...pageIn()} className="space-y-10 [&>section]:border-t-0 [&>section]:pt-0">
            {children}
          </m.div>
        )}
      </div>
    </main>
  )
}

// ───────────────────────────── Parts ─────────────────────────────

/** A word at the far end of a heading that opens (or closes) a form under it. */
function Opener({ open, onClick, children }: { open: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-expanded={open} onClick={onClick} className="text-sm text-clay-ink hover:underline">
      {open ? '取消' : children}
    </button>
  )
}

/** The form a heading's word opens, its height following. */
function Fold({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <m.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
          className="-mx-1 overflow-hidden px-1" // wider by the fields' focus rings
        >
          <div className="pb-5">{children}</div>
        </m.div>
      )}
    </AnimatePresence>
  )
}

/** One thing in a part: a mark, its name and tags, a line about it, and what can be done to it. */
function Row({ lead, name, tags, detail, acts, children }: { lead: ReactNode; name: ReactNode; tags?: ReactNode; detail: ReactNode; acts?: ReactNode; children?: ReactNode }) {
  return (
    <li className="py-3">
      <div className="flex items-center gap-3">
        {lead}
        <div className="min-w-0 flex-1">
          <p className="flex min-w-0 items-center gap-2 text-sm font-medium text-ink">
            <span className="truncate">{name}</span>
            {tags}
          </p>
          <p className="mt-0.5 truncate text-xs text-stone">{detail}</p>
        </div>
        {acts && <div className="flex shrink-0 items-center gap-1">{acts}</div>}
      </div>
      {children}
    </li>
  )
}

const Tag = ({ clay, children }: { clay?: boolean; children: ReactNode }) => (
  <span className={`tag shrink-0 font-normal ${clay ? 'bg-clay-wash text-clay-ink' : ''}`}>{children}</span>
)
const actCls = 'rounded-md px-2 py-1 text-sm transition-colors duration-(--duration-fast) disabled:opacity-40 disabled:hover:bg-transparent'
const Act = ({ danger, onClick, disabled, children }: { danger?: boolean; onClick: () => void; disabled?: boolean; children: ReactNode }) => (
  <button type="button" onClick={onClick} disabled={disabled} className={`${actCls} ${danger ? 'text-clay-ink hover:bg-clay-wash' : 'text-ink-soft hover:bg-stone-wash'}`}>
    {children}
  </button>
)
const Status = ({ error, done }: { error?: string; done?: string }) =>
  error || done ? <p role="status" className={`mt-2 text-sm ${error ? 'text-clay-ink' : 'text-stone'}`}>{error ?? done}</p> : null

// ───────────────────────────── Agent ─────────────────────────────

interface Prompt {
  key: string
  version: string // report.v2, or report.v2+1a2b3c4d once changed here
  text: string
  default_text: string
  modified: boolean
}

/** A setting of the model's connection: its value and whether it was set here or comes from the
 * deployment's configuration. */
interface Setting {
  value: string
  source: 'web' | 'config'
}

interface Agent {
  api_key_set: boolean
  api_key_hint: string | null // "…wxyz"
  api_key_source: 'web' | 'env' | null
  model?: { base_url: Setting; brief_model: Setting; report_model: Setting } // left out before the backend had it
  prompts: Prompt[]
}

/** What each prompt does, by its key; json: it fixes an output format the code parses. */
const PROMPTS: Record<string, { name: string; about: string; Art: typeof TagSpot; json?: string; keep?: string[] }> = {
  brief: { name: '摘要与打标', about: '入库时成批给论文写中文摘要，打领域、方向和类型标签', Art: TagSpot, json: '摘要和打标', keep: ['{{VOCABULARY}}', '{{LANGUAGE}}'] },
  report: { name: '论文解读', about: '阅读页右栏的长篇解读，输出 Markdown', Art: BookSpot },
  assess: { name: '创新评估', about: '判断论文的创新性质，挑出卡片上的方法总览图', Art: BulbSpot, json: '创新评估' },
  'topic-review': { name: '方向审核', about: '定期找出自动新建的研究方向里的重复项并合并', Art: MergeSpot, json: '方向审核' },
  translate: { name: '摘要翻译', about: '详情页第一次打开时，把英文摘要逐句忠实地翻成中文', Art: GlobeSpot, keep: ['{{LANGUAGE}}'] },
  chat: { name: '问答', about: '阅读页「问答」里回答读者对这篇论文的提问，引用原文页码', Art: AgentSpot, keep: ['{{LANGUAGE}}'] },
}
const ART = 'size-10 shrink-0' // a row's drawing

const SOURCE = (s: string | null | undefined) => (s === 'web' ? '网页设置' : s ? '部署配置' : '未设置')

/** The backend's reasons a connection was refused or could not be tried (short English), in
 * our words; one not here shows as it came. */
const CONNECTION_ERRORS: [RegExp, string][] = [
  [/^A different host needs its own API key/, '换了主机，要填这个服务商自己的 API Key'],
  [/^No API key is set/, '还没有密钥，请填上 API Key'],
  [/^(401|403) from /, '服务商拒绝了这个密钥'],
  [/^30\d from /, '这个地址把请求转到了别处，看看 Base URL 写对没有'],
  [/did not answer with a model list/, '这个地址没有返回模型列表，看看 Base URL 是不是少了 /v1'],
  [/took longer than/, '连不上：15 秒内没有回应'],
  [/^Could not reach /, '连不上这个地址'],
]

function probeError(err: unknown) {
  if (!(err instanceof ApiError)) return (err as Error).message
  const d = err.detail ?? ''
  const known = CONNECTION_ERRORS.find(([re]) => re.test(d))?.[1]
  if (known) return known
  const status = d.match(/^(\d{3}) from /)?.[1]
  if (status) return `服务商返回了 ${status}，没有给出模型列表`
  if (err.status === 422) return '要填带主机名的 http 或 https 地址，最长 2048 个字符'
  return d || err.message
}

/** Protocol, host and port of an address, to tell whether a new Base URL is on the same host. */
const origin = (url?: string) => {
  try {
    return new URL(url ?? '').origin
  } catch {
    return ''
  }
}

/** The connection (where requests go and the key they carry) is one thing: a row with both, tried
 * (检测) as it is, or changed in one form where the new pair can be tried before it is saved.
 * The key may be left empty only on the same host (only the path changes): the backend never
 * sends the current key elsewhere. 删除 goes back to the deployment's pair. */
function Connection({ agent, children }: { agent: Agent; children?: ReactNode }) {
  const [editing, setEditing] = useState(false)
  const [draftUrl, setDraftUrl] = useState('')
  const [probe, setProbe] = useState<{ busy?: boolean; found?: number; error?: string }>({})
  const [state, setState] = useState<{ busy?: boolean; error?: string; done?: string }>({})
  const url = agent.model?.base_url
  const keepKey = agent.api_key_set && origin(draftUrl) !== '' && origin(draftUrl) === origin(url?.value)
  const source = url?.source === 'web' || agent.api_key_source === 'web' ? 'web' : url ? 'config' : agent.api_key_source

  async function test(body: { base_url?: string; api_key?: string }) {
    setProbe({ busy: true })
    try {
      setProbe({ found: (await send<{ models: string[] }>('POST', '/admin/agent/probe', body)).models.length })
    } catch (err) {
      setProbe({ error: probeError(err) })
    }
  }

  const fields = (form: HTMLFormElement) => {
    const data = new FormData(form)
    const base_url = String(data.get('base_url') ?? '').trim()
    const api_key = String(data.get('api_key') ?? '').trim()
    return { base_url, ...(api_key ? { api_key } : {}) }
  }

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setState({ busy: true })
    try {
      await send('PUT', '/admin/agent/connection', fields(e.currentTarget))
      invalidate('/admin/agent')
      setEditing(false)
      setProbe({})
      setState({ done: '已保存，下一个任务起生效' })
    } catch (err) {
      setState({ error: probeError(err) })
    }
  }

  async function remove() {
    if (!(await ask({ title: '改回部署配置的连接？', body: '网页上设置的 Base URL 和 API Key 都会删除，下一个任务起改用部署时配置的。', action: '改回' }))) return
    try {
      await send('DELETE', '/admin/agent/connection')
      invalidate('/admin/agent')
      setState({ done: '已改回部署配置的连接' })
    } catch (err) {
      setState({ error: (err as Error).message })
    }
  }

  // The models chosen on this connection hang under it, joined by a hairline.
  const options = children && <ul className="mt-2 ml-13 border-l border-line pl-4">{children}</ul>
  const found = probe.busy ? '检测中…' : probe.error ? <span className="text-clay-ink">{probe.error}</span> : probe.found !== undefined && <span className="text-ink-soft">连通，有 {probe.found} 个模型可用</span>
  return (
    <>
      <ul className="divide-y divide-line border-y border-line">
        {editing ? (
          <li className="py-3">
            <form onSubmit={save} className="flex flex-wrap items-center gap-3">
              <GlobeSpot className={ART} />
              <label className="field min-w-56 flex-[1.4]">
                <input name="base_url" required autoFocus value={draftUrl} onChange={(e) => setDraftUrl(e.target.value)} maxLength={2048} autoComplete="off" spellCheck={false} placeholder="Base URL，如 https://api.example.com/v1" aria-label="Base URL" className="font-mono text-sm" />
              </label>
              <label className="field min-w-44 flex-1">
                <input
                  name="api_key"
                  type="password"
                  required={!keepKey}
                  maxLength={512}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={keepKey ? '留空沿用当前密钥' : agent.api_key_set ? '换了主机，要填 API Key' : 'API Key'}
                  aria-label="API Key"
                  className="font-mono text-sm"
                />
              </label>
              <button type="button" className="btn-quiet" disabled={probe.busy} onClick={(e) => e.currentTarget.form?.reportValidity() && test(fields(e.currentTarget.form))}>
                检测
              </button>
              <button className="btn-clay" disabled={state.busy}>
                保存
              </button>
              <button type="button" className="btn-quiet" onClick={() => (setEditing(false), setProbe({}), setState({}))}>
                取消
              </button>
            </form>
            {options}
          </li>
        ) : (
          <Row
            lead={<GlobeSpot className={ART} />}
            name="连接"
            tags={<Tag clay={source !== 'config'}>{SOURCE(source)}</Tag>}
            detail={
              <>
                <span className="font-mono">{url?.value ?? '没有 Base URL'}</span>
                {' · 密钥 '}
                <span className="font-mono">{agent.api_key_hint ?? '未设置'}</span>
              </>
            }
            acts={
              <>
                <Act onClick={() => test({})} disabled={probe.busy}>
                  检测
                </Act>
                {source === 'web' && (
                  <Act danger onClick={remove}>
                    删除
                  </Act>
                )}
                <Act onClick={() => (setEditing(true), setDraftUrl(url?.value ?? ''), setProbe({}), setState({}))}>更换</Act>
              </>
            }
          >
            {options}
          </Row>
        )}
      </ul>
      {(state.error || found || state.done) && (
        <p role="status" className="mt-2 text-sm text-stone">
          {state.error ? <span className="text-clay-ink">{state.error}</span> : found || state.done}
        </p>
      )}
    </>
  )
}

/** One of the connection's two models, as an option under it: chosen from what the connection
 * offers (detected when the page opens), or typed in when nothing could be detected. 恢复部署配置
 * goes back to the deployment's model. */
function ModelOption({ name, label, about, setting, models }: { name: string; label: string; about: string; setting: Setting; models?: string[] | string }) {
  const [typing, setTyping] = useState(false)
  const [state, setState] = useState<{ busy?: boolean; error?: string }>({})
  const detected = Array.isArray(models) ? models : undefined

  async function choose(value: string | null) {
    setState({ busy: true })
    try {
      await send(value === null ? 'DELETE' : 'PUT', `/admin/agent/model/${name}`, value === null ? undefined : { value })
      invalidate('/admin/agent')
      setTyping(false)
      setState({})
    } catch (err) {
      setState({ error: err instanceof ApiError && err.status === 422 ? '模型名不能为空、不能有空格，最长 128 个字符' : (err as Error).message })
    }
  }

  const picker = (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger
        aria-label={`选择${label}`}
        disabled={state.busy}
        className={`group flex max-w-64 items-center gap-1 ${actCls} text-ink-soft hover:bg-stone-wash data-[state=open]:bg-stone-wash`}
      >
        <span className="truncate font-mono">{setting.value}</span>
        <ChevronDown size={14} strokeWidth={1.75} className="shrink-0 text-stone transition-transform duration-(--duration-base) group-data-[state=open]:rotate-180" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={6} collisionPadding={16} className="popover max-h-80 min-w-56 overflow-y-auto p-1 data-[state=open]:animate-fade">
          {detected ? (
            <DropdownMenu.RadioGroup value={setting.value} onValueChange={(v) => v !== setting.value && choose(v)}>
              {(detected.includes(setting.value) ? detected : [setting.value, ...detected]).map((m) => (
                <DropdownMenu.RadioItem key={m} value={m} className="option font-mono">
                  <span className="truncate">{m}</span>
                  <DropdownMenu.ItemIndicator>
                    <Check size={14} strokeWidth={2} className="text-clay" />
                  </DropdownMenu.ItemIndicator>
                </DropdownMenu.RadioItem>
              ))}
            </DropdownMenu.RadioGroup>
          ) : (
            <p className="px-2 py-1.5 text-sm text-stone">{models === undefined ? '正在探测可用的模型…' : `没探测到模型：${models}`}</p>
          )}
          <DropdownMenu.Separator className="mx-1 my-1 h-px bg-line" />
          <DropdownMenu.Item className="option" onSelect={() => setTyping(true)}>
            手动填写
          </DropdownMenu.Item>
          {setting.source === 'web' && (
            <DropdownMenu.Item className="option" onSelect={() => choose(null)}>
              恢复部署配置
            </DropdownMenu.Item>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )

  return (
    <li className="py-1.5">
      {typing ? (
        <form
          onSubmit={(e) => (e.preventDefault(), choose(String(new FormData(e.currentTarget).get('value') ?? '').trim()))}
          className="flex items-center gap-3"
        >
          <span className="w-20 shrink-0 text-sm text-ink">{label}</span>
          <label className="field flex-1">
            <input name="value" required autoFocus defaultValue={setting.value} maxLength={128} autoComplete="off" spellCheck={false} aria-label={label} className="font-mono text-sm" />
          </label>
          <button className="btn-clay" disabled={state.busy}>
            保存
          </button>
          <button type="button" className="btn-quiet" onClick={() => (setTyping(false), setState({}))}>
            取消
          </button>
        </form>
      ) : (
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-2 text-sm text-ink">
              {label}
              {setting.source === 'web' && <Tag clay>网页设置</Tag>}
            </p>
            <p className="truncate text-xs text-stone">{about}</p>
          </div>
          {picker}
        </div>
      )}
      {state.error && <p className="mt-1 text-sm text-clay-ink">{state.error}</p>}
    </li>
  )
}

/** The model's connection and the two models chosen on it. The models offered are detected from
 * the connection in use, again whenever it changes. */
function ModelPart({ agent }: { agent: Agent }) {
  const [models, setModels] = useState<{ for: string; list: string[] | string }>()
  const conn = `${agent.model?.base_url.value}|${agent.api_key_hint}`
  useEffect(() => {
    let live = true
    send<{ models: string[] }>('POST', '/admin/agent/probe', {}).then(
      (r) => live && setModels({ for: conn, list: r.models }),
      (err) => live && setModels({ for: conn, list: probeError(err) }),
    )
    return () => {
      live = false
    }
  }, [conn])
  const list = models?.for === conn ? models.list : undefined
  return (
    <Section id="model" title="模型接口">
      <p className="-mt-3 mb-4 text-sm text-stone">下一个任务起生效。改 Base URL 后，之后的请求会带着 API Key 发到新地址。</p>
      <Connection agent={agent}>
        {agent.model && (
          <>
            <ModelOption name="brief_model" label="打标模型" about="摘要与打标" setting={agent.model.brief_model} models={list} />
            <ModelOption name="report_model" label="解读模型" about="论文解读与创新评估" setting={agent.model.report_model} models={list} />
          </>
        )}
      </Connection>
    </Section>
  )
}

/** A prompt in a large dialog: saving applies to what is made from then on (nothing made before
 * is redone). Leaving with changes unsaved asks first. */
function PromptDialog({ prompt, onClose }: { prompt: Prompt; onClose: () => void }) {
  const meta = PROMPTS[prompt.key] ?? { name: prompt.key, about: '', Art: TagSpot }
  const [text, setText] = useState(prompt.text)
  const [state, setState] = useState<{ busy?: boolean; error?: string }>({})
  const dirty = text !== prompt.text
  const missing = (meta.keep ?? []).filter((k) => !text.includes(k))

  async function close() {
    if (!dirty || (await ask({ title: '放弃这次修改？', body: '编辑框里改过的内容不会保存。', action: '放弃' }))) onClose()
  }

  async function save() {
    setState({ busy: true })
    try {
      await send<Prompt>('PUT', `/admin/agent/prompts/${prompt.key}`, { text })
      invalidate('/admin/agent')
      onClose()
    } catch (err) {
      setState({ error: err instanceof ApiError && err.detail ? err.detail : (err as Error).message })
    }
  }

  return (
    <Dialog.Root open onOpenChange={(o) => !o && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30 data-[state=open]:animate-fade" />
        <Dialog.Content
          aria-describedby={undefined}
          onPointerDownOutside={(e) => e.preventDefault()}
          className="popover fixed inset-x-4 top-[6vh] bottom-[6vh] z-50 mx-auto flex max-w-4xl flex-col gap-4 p-6 data-[state=open]:animate-rise"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="font-serif text-lg font-semibold text-ink">编辑「{meta.name}」</Dialog.Title>
              <p className="mt-1 text-sm text-stone">
                {meta.about}
                <span className="ml-2 font-mono text-xs">{prompt.version}</span>
              </p>
            </div>
            <button type="button" className="icon-btn -mr-2" aria-label="关闭" onClick={close}>
              <X size={16} strokeWidth={1.75} />
            </button>
          </div>
          {(meta.json || meta.keep) && (
            // As the reading's quiet notes: a dashed hairline, stone text, no fill.
            <div role="note" className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border border-dashed border-line px-3 py-2 text-sm text-stone">
              {meta.json && (
                <span className="flex items-center gap-2">
                  <Braces size={16} strokeWidth={1.75} />
                  输出 JSON 由代码解析，格式改坏会让{meta.json}失败
                </span>
              )}
              {meta.keep && (
                <span className="flex items-center gap-2">
                  必须保留
                  {meta.keep.map((k) => {
                    const kept = text.includes(k)
                    return (
                      <span key={k} className={`badge font-mono ${kept ? 'bg-sage-wash text-ink-soft' : 'bg-clay-wash text-clay-ink'}`}>
                        {kept ? <Check size={12} strokeWidth={2.5} /> : <X size={12} strokeWidth={2.5} />}
                        {k}
                      </span>
                    )
                  })}
                </span>
              )}
            </div>
          )}
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
            autoFocus
            maxLength={100000}
            aria-label={`${meta.name}的提示词`}
            className="min-h-0 flex-1 resize-none rounded-md border border-line bg-paper p-4 text-sm leading-7 text-ink outline-none focus:border-clay"
          />
          <div className="flex items-center gap-3">
            <p role="status" className="flex-1 text-sm text-clay-ink">
              {missing.length > 0 ? '补回缺少的占位符才能保存' : state.error}
            </p>
            <button type="button" className="btn-quiet" onClick={close}>
              取消
            </button>
            <button type="button" className="btn-clay" disabled={!dirty || missing.length > 0 || state.busy} onClick={save}>
              保存
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** The four prompts, a row each: what it does, its version, whether it was changed here; 编辑
 * opens it in a dialog, 恢复默认 goes back to the file the code ships with. */
function Prompts({ prompts }: { prompts: Prompt[] }) {
  const [editing, setEditing] = useState<string>()
  const [state, setState] = useState<{ error?: string; done?: string }>({})
  const open = prompts.find((p) => p.key === editing)

  async function restore(p: Prompt) {
    const name = PROMPTS[p.key]?.name ?? p.key
    if (!(await ask({ title: `恢复「${name}」的默认提示词？`, body: '网页上改过的内容会被丢弃，之后生成的内容改用默认版本。', action: '恢复默认' }))) return
    try {
      await send<Prompt>('DELETE', `/admin/agent/prompts/${p.key}`)
      invalidate('/admin/agent')
      setState({ done: `「${name}」已恢复默认` })
    } catch (err) {
      setState({ error: (err as Error).message })
    }
  }

  return (
    <Section id="prompts" title="提示词">
      <p className="-mt-3 mb-4 text-sm text-stone">改动只影响之后生成的内容，已经生成的不会重跑。</p>
      <ul className="divide-y divide-line border-y border-line">
        {prompts.map((p) => {
          const meta = PROMPTS[p.key] ?? { name: p.key, about: '', Art: TagSpot }
          return (
            <Row
              key={p.key}
              lead={<meta.Art className={ART} />}
              name={meta.name}
              tags={
                <>
                  <Tag>{p.version.split('+')[0]}</Tag>
                  {p.modified && <Tag clay>已修改</Tag>}
                  {meta.json && <Tag>JSON</Tag>}
                </>
              }
              detail={meta.about}
              acts={
                <>
                  {p.modified && <Act onClick={() => restore(p)}>恢复默认</Act>}
                  <Act onClick={() => (setEditing(p.key), setState({}))}>编辑</Act>
                </>
              }
            />
          )
        })}
      </ul>
      <Status {...state} />
      {open && <PromptDialog key={open.version} prompt={open} onClose={() => setEditing(undefined)} />}
    </Section>
  )
}

/** The model's settings for the whole site: its API key and its prompts. */
export function AgentPage() {
  const agent = useApi<Agent>('/admin/agent')
  return (
    <AdminFrame path="/admin/agent" denied={agent.error?.includes('（403）')}>
      {!agent.data ? (
        agent.error ? <p className="text-sm text-clay-ink">{agent.error}</p> : <Skeleton rows={2} />
      ) : (
        <>
          <ModelPart agent={agent.data} />
          <Prompts prompts={agent.data.prompts} />
        </>
      )}
    </AdminFrame>
  )
}

// ───────────────────────────── Accounts ─────────────────────────────

interface Account {
  id: number
  username: string
  email: string | null
  is_admin: boolean
  created_at: string
}

/** The backend's 409s (in English) in our words; one not here shows as it came. */
const REFUSALS: [string, string][] = [
  ['Another account has this username', '这个用户名已经有人用了'],
  ['Another account has this email', '这个邮箱已经有账号了'],
  ['You cannot remove your own admin role', '不能取消自己的管理员身份'],
  ['You cannot delete your own account', '不能删除自己的账号'],
  ['Change your own password on your profile', '自己的密码请在右上角「修改密码」里改'],
]

/** Why a change was refused, in our words. Losing the admin role (another admin just took it)
 * also refreshes who we are, so the page and the menu follow; a vanished account (another
 * admin deleted it) refreshes the list. */
function refusal(err: unknown) {
  if (!(err instanceof ApiError)) return (err as Error).message
  if (err.status === 403 || err.detail?.startsWith('You are no longer an admin')) {
    invalidate('/me')
    return '你已不再是管理员'
  }
  if (err.status === 409) return REFUSALS.find(([en]) => err.detail?.startsWith(en))?.[1] ?? err.detail ?? err.message
  if (err.status === 422) return '用户名只能用字母、数字和 . _ -，邮箱要写对，密码至少 8 位'
  if (err.status === 404) {
    invalidate('/admin/users')
    return '这个账号已经不在了'
  }
  return err.detail ?? err.message
}

/** An account's role and what can be done to it: the role opens a menu to switch it,
 * set a new password (in a dialog) or delete the account. One's own account cannot be changed
 * here (one's own password is changed from the header menu, with the old one). */
function AccountMenu({ user, self, onChange }: { user: Account; self: boolean; onChange: (error?: string, done?: string) => void }) {
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState('')

  async function reset(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const password = String(new FormData(e.currentTarget).get('password') ?? '')
    try {
      await send('PUT', `/admin/users/${user.id}/password`, { password })
      setResetting(false)
      onChange(undefined, `已重置 ${user.username} 的密码，他需要用新密码重新登录`)
    } catch (err) {
      setResetError(refusal(err))
    }
  }

  async function role() {
    try {
      await send('PATCH', `/admin/users/${user.id}`, { is_admin: !user.is_admin })
      invalidate('/admin/users')
      onChange(undefined, `${user.username} 已设为${user.is_admin ? '成员' : '管理员'}`)
    } catch (err) {
      onChange(refusal(err))
    }
  }
  async function remove() {
    const ok = await ask({
      title: `删除账号“${user.username}”？`,
      body: '他会立即被登出。他的收藏、分组、笔记和个人令牌会一起删除；他改过的标签和方向保留。这一步不能撤回。',
      action: '删除账号',
    })
    if (!ok) return
    try {
      await send('DELETE', `/admin/users/${user.id}`)
      invalidate('/admin/users')
      onChange(undefined, `已删除 ${user.username}`)
    } catch (err) {
      onChange(refusal(err))
    }
  }
  return (
    <>
      <DropdownMenu.Root modal={false}>
        <DropdownMenu.Trigger
          disabled={self}
          title={self ? '不能在这里改自己的账号' : undefined}
          className={`group flex items-center gap-1 ${actCls} data-[state=open]:bg-stone-wash ${user.is_admin ? 'text-clay-ink' : 'text-ink-soft'} enabled:hover:bg-stone-wash`}
        >
          {user.is_admin ? '管理员' : '成员'}
          <ChevronDown size={14} strokeWidth={1.75} className="text-stone transition-transform duration-(--duration-base) group-data-[state=open]:rotate-180" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={6} className="popover min-w-36 p-1 data-[state=open]:animate-fade">
            <DropdownMenu.Item className="option" onSelect={role}>
              {user.is_admin ? '设为成员' : '设为管理员'}
            </DropdownMenu.Item>
            <DropdownMenu.Item className="option" onSelect={() => (setResetError(''), setResetting(true))}>
              重置密码
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="mx-1 my-1 h-px bg-line" />
            <DropdownMenu.Item className="option text-clay-ink" onSelect={remove}>
              删除账号
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <Dialog.Root open={resetting} onOpenChange={setResetting}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30 data-[state=open]:animate-fade" />
          <Dialog.Content className="popover fixed top-[42%] left-1/2 z-50 w-[min(24rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 p-6 data-[state=open]:animate-rise">
            <Dialog.Title className="font-serif text-lg font-semibold text-ink">重置 {user.username} 的密码</Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-ink-soft">他已登录的地方都会退出，要用新密码重新登录；个人令牌不受影响。</Dialog.Description>
            <form onSubmit={reset} className="mt-4 space-y-3">
              <label className="field">
                <input name="password" required autoFocus minLength={8} maxLength={1024} autoComplete="new-password" spellCheck={false} placeholder="新密码，至少 8 位" aria-label="新密码" />
              </label>
              {resetError && <p className="text-sm text-clay-ink">{resetError}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <Dialog.Close className="btn-quiet h-9 px-4 text-sm">取消</Dialog.Close>
                <button className="btn-clay h-9 px-4 text-sm">重置密码</button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  )
}

/** Accounts: a new one from the heading (username, email, first password and role
 * in one row), everyone below with their role, which opens what can be done to them. */
export function UsersPage() {
  const users = useApi<Account[]>('/admin/users')
  const me = useApi<{ username: string }>('/me')
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<{ busy?: boolean; error?: string; done?: string }>({})

  async function add(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const data = new FormData(e.currentTarget)
    const [username, email, password, role] = ['username', 'email', 'password', 'role'].map((k) => String(data.get(k) ?? '').trim())
    setState({ busy: true })
    try {
      const made = await send<Account>('POST', '/admin/users', { username, email, password, is_admin: role === 'admin' })
      setOpen(false)
      setState({ done: `已新增 ${made.username}，把初始密码告诉对方，登录后可以在右上角菜单里改` })
      invalidate('/admin/users')
    } catch (err) {
      setState({ error: refusal(err) })
    }
  }

  return (
    <AdminFrame path="/admin/users" denied={users.error?.includes('（403）')}>
      <Section
        id="accounts"
        title={
          <>
            账号
            {users.data && <span className="ml-2 font-sans text-sm font-normal text-stone tabular-nums">{users.data.length}</span>}
          </>
        }
        aside={
          <Opener open={open} onClick={() => (setOpen(!open), setState({}))}>
            新增账号
          </Opener>
        }
      >
        <Fold open={open}>
          <form onSubmit={add} className="flex flex-wrap gap-3">
            <label className="field min-w-32 flex-1">
              <input name="username" required autoFocus maxLength={64} pattern="[A-Za-z0-9._\-]+" autoComplete="off" spellCheck={false} placeholder="用户名" aria-label="用户名" title="字母、数字和 . _ -" />
            </label>
            <label className="field min-w-44 flex-[1.4]">
              <input name="email" type="email" required maxLength={254} autoComplete="off" placeholder="邮箱" aria-label="邮箱" />
            </label>
            <label className="field min-w-36 flex-1">
              <input name="password" required minLength={8} maxLength={1024} autoComplete="new-password" spellCheck={false} placeholder="初始密码，至少 8 位" aria-label="初始密码" />
            </label>
            <label className="field">
              <select name="role" defaultValue="member" aria-label="角色" className="h-full appearance-none bg-transparent pr-5 outline-none">
                <option value="member">成员</option>
                <option value="admin">管理员</option>
              </select>
              <ChevronDown size={14} strokeWidth={1.75} className="pointer-events-none -ml-6 text-stone" />
            </label>
            <button className="btn-clay" disabled={state.busy}>
              新增
            </button>
          </form>
        </Fold>
        {users.data ? (
          <ul className="divide-y divide-line border-y border-line">
            {users.data.map((u) => {
              const self = u.username === me.data?.username
              return (
                <Row
                  key={u.id}
                  lead={<Avatar name={u.username} className="size-8 text-sm" />}
                  name={u.username}
                  tags={self && <Tag>你</Tag>}
                  detail={`${u.email ?? '没有邮箱'} · 加入于 ${day(u.created_at)}`}
                  acts={<AccountMenu user={u} self={self} onChange={(error, done) => setState({ error, done })} />}
                />
              )
            })}
          </ul>
        ) : (
          <p className="text-sm text-stone">{users.error ?? '加载中…'}</p>
        )}
        <Status {...state} />
      </Section>
    </AdminFrame>
  )
}

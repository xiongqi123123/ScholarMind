import { Check } from 'lucide-react'
import * as m from 'motion/react-m'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArxivPicker, Models, VenueImport, type Agent } from './Admin'
import { invalidate, saveArxiv, sameCategories, send, useApi, type Arxiv } from './api'
import { AgentSpot, SproutBook, StackSpot, SunriseSpot } from './Illustrations'
import { pageIn } from './motion'
import { navigate } from './router'
import { toast } from './toast'
import { Mark, ScrollCue, Skeleton } from './ui'

const STEPS = [
  ['模型连接', AgentSpot],
  ['arXiv 类别', SunriseSpot],
  ['顶会', StackSpot],
  ['完成', null],
] as const

const link = 'text-clay-ink hover:underline'

/** Where the wizard is: the steps in order, those done ticked (each a way back), the current one
 * in clay. A column in the rail; on a narrow screen a row above the step, naming the current one. */
function Progress({ step, onPick }: { step: number; onPick: (n: number) => void }) {
  const finished = step === STEPS.length - 1
  return (
    <nav aria-label="初始化步骤">
      <p className="mb-5 hidden font-serif text-md font-semibold text-ink lg:block">初始化</p>
      <ol className="flex items-center gap-3 lg:flex-col lg:items-stretch lg:gap-0">
        {STEPS.map(([name], i) => {
          const done = i < step
          const current = i === step
          return (
            <li key={name} className={`group/step relative flex items-center lg:pb-8 lg:last:pb-0 ${current ? 'min-w-0 flex-1 lg:flex-none' : ''}`}>
              <span aria-hidden="true" className="absolute top-9 bottom-1 left-3.5 hidden w-px -translate-x-1/2 bg-line group-last/step:hidden lg:block" />
              <button
                type="button"
                disabled={!done || finished}
                onClick={() => onPick(i)}
                aria-current={current ? 'step' : undefined}
                aria-label={done ? `${name}（已完成，回到这一步）` : undefined}
                className="group/btn flex min-w-0 items-center gap-3 rounded-full text-left disabled:pointer-events-none"
              >
                <span
                  className={`grid size-7 shrink-0 place-items-center rounded-full border font-serif text-sm font-semibold transition-colors duration-(--duration-base) ${
                    current ? 'border-clay bg-clay text-paper-raised' : done ? 'border-sage bg-sage-wash text-ink-soft group-hover/btn:border-clay group-hover/btn:text-clay-ink' : 'border-line text-stone'
                  }`}
                >
                  {done ? <Check size={14} strokeWidth={2.5} /> : i + 1}
                </span>
                <span
                  className={`truncate text-sm ${current ? 'font-medium text-clay-ink' : 'sr-only lg:not-sr-only'} ${done ? 'text-ink-soft group-hover/btn:text-clay-ink' : current ? '' : 'text-stone'}`}
                >
                  {name}
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

/** A step's heading: its drawing, which step of how many, its name and what it is for. */
function Head({ step, title, children }: { step: number; title: string; children: ReactNode }) {
  const Art = STEPS[step][1]
  return (
    <header className="mb-5 flex shrink-0 items-start gap-4 lg:mb-8">
      {Art && <Art className="size-12 shrink-0 sm:size-16" />}
      <div className="min-w-0">
        <p className="text-sm text-stone tabular-nums">
          第 {step + 1} 步，共 {STEPS.length} 步
        </p>
        <h1 className="mt-0.5 font-serif text-xl font-semibold text-ink">{title}</h1>
        <div className="mt-2 text-sm text-ink-soft [@media(max-height:36rem)]:hidden">{children}</div>
      </div>
    </header>
  )
}

interface StepProps {
  onBack?: () => void
  onLater: () => void
  onNext: () => void
}

/** A step's own part, between its heading and its bar: the only thing that scrolls, when long. */
function Body({ children }: { children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  return (
    <>
      <div ref={box} className="scroll-fade -mx-1 min-h-0 flex-1 px-1 pb-4">
        {children}
      </div>
      <ScrollCue container={box} />
    </>
  )
}

/** The bar at the bottom of a step, in the same place on every one: why it cannot go on yet (or
 * that it can), then 稍后再说 at the start and back and next at the end. */
function Footer({ onBack, onLater, status, children }: Partial<Omit<StepProps, 'onNext'>> & { status?: ReactNode; children: ReactNode }) {
  return (
    <div className="shrink-0 border-t border-line py-4 lg:pb-6">
      <div role="status" className="mb-3 flex flex-wrap items-center justify-end gap-x-2 text-right text-sm text-ink-soft empty:hidden">
        {status}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-3">
        {onLater && (
          <button type="button" onClick={onLater} className="mr-auto text-sm text-stone transition-colors duration-(--duration-fast) hover:text-clay-ink hover:underline">
            稍后再说
          </button>
        )}
        {onBack && (
          <button type="button" className="btn-quiet" onClick={onBack}>
            上一步
          </button>
        )}
        {children}
      </div>
    </div>
  )
}

/** ① The model's connection (the admin console's own form): on only once it answers. One set up
 * when deploying passes as it is. */
function ModelStep({ onNext, ...nav }: StepProps) {
  const agent = useApi<Agent>('/admin/agent')
  const [models, setModels] = useState<string[] | string>()
  const a = agent.data
  // A chosen model the connection does not list is likely a typo, but some providers list few.
  const unlisted =
    Array.isArray(models) && a?.model
      ? [
          ['打标模型', a.model.brief_model.value],
          ['解读模型', a.model.report_model.value],
        ].filter(([, v]) => !models.includes(v))
      : []
  const ok = Array.isArray(models)
  return (
    <>
      <Head step={0} title="连接模型">
        <p>ScholarMind 用 OpenAI 兼容的接口给论文写摘要、打标签、写解读。填上 Base URL 和 API Key，检测连通后保存，再选打标签和写解读用的模型。</p>
        {a?.api_key_source === 'env' && <p className="mt-1">部署时已经配好了连接，检测通过就可以继续。</p>}
      </Head>
      <Body>
        {a ? <Models agent={a} edit={!a.api_key_set} onModels={setModels} /> : agent.error ? <p className="text-sm text-clay-ink">{agent.error}</p> : <Skeleton rows={2} />}
      </Body>
      <Footer
        {...nav}
        status={
          !a ? undefined : models === undefined ? (
            '正在检测连接…'
          ) : ok ? (
            <>
              <Mark tone="success" />
              连接正常，有 {models.length} 个模型可用
              {unlisted.length > 0 && <span className="basis-full text-clay-ink">{unlisted.map(([l, v]) => `${l} ${v}`).join('、')} 不在这个接口的模型列表里，确认一下名字</span>}
            </>
          ) : (
            <>
              <Mark tone="error" />
              <span className="text-clay-ink">{models}</span>
            </>
          )
        }
      >
        <button type="button" className="btn-clay" disabled={!ok} onClick={onNext}>
          下一步
        </button>
      </Footer>
    </>
  )
}

/** ② The arXiv categories, saved when going on (only if changed). */
function ArxivStep({ onNext, ...nav }: StepProps) {
  const arxiv = useApi<Arxiv>('/admin/arxiv')
  const [picked, setPicked] = useState<string[]>() // until changed, what is set
  const [state, setState] = useState<{ busy?: boolean; error?: string }>({})
  const value = picked ?? arxiv.data?.categories ?? []

  async function next() {
    if (arxiv.data && !sameCategories(value, arxiv.data.categories)) {
      setState({ busy: true })
      const error = await saveArxiv(value)
      if (error) return setState({ error })
    }
    onNext()
  }

  return (
    <>
      <Head step={1} title="选 arXiv 类别">
        每天同步这些类别新公告的论文，AI 给每篇写中文摘要、打标签。类别越多，每天要打标签的论文越多。以后可以在「管理后台 › 数据源」里改。
      </Head>
      <Body>
        {arxiv.data ? (
          <ArxivPicker value={value} onChange={(next) => (setPicked(next), setState({}))} />
        ) : arxiv.error ? (
          <p className="text-sm text-clay-ink">{arxiv.error}</p>
        ) : (
          <Skeleton rows={1} />
        )}
      </Body>
      <Footer {...nav} status={state.error && <span className="text-clay-ink">{state.error}</span>}>
        <button type="button" className="btn-clay" disabled={!arxiv.data || !value.length || state.busy} onClick={next}>
          下一步
        </button>
      </Footer>
    </>
  )
}

/** ③ Conferences (the sources page's own form), none at all being fine. The estimate is in the bar,
 * and its button queues what is ticked (a large import asked about first) before going on; going on
 * runs the first arXiv sync and marks the setup done. */
function VenueStep({ onNext, onQueued, ...nav }: StepProps & { onQueued: () => void }) {
  const [state, setState] = useState<{ busy?: boolean; error?: string }>({})
  return (
    <VenueImport>
      {({ form, estimate, ticked, plan, queue }) => {
        const importing = ticked && plan?.editions.length !== 0 // not when all of it is in already
        async function next() {
          setState({ busy: true })
          try {
            if (importing) {
              const done = await queue()
              if (!done) return setState({})
              if (done.queued) onQueued()
            }
            await send('POST', '/admin/sync')
            await send('PUT', '/admin/setup/done')
            invalidate('/admin/setup')
            onNext()
          } catch (err) {
            setState({ error: (err as Error).message })
          }
        }
        return (
          <>
            <Head step={2} title="导入顶会论文">
              勾选会议和起始年份，底部会估算篇数和打标签要用的 token。导入在后台一届一届进行。也可以一个都不选，以后在「管理后台 › 数据源」里导入。
            </Head>
            <Body>{form}</Body>
            <Footer
              {...nav}
              status={
                <>
                  <div className="basis-full">{estimate}</div>
                  {state.error && <span className="basis-full text-clay-ink">{state.error}</span>}
                </>
              }
            >
              <button type="button" className="btn-clay" disabled={state.busy || (ticked && !plan)} onClick={next}>
                {state.busy ? (importing ? '正在导入…' : '正在完成…') : importing ? '导入并继续' : '下一步'}
              </button>
            </Footer>
          </>
        )
      }}
    </VenueImport>
  )
}

/** ④ What happens now, and the way into the library. */
function Done({ imported }: { imported: boolean }) {
  return (
    <>
      <Body>
        <div className="flex flex-col items-center py-4 text-center">
          <SproutBook className="w-60" />
          <h1 className="mt-6 font-serif text-xl font-semibold text-ink">设置好了</h1>
          <ul className="mt-6 max-w-[28rem] space-y-3 text-left text-sm text-ink-soft">
            <li className="flex gap-3">
              <span className="flex w-5 shrink-0 justify-center pt-0.5">
                <Mark tone="success" />
              </span>
              <span>
                arXiv 已开始同步，几分钟后新论文会出现在
                <a href="/" className={link}>
                  每日新增
                </a>
                里。
              </span>
            </li>
            <li className="flex gap-3">
              <span className="flex w-5 shrink-0 justify-center pt-0.5">
                <Mark tone={imported ? 'success' : 'hint'} />
              </span>
              {imported ? (
                <span>
                  选了的会议在后台一届一届导入，进度在
                  <a href="/admin/sources" className={link}>
                    管理后台 › 数据源
                  </a>
                  里看，也可以在那里追加。
                </span>
              ) : (
                <span>
                  顶会论文随时可以在
                  <a href="/admin/sources" className={link}>
                    管理后台 › 数据源
                  </a>
                  里导入。
                </span>
              )}
            </li>
            <li className="flex gap-3">
              <span className="flex w-5 shrink-0 justify-center pt-0.5">
                <Mark tone="hint" />
              </span>
              <span>
                成员账号在
                <a href="/admin/users" className={link}>
                  管理后台 › 账户管理
                </a>
                里添加。
              </span>
            </li>
          </ul>
        </div>
      </Body>
      <Footer>
        <a href="/library" className="btn-clay">
          进入论文库
        </a>
      </Footer>
    </>
  )
}

/** A new deployment's first run, for its admin: connect the model, pick the arXiv categories,
 * import conferences, then a first sync. Steps done can be gone back to; 稍后再说 leaves, and the
 * app no longer opens the wizard by itself. Members are sent to the daily page. */
export default function Setup() {
  const me = useApi<{ is_admin: boolean }>('/me')
  const [step, setStep] = useState(0)
  const [imported, setImported] = useState(false)
  useEffect(() => {
    if (me.data && !me.data.is_admin) navigate('/', true)
  }, [me.data])

  async function later() {
    try {
      await send('PUT', '/admin/setup/done')
      invalidate('/admin/setup')
      navigate('/')
    } catch (err) {
      toast({ tone: 'error', title: '没能跳过初始化', description: (err as Error).message })
    }
  }

  const nav = { onLater: later, onBack: step > 0 ? () => setStep(step - 1) : undefined }
  // As tall as the window under the header, which itself never scrolls: only a step's body does.
  return (
    <main className="fixed inset-x-0 top-16 bottom-0">
      <title>初始化 · ScholarMind</title>
      <div className="frame flex h-full max-w-page flex-col pt-5 lg:grid lg:grid-cols-12 lg:grid-rows-1 lg:gap-x-8 lg:pt-8">
        <aside className="relative mb-5 shrink-0 lg:col-span-3 lg:mb-0">
          <div className="lg:mx-auto lg:w-1/2 lg:min-w-40">
            <Progress step={step} onPick={setStep} />
          </div>
          <span className="hand-rule-v hidden lg:block" aria-hidden="true" />
        </aside>
        <div className="flex min-h-0 flex-1 flex-col lg:col-span-7 lg:col-start-4">
          {!me.data?.is_admin ? (
            <Skeleton rows={2} />
          ) : (
            <m.div key={step} {...pageIn()} className="flex min-h-0 flex-1 flex-col">
              {step === 0 && <ModelStep {...nav} onNext={() => setStep(1)} />}
              {step === 1 && <ArxivStep {...nav} onNext={() => setStep(2)} />}
              {step === 2 && <VenueStep {...nav} onNext={() => setStep(3)} onQueued={() => setImported(true)} />}
              {step === 3 && <Done imported={imported} />}
            </m.div>
          )}
        </div>
      </div>
    </main>
  )
}

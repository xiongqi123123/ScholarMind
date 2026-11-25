import { ArrowRight, X } from 'lucide-react'
import * as m from 'motion/react-m'
import { Dialog } from 'radix-ui'
import { useRef, useState, useSyncExternalStore } from 'react'
import { Avatar, AvatarPicker } from './Avatar'
import { BookSpot, KeySpot, StarSpot, SunriseSpot, TagSpot } from './Illustrations'
import { rowIn } from './motion'
import { PasswordDialog } from './Password'
import { useStored } from './prefs'

/** The places worth knowing, a card each, with a way there where there is one. */
const CARDS = [
  {
    Art: SunriseSpot,
    title: '每日新增',
    text: 'arXiv 每天公告的新论文，一天一页。每篇都有中文摘要和领域、方向标签，扫一眼就知道值不值得细读。',
    href: '/',
    go: '看今天的',
  },
  {
    Art: TagSpot,
    title: '论文库',
    text: '所有论文都在这里，导入的顶会论文也在。左栏按领域、方向、类型、来源和年份筛选，可以多选；上方可以搜索。',
    href: '/library',
    go: '去论文库',
  },
  {
    Art: BookSpot,
    title: '阅读页',
    text: '论文上的「阅读论文」打开它：PDF 和 AI 解读左右并排。划选一段文字，可以记成批注，也可以就这段向论文提问，回答会标出原文页码。',
  },
  {
    Art: StarSpot,
    title: '收藏和笔记',
    text: '点星标收藏，收藏可以分组，都在「精选」里。读论文时的批注和随手写的笔记，在阅读页右栏。',
    href: '/starred',
    go: '去精选',
  },
  {
    Art: KeySpot,
    title: '接入 AI 助手',
    text: '在个人信息页新建一个令牌，Claude Code 等 AI 助手就能通过 MCP 搜论文、读解读、看你的笔记。',
    href: '/me',
    go: '去新建令牌',
  },
]

// The profile page opens it again (WelcomeAgain); mounted once in App.
let asked = false
const listeners = new Set<() => void>()
const setAsked = (v: boolean) => {
  asked = v
  listeners.forEach((f) => f())
}
const subscribe = (f: () => void) => (listeners.add(f), () => void listeners.delete(f))

/** The way back to it, on the profile page. */
export function WelcomeAgain({ className }: { className?: string }) {
  return (
    <button type="button" onClick={() => setAsked(true)} className={className}>
      重新查看欢迎页
    </button>
  )
}

/** A first look around, once per person in this browser (`ready`: an admin has the setup wizard
 * behind them): the places worth knowing as cards, the avatar to change right there and a word on
 * the password, which opens the usual dialog over it. Closing it in any way counts as seen. */
export function Welcome({ me, ready }: { me: { username: string; avatar?: string | null }; ready: boolean }) {
  const [seen, setSeen] = useStored(`sm.welcomed.${me.username}`, '')
  const again = useSyncExternalStore(subscribe, () => asked)
  const [changing, setChanging] = useState(false)
  const start = useRef<HTMLButtonElement>(null)
  const close = () => {
    setSeen('1')
    setAsked(false)
  }

  return (
    <Dialog.Root open={again || (ready && !seen)} onOpenChange={(open) => !open && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30 data-[state=open]:animate-fade" />
        <Dialog.Content
          onOpenAutoFocus={(e) => {
            e.preventDefault()
            start.current?.focus()
          }}
          className="popover fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100dvh-2rem)] w-[min(52rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col p-0 data-[state=open]:animate-rise"
        >
          <div className="flex items-start gap-5 px-6 pt-6 pb-5">
            <div className="hidden flex-col items-center gap-1.5 sm:flex">
              {me.avatar !== undefined ? <AvatarPicker name={me.username} id={me.avatar} /> : <Avatar name={me.username} className="size-24 text-2xl" />}
              {me.avatar !== undefined && <span className="text-xs text-stone">点头像换一个</span>}
            </div>
            <div className="min-w-0 flex-1 sm:pt-3">
              <Dialog.Title className="font-serif text-xl font-semibold break-words text-ink">欢迎，{me.username}</Dialog.Title>
              <Dialog.Description className="mt-2 text-sm text-ink-soft">
                ScholarMind 每天收 arXiv 的新论文，也能导入顶会论文。AI 给每篇写中文摘要、打标签，想细读的再看全文解读。这几处最常用：
              </Dialog.Description>
            </div>
            <Dialog.Close className="icon-btn -mt-1 -mr-2 shrink-0" aria-label="关闭">
              <X size={16} strokeWidth={1.75} />
            </Dialog.Close>
          </div>

          <div className="scroll-fade min-h-0 flex-1 px-6 pb-2">
            <ul className="grid gap-3 sm:grid-cols-2">
              {CARDS.map((c, i) => (
                <m.li key={c.title} {...rowIn(i)} className={`flex gap-4 rounded-lg border border-line bg-paper p-4 ${i === CARDS.length - 1 ? 'sm:col-span-2' : ''}`}>
                  <c.Art className="size-10 shrink-0 sm:size-14" />
                  <div className="min-w-0">
                    <h3 className="font-medium text-ink">{c.title}</h3>
                    <p className="mt-1 text-sm text-ink-soft">{c.text}</p>
                    {c.href && (
                      <a href={c.href} onClick={close} className="mt-2 inline-flex items-center gap-1 text-sm text-clay-ink hover:underline">
                        {c.go}
                        <ArrowRight size={14} strokeWidth={1.75} />
                      </a>
                    )}
                  </div>
                </m.li>
              ))}
            </ul>
            {/* As the prompts' quiet notes: a dashed hairline, no fill. */}
            <div role="note" className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-dashed border-line px-4 py-3 text-sm text-stone">
              <span className="min-w-0 flex-1">密码是管理员给你的？建议换成只有自己知道的。</span>
              <button type="button" onClick={() => setChanging(true)} className="shrink-0 text-clay-ink hover:underline">
                修改密码
              </button>
              {me.avatar !== undefined && (
                <a href="/me" onClick={close} className="shrink-0 text-clay-ink hover:underline sm:hidden">
                  换头像
                </a>
              )}
            </div>
          </div>

          <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
            <Dialog.Close className="btn-quiet">跳过</Dialog.Close>
            <Dialog.Close ref={start} className="btn-clay">
              开始使用
            </Dialog.Close>
          </div>
          <PasswordDialog open={changing} onOpenChange={setChanging} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

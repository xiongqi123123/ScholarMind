import { ChevronDown, KeyRound, LayoutDashboard, LogOut, UserRound } from 'lucide-react'
import { LazyMotion, MotionConfig, domAnimation } from 'motion/react'
import { DropdownMenu, Popover, Tooltip } from 'radix-ui'
import { useEffect, useRef, useState } from 'react'
import { AgentPage, SourcesPage, UsersPage } from './Admin'
import { logout, useApi } from './api'
import { Avatar } from './Avatar'
import Daily from './Daily'
import Discover from './Discover'
import Login from './Login'
import PaperView from './PaperView'
import { PasswordDialog } from './Password'
import Profile from './Profile'
import Reader from './Reader'
import { useNow, useWatched, useWatchReadings, watchedLine } from './readings'
import { navigate, rememberList, useLocation } from './router'
import Setup from './Setup'
import Starred from './Starred'
import { Asker, ScrollCue, Toaster } from './ui'
import { Welcome } from './Welcome'

const NAV = [
  ['/', '每日新增'],
  ['/library', '论文库'],
  ['/starred', '精选'],
] as const

/** Same frame on every page, so the logo, the sections and the account controls never move. */
function Header({ pathname }: { pathname: string }) {
  const me = useApi<{ username: string; avatar?: string | null; is_admin?: boolean }>('/me') // left out when the backend has no /api/me
  return (
    <header className="sticky top-0 z-10 bg-paper/85 backdrop-blur-sm">
      <div className="frame flex h-14 max-w-page items-center gap-4 sm:gap-8">
        <a href="/" className="flex shrink-0 items-center gap-2 font-serif text-lg font-medium text-ink" aria-label="ScholarMind">
          <img src="/logo.png" alt="" className="size-7 rounded-sm" />
          <span className="hidden sm:inline">ScholarMind</span>
        </a>
        <nav aria-label="栏目" className="flex min-w-0 items-center gap-1 sm:gap-2">
          {NAV.map(([href, label]) => (
            <a
              key={href}
              href={href}
              aria-current={pathname === href ? 'page' : undefined}
              className="relative px-2 py-1 text-sm whitespace-nowrap text-stone transition-colors duration-(--duration-fast) hover:text-ink aria-[current=page]:font-medium aria-[current=page]:text-ink"
            >
              {label}
              {pathname === href && <span className="absolute inset-x-2 -bottom-1 h-0.5 rounded-full bg-clay" />}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex shrink-0 items-center gap-3">
          <Readings />
          <UserMenu username={me.data?.username} avatar={me.data?.avatar} admin={!!me.data?.is_admin} current={pathname === '/me' || pathname.startsWith('/admin')} />
        </div>
      </div>
      <div className="hand-rule" />
    </header>
  )
}

/** The readings asked for here and not done yet, as a pill that lists where each one is; a card
 * at the bottom right says when one is done (readings.ts watches them). */
function Readings() {
  const list = useWatched()
  const now = useNow(list.length > 0)
  if (!list.length) return null
  return (
    <Popover.Root>
      <Popover.Trigger className="flex h-7 items-center gap-1.5 rounded-full bg-clay-wash px-2.5 text-xs whitespace-nowrap text-clay-ink outline-none transition-colors duration-(--duration-fast) hover:bg-clay/20 focus-visible:ring-2 focus-visible:ring-clay/40">
        <span className="size-1.5 animate-pulse rounded-full bg-clay" />
        {list.length} 篇解读中
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} className="popover w-80 p-2 data-[state=open]:animate-fade">
          <p className="px-2 pt-1 pb-2 text-xs text-stone">好了会在右下角告诉你，离开这一页也会</p>
          <ul>
            {list.map((w) => (
              <li key={w.id}>
                <Popover.Close asChild>
                  <a href={`/papers/${w.id}`} className="block rounded-md px-2 py-2 transition-colors duration-(--duration-fast) hover:bg-stone-wash">
                    <span className="line-clamp-2 text-sm text-ink">{w.title}</span>
                    <span className="mt-0.5 block text-xs text-clay-ink">{watchedLine(w, now)}</span>
                  </a>
                </Popover.Close>
              </li>
            ))}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

/** Watches the readings asked for here, on every page (readings.ts). */
function Watcher() {
  useWatchReadings()
  return null
}

/** First steps. An admin of a new, empty deployment is taken to the setup wizard when the app opens
 * (once a visit, so the header still leads elsewhere); everyone sees the welcome once, an admin
 * after the wizard. */
function Onboarding({ pathname }: { pathname: string }) {
  const me = useApi<{ username: string; avatar?: string | null; is_admin?: boolean }>('/me')
  const admin = !!me.data?.is_admin
  const setup = useApi<{ done: boolean; papers: number }>(admin ? '/admin/setup' : null)
  const fresh = !!setup.data && !setup.data.done && setup.data.papers === 0
  const decided = useRef(false)
  useEffect(() => {
    if (!setup.data || decided.current) return
    decided.current = true
    if (fresh && pathname !== '/setup') navigate('/setup', true)
  }, [setup.data, fresh, pathname])
  // A backend without the setup endpoint (an error) has no wizard to wait for.
  const ready = pathname !== '/setup' && (!admin || !!setup.error || (!!setup.data && !fresh))
  return me.data ? <Welcome key={me.data.username} me={me.data} ready={ready} /> : null
}

/** The account at the right end of the header: the name opens a menu with the profile, changing
 * the password (in a dialog, without leaving the page), for admins the admin console, and logging
 * out. */
function UserMenu({ username, avatar, admin, current }: { username?: string; avatar?: string | null; admin: boolean; current: boolean }) {
  const [changing, setChanging] = useState(false)
  return (
    <>
      <DropdownMenu.Root modal={false}>
        <DropdownMenu.Trigger
          aria-label={username ?? '账号'}
          data-current={current || undefined}
          className="group -mr-2 flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2 text-sm text-stone outline-none transition-colors duration-(--duration-fast) hover:text-ink focus-visible:bg-clay-wash data-current:text-ink data-[state=open]:text-ink"
        >
          {username ? (
            <Avatar name={username} id={avatar} className="size-6 text-xs" />
          ) : (
            <UserRound size={16} strokeWidth={1.75} />
          )}
          <span className="hidden sm:inline">{username}</span>
          <ChevronDown size={14} strokeWidth={1.75} className="transition-transform duration-(--duration-base) group-data-[state=open]:rotate-180" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={6} className="popover min-w-36 p-1 data-[state=open]:animate-fade">
            <DropdownMenu.Item className="option" onSelect={() => navigate('/me')}>
              <span className="flex items-center gap-2">
                <UserRound size={14} strokeWidth={1.75} />
                个人信息
              </span>
            </DropdownMenu.Item>
            <DropdownMenu.Item className="option" onSelect={() => setChanging(true)}>
              <span className="flex items-center gap-2">
                <KeyRound size={14} strokeWidth={1.75} />
                修改密码
              </span>
            </DropdownMenu.Item>
            {admin && (
              <DropdownMenu.Item className="option" onSelect={() => navigate('/admin/agent')}>
                <span className="flex items-center gap-2">
                  <LayoutDashboard size={14} strokeWidth={1.75} />
                  管理后台
                </span>
              </DropdownMenu.Item>
            )}
            <DropdownMenu.Separator className="mx-1 my-1 h-px bg-line" />
            <DropdownMenu.Item className="option" onSelect={logout}>
              <span className="flex items-center gap-2">
                <LogOut size={14} strokeWidth={1.75} />
                退出登录
              </span>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <PasswordDialog open={changing} onOpenChange={setChanging} />
    </>
  )
}

export default function App() {
  const { pathname, search } = useLocation()
  const paper = pathname.match(/^\/papers\/(\d+)(\/read)?$/)
  const list = pathname === '/' || pathname === '/library' || pathname === '/starred'
  const admin = pathname === '/admin/agent' || pathname === '/admin/users' || pathname === '/admin/sources'
  const known = list || pathname === '/login' || pathname === '/me' || pathname === '/setup' || admin || !!paper
  // Unknown paths go to the daily page, which in turn sends logged-out visitors to login.
  useEffect(() => {
    if (pathname === '/admin') navigate('/admin/agent', true)
    else if (!known) navigate('/', true)
  }, [known, pathname])
  useEffect(() => {
    if (list) rememberList(pathname + search)
  }, [list, pathname, search])

  let page = null
  if (paper) page = paper[2] ? <Reader key={paper[1]} id={paper[1]} /> : <PaperView key={paper[1]} id={paper[1]} />
  else if (pathname === '/library') page = <Discover />
  else if (pathname === '/starred') page = <Starred />
  else if (pathname === '/') page = <Daily />
  else if (pathname === '/me') page = <Profile />
  else if (pathname === '/admin/agent') page = <AgentPage />
  else if (pathname === '/admin/users') page = <UsersPage />
  else if (pathname === '/admin/sources') page = <SourcesPage />
  else if (pathname === '/setup') page = <Setup />

  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        {pathname === '/login' ? (
          <Login />
        ) : (
          <Tooltip.Provider delayDuration={300}>
            {/* The reader is a page of its own, under its own bar. */}
            {!paper?.[2] && <Header pathname={pathname} />}
            {page}
            <ScrollCue />
            <Asker />
            <Toaster />
            <Watcher />
            <Onboarding pathname={pathname} />
          </Tooltip.Provider>
        )}
      </MotionConfig>
    </LazyMotion>
  )
}

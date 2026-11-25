import * as m from 'motion/react-m'
import { useState, type FormEvent } from 'react'
import { ApiError, invalidate, send, useApi } from './api'
import { SproutBook } from './Illustrations'
import { pageIn } from './motion'
import { navigate } from './router'

const MIN = 8 // the backend's auth.MIN_PASSWORD

function Brand() {
  return (
    <div className="mb-8 flex items-center gap-3">
      <img src="/logo.png" alt="" className="size-10 rounded-md" />
      <h1 className="font-serif text-xl font-medium">ScholarMind</h1>
    </div>
  )
}

function LoginForm({ notice }: { notice: string }) {
  const [error, setError] = useState(notice)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = new FormData(e.currentTarget)
    setBusy(true)
    setError('')
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: form.get('username'), password: form.get('password') }),
      })
      if (res.ok) {
        // Only ever return to a page of this site.
        const next = new URL(new URLSearchParams(location.search).get('next') ?? '/', location.origin)
        navigate(next.origin === location.origin ? next.pathname + next.search : '/', true)
        return
      }
      setError(res.status === 401 ? '用户名或密码错误' : `登录失败（${res.status}）`)
    } catch {
      setError('无法连接服务器')
    }
    setBusy(false)
  }

  return (
    <m.form {...pageIn()} onSubmit={submit} className="mx-auto w-full max-w-form space-y-3 md:col-span-2">
      <Brand />
      <label className="field">
        <input name="username" placeholder="用户名" aria-label="用户名" autoComplete="username" required autoFocus />
      </label>
      <label className="field">
        <input name="password" type="password" placeholder="密码" aria-label="密码" autoComplete="current-password" required />
      </label>
      <button className="btn-clay w-full" disabled={busy}>
        {busy ? '登录中…' : '登录'}
      </button>
      <p className="h-5 text-sm text-clay-ink" role="alert">
        {error}
      </p>
    </m.form>
  )
}

/** A new deployment without any account: its first admin, made here and signed in at once (the
 * backend makes only one, however many ask at the same moment), then on to the setup wizard. */
function FirstAdmin({ onTaken }: { onTaken: () => void }) {
  const [state, setState] = useState<{ busy?: boolean; error?: string }>({})

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const data = new FormData(e.currentTarget)
    const [username, email] = ['username', 'email'].map((k) => String(data.get(k) ?? '').trim())
    const [password, again] = ['password', 'again'].map((k) => String(data.get(k) ?? ''))
    if (password.length < MIN) return setState({ error: `密码至少 ${MIN} 位` })
    if (password !== again) return setState({ error: '两次输入的密码不一致' })
    setState({ busy: true })
    try {
      await send('POST', '/setup/admin', { username, password, email: email || null })
      navigate('/setup', true)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) return onTaken() // someone was quicker
      setState({ error: err instanceof ApiError && err.status === 422 ? `用户名只能用字母、数字和 . _ -，邮箱要写对，密码至少 ${MIN} 位` : (err as Error).message })
    }
  }

  return (
    <m.form {...pageIn()} onSubmit={submit} className="mx-auto w-full max-w-form md:col-span-2" aria-labelledby="first-admin">
      <Brand />
      <h2 id="first-admin" className="font-serif text-lg font-semibold text-ink">
        创建管理员
      </h2>
      <p className="mt-1 mb-6 text-sm text-stone">这里还没有账号。先建一个管理员，接着连上模型、选好论文来源；成员账号之后在管理后台添加。</p>
      <div className="space-y-3">
        <label className="field">
          <input name="username" required autoFocus maxLength={64} pattern="[A-Za-z0-9._\-]+" title="字母、数字和 . _ -" autoComplete="username" spellCheck={false} placeholder="用户名" aria-label="用户名" />
        </label>
        <label className="field">
          <input name="password" type="password" required maxLength={1024} autoComplete="new-password" placeholder={`密码，至少 ${MIN} 位`} aria-label="密码" />
        </label>
        <label className="field">
          <input name="again" type="password" required maxLength={1024} autoComplete="new-password" placeholder="再输一次密码" aria-label="再输一次密码" />
        </label>
        <label className="field">
          <input name="email" type="email" maxLength={254} autoComplete="email" placeholder="邮箱（可不填）" aria-label="邮箱（可不填）" />
        </label>
        <button className="btn-clay w-full" disabled={state.busy}>
          {state.busy ? '创建中…' : '创建并登录'}
        </button>
        <p className="min-h-5 text-sm text-clay-ink" role="alert">
          {state.error}
        </p>
      </div>
    </m.form>
  )
}

export default function Login() {
  // A backend without the setup endpoint (an error) always has accounts.
  const status = useApi<{ needs_admin: boolean }>('/setup/status')
  const [notice, setNotice] = useState('')
  const first = status.data?.needs_admin && !notice

  // Asymmetric: the illustration carries the weight on the left, the form sits light on the right.
  return (
    <main className="frame grid min-h-dvh max-w-content content-center items-center gap-8 py-12 md:grid-cols-5 md:gap-16">
      <title>{first ? '创建管理员 · ScholarMind' : '登录 · ScholarMind'}</title>
      <SproutBook className="mx-auto w-64 md:col-span-3 md:w-full" />
      {!status.data && !status.error ? (
        <div className="md:col-span-2" /> // a moment, rather than the login form flashing by
      ) : first ? (
        <FirstAdmin
          onTaken={() => {
            invalidate('/setup/status')
            setNotice('已经有管理员了，请登录')
          }}
        />
      ) : (
        <LoginForm notice={notice} />
      )}
    </main>
  )
}

import * as m from 'motion/react-m'
import { useState, type FormEvent } from 'react'
import { SproutBook } from './Illustrations'
import { pageIn } from './motion'
import { navigate } from './router'

export default function Login() {
  const [error, setError] = useState('')
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

  // Asymmetric: the illustration carries the weight on the left, the form sits light on the right.
  return (
    <main className="frame grid min-h-dvh max-w-content content-center items-center gap-8 py-12 md:grid-cols-5 md:gap-16">
      <title>登录 · ScholarMind</title>
      <SproutBook className="mx-auto w-64 md:col-span-3 md:w-full" />
      <m.form {...pageIn()} onSubmit={submit} className="mx-auto w-full max-w-form space-y-3 md:col-span-2">
        <div className="mb-8 flex items-center gap-3">
          <img src="/logo.png" alt="" className="size-10 rounded-md" />
          <h1 className="font-serif text-xl font-medium">ScholarMind</h1>
        </div>
        <label className="field">
          <input name="username" placeholder="用户名" aria-label="用户名" autoComplete="username" required autoFocus />
        </label>
        <label className="field">
          <input
            name="password"
            type="password"
            placeholder="密码"
            aria-label="密码"
            autoComplete="current-password"
            required
          />
        </label>
        <button className="btn-clay w-full" disabled={busy}>
          {busy ? '登录中…' : '登录'}
        </button>
        <p className="h-5 text-sm text-clay-ink" role="alert">
          {error}
        </p>
      </m.form>
    </main>
  )
}

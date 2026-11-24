import { Check } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, send } from './api'

const MIN = 8 // the backend's auth.MIN_PASSWORD

/** Change the password, on the profile page or in the header menu's dialog. On success the backend ends every other
 * session and gives this browser a new cookie. */
export default function PasswordForm() {
  const [state, setState] = useState<{ busy?: boolean; error?: string; done?: boolean }>({})

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const data = new FormData(form)
    const [old_password, new_password, again] = ['old', 'new', 'again'].map((k) => String(data.get(k) ?? ''))
    if (new_password.length < MIN) return setState({ error: `新密码至少 ${MIN} 位` })
    if (new_password !== again) return setState({ error: '两次输入不一致' })
    setState({ busy: true })
    try {
      await send('POST', '/me/password', { old_password, new_password })
      form.reset()
      setState({ done: true })
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      setState({ error: status === 400 ? '当前密码不对' : status === 422 ? `新密码至少 ${MIN} 位` : (err as Error).message })
    }
  }

  return (
    <form onSubmit={submit} onInput={() => state.done && setState({})} className="max-w-form space-y-3" aria-label="修改密码">
      <label className="field">
        <input name="old" type="password" placeholder="当前密码" aria-label="当前密码" autoComplete="current-password" required />
      </label>
      <label className="field">
        <input name="new" type="password" placeholder="新密码" aria-label="新密码" autoComplete="new-password" required />
      </label>
      <label className="field">
        <input name="again" type="password" placeholder="再输一次" aria-label="再输一次新密码" autoComplete="new-password" required />
      </label>
      <div className="flex items-center gap-4">
        <button className="btn-clay" disabled={state.busy}>
          修改密码
        </button>
        <p role="alert" className="text-sm text-clay-ink">
          {state.done ? (
            <span className="flex items-center gap-1.5 text-ink-soft">
              <Check size={16} strokeWidth={2} className="text-sage" />
              已更新，其他设备需要重新登录
            </span>
          ) : (
            state.error
          )}
        </p>
      </div>
    </form>
  )
}

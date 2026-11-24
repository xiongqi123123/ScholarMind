import { BookOpenText } from 'lucide-react'
import { useState } from 'react'
import { ApiError, get, send, type Paper, type ReportStatus } from './api'
import { progressLine, reason, STEPS, useNow, watchReading } from './readings'

/** "Generate a reading", where it is while queued or being written (the caller polls; it is
 * also watched, so a card says when it is done should the reader leave), and a retry with the
 * reason after a failure. */
export default function ReportAction({ paper, onChange }: { paper: Paper; onChange: (paper: Paper) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const status = paper.report_status

  async function generate() {
    setBusy(true)
    setError('')
    try {
      // A reading the page cannot show (an older format) counts as none: redo it.
      const force = status === 'reported'
      const res = await send<{ report_status: ReportStatus }>('POST', `/papers/${paper.id}/report?force=${force}`)
      const next = { ...paper, report_status: res.report_status, report_progress: null, report_error: null }
      onChange(next)
      watchReading(next)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) onChange(await get<Paper>(`/papers/${paper.id}`))
      else setError((e as Error).message)
    }
    setBusy(false)
  }

  if (status === 'queued' || status === 'reporting') return <Progress paper={paper} />
  const failed = status === 'report_failed'
  return (
    <>
      <button type="button" className="btn-quiet h-8 px-3 text-sm" disabled={busy} onClick={generate}>
        <BookOpenText size={14} strokeWidth={1.75} />
        {failed || status === 'reported' ? '重新生成解读' : '生成解读'}
      </button>
      {(failed || error) && (
        <span role="alert" className="text-sm text-clay-ink">
          {error || (paper.report_error ? `上次生成失败：${reason(paper)}` : '上次生成失败')}
        </span>
      )}
    </>
  )
}

/** Queued: a pulsing dot. Running: its four steps as short bars (done, current, to come). */
function Progress({ paper }: { paper: Paper }) {
  const now = useNow(true)
  const at = STEPS.findIndex((s) => s.stage === paper.report_progress?.stage)
  return (
    <span role="status" className="inline-flex min-h-8 items-center gap-2.5 px-3 text-sm text-clay-ink">
      {at < 0 ? (
        <span className="size-2 shrink-0 animate-pulse rounded-full bg-clay" />
      ) : (
        <span className="flex shrink-0 gap-0.5" aria-hidden="true" title={STEPS.map((s) => s.name).join(' → ')}>
          {STEPS.map((s, i) => (
            <span key={s.stage} className={`h-1.5 w-3 rounded-full ${i < at ? 'bg-clay' : i === at ? 'animate-pulse bg-clay' : 'bg-line'}`} />
          ))}
        </span>
      )}
      {progressLine(paper, now)}
    </span>
  )
}

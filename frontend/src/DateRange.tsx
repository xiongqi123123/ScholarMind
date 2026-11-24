import { CalendarDays, X } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useState } from 'react'
import { DayPicker, type DateRange as Range } from 'react-day-picker'
import { zhCN } from 'react-day-picker/locale/zh-CN'
import { localDay as iso } from './api'

const parse = (s: string | null) => (s ? new Date(s + 'T00:00') : undefined)

function daysAgo(n: number) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return iso(d)
}

const PRESETS = [
  ['3 天', 2],
  ['7 天', 6],
  ['30 天', 29],
] as const

/** A date-range button; the change is applied when the calendar closes. */
export default function DateRange({
  from,
  to,
  onChange,
}: {
  from: string | null
  to: string | null
  onChange: (from: string | null, to: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [range, setRange] = useState<Range | undefined>()

  function openChange(next: boolean) {
    if (next) setRange({ from: parse(from), to: parse(to) })
    else if (range?.from) {
      const [f, t] = [iso(range.from), iso(range.to ?? range.from)]
      if (f !== from || t !== to) onChange(f, t)
    }
    setOpen(next)
  }

  function pick(f: string | null, t: string | null) {
    setOpen(false)
    onChange(f, t)
  }

  const label = from || to ? (from === to ? from : `${from ?? '…'} – ${to ?? '…'}`) : '日期'
  return (
    <Popover.Root open={open} onOpenChange={openChange}>
      <Popover.Trigger className={`field tabular-nums ${from || to ? 'text-ink' : 'text-stone'}`} aria-label="日期范围">
        <CalendarDays size={16} strokeWidth={1.75} className="shrink-0 text-stone" />
        <span className="whitespace-nowrap">{label}</span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} collisionPadding={16} className="popover">
          <DayPicker
            mode="range"
            locale={zhCN}
            weekStartsOn={1}
            selected={range}
            onSelect={setRange}
            defaultMonth={range?.from ?? new Date()}
            disabled={{ after: new Date() }}
          />
          <div className="mt-2 flex items-center gap-1 border-t border-line pt-3 text-sm">
            {PRESETS.map(([name, n]) => (
              <button key={name} type="button" className="chip h-7 px-3 text-sm" onClick={() => pick(daysAgo(n), null)}>
                {name}
              </button>
            ))}
            {(from || to) && (
              <button type="button" className="icon-btn ml-auto" aria-label="清除日期" onClick={() => pick(null, null)}>
                <X size={16} strokeWidth={1.75} />
              </button>
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

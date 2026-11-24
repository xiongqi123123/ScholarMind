import * as m from 'motion/react-m'
import type { ReactNode } from 'react'
import { draw, fade, grow } from './motion'

// Original illustrations. Shapes are drawn loosely by hand; the filter below adds a slight
// wobble to every edge and a print-like grain inside the fills. Colours are token classes.

function Hand({ id }: { id: string }) {
  return (
    <defs>
      <filter id={id} x="-5%" y="-5%" width="110%" height="110%">
        <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="7" result="wobble" />
        <feDisplacementMap in="SourceGraphic" in2="wobble" scale="3" result="drawn" />
        <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="2" result="noise" />
        <feColorMatrix in="noise" values="0 0 0 0 .16  0 0 0 0 .13  0 0 0 0 .1  0 0 0 .5 -.12" result="specks" />
        <feComposite in="specks" in2="drawn" operator="in" result="grain" />
        <feMerge>
          <feMergeNode in="drawn" />
          <feMergeNode in="grain" />
        </feMerge>
      </filter>
    </defs>
  )
}

const GROUND = 'M14 134C70 129 150 136 226 131'

/** Login: a sprout growing out of an open book under a warm sun — reading turns into ideas. */
export function SproutBook({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 320 280" className={className} aria-hidden="true">
      <Hand id="art-sprout" />
      <g filter="url(#art-sprout)" fill="none" strokeLinecap="round" strokeLinejoin="round">
        <m.path
          {...grow(0)}
          className="fill-clay"
          d="M198 38C235 40 261 70 259 107C257 142 228 168 193 166C158 164 132 137 134 102C136 66 163 36 198 38Z"
        />
        <path className="fill-paper-raised stroke-ink" strokeWidth="2" d="M160 196C132 180 88 174 46 182L40 232C84 226 128 230 160 244Z" />
        <path className="fill-paper-raised stroke-ink" strokeWidth="2" d="M160 196C188 180 232 174 274 182L280 232C236 226 192 230 160 244Z" />
        <path className="stroke-ink" strokeWidth="3.5" d="M34 239C88 231 130 237 160 251C190 237 232 231 286 239" />
        <g className="stroke-stone" strokeWidth="1.5">
          <path d="M62 197C88 192 113 195 138 203" />
          <path d="M60 209C86 205 111 207 136 215" />
          <path d="M59 221C80 218 100 219 118 223" />
          <path d="M182 203C207 195 232 192 258 197" />
          <path d="M184 215C209 207 234 205 260 209" />
          <path d="M202 223C222 219 242 218 261 221" />
        </g>
        <m.path {...draw(1)} className="stroke-ink" strokeWidth="2" d="M160 196C157 174 165 152 160 128" />
        <m.path
          {...grow(4)}
          className="fill-sage stroke-ink"
          strokeWidth="1.5"
          d="M160 150C147 136 129 135 120 143C130 156 148 158 160 150Z"
        />
        <m.path
          {...grow(5)}
          className="fill-sage stroke-ink"
          strokeWidth="1.5"
          d="M161 134C171 116 191 111 202 117C195 132 177 139 161 134Z"
        />
        <m.g {...fade(6)} className="fill-ink">
          <circle cx="104" cy="98" r="2.4" />
          <circle cx="86" cy="132" r="1.8" />
          <circle cx="282" cy="74" r="2" />
        </m.g>
      </g>
    </svg>
  )
}

/** Nothing found: a ball of yarn whose loose thread runs out — no lead to follow. */
export function Yarn({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 160" className={className} aria-hidden="true">
      <Hand id="art-yarn" />
      <g filter="url(#art-yarn)" fill="none" strokeLinecap="round" strokeLinejoin="round">
        <path className="stroke-line" strokeWidth="1.5" d={GROUND} />
        <m.path
          {...grow(0)}
          className="fill-clay"
          d="M78 63C98 63 113 79 112 99C111 118 96 132 77 131C58 130 44 116 45 97C46 78 60 63 78 63Z"
        />
        <m.g {...fade(1)} className="stroke-clay-ink" strokeWidth="1.5" opacity="0.75">
          <path d="M52 82C70 90 92 88 108 79" />
          <path d="M47 101C68 112 96 110 111 97" />
          <path d="M58 124C66 104 82 81 104 70" />
        </m.g>
        <m.path
          {...draw(1)}
          className="stroke-ink"
          strokeWidth="1.75"
          d="M110 111C128 126 140 103 156 113C170 122 160 136 176 134C190 132 196 119 211 125C217 128 218 134 213 135"
        />
      </g>
    </svg>
  )
}

/** Something failed: a paper plane that came down short — the message did not get through. */
export function PaperPlane({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 160" className={className} aria-hidden="true">
      <Hand id="art-plane" />
      <g filter="url(#art-plane)" fill="none" strokeLinecap="round" strokeLinejoin="round">
        <path className="stroke-line" strokeWidth="1.5" d={GROUND} />
        <m.path
          {...fade(0)}
          className="stroke-stone"
          strokeWidth="1.5"
          strokeDasharray="4 7"
          d="M16 44C58 20 98 32 101 56C104 77 75 81 77 62C79 43 121 40 140 70C151 88 155 106 157 122"
        />
        <m.g {...grow(2)}>
          <path className="fill-paper-raised stroke-ink" strokeWidth="1.75" d="M158 130L210 82L192 108Z" />
          <path className="fill-line stroke-ink" strokeWidth="1.75" d="M158 130L192 108L206 118Z" />
        </m.g>
        <m.g {...fade(4)} className="stroke-ink" strokeWidth="1.5">
          <path d="M146 124L139 120" />
          <path d="M150 117L146 110" />
        </m.g>
        <g className="stroke-sage" strokeWidth="1.75">
          <path d="M118 133C119 125 122 121 125 119" />
          <path d="M123 133C125 127 129 124 134 123" />
          <path d="M114 133C112 127 109 124 105 123" />
        </g>
      </g>
    </svg>
  )
}

/** A small drawing for a row or a rail: the same hand, at 120 units square. */
function Spot({ id, className, children }: { id: string; className?: string; children: ReactNode }) {
  return (
    <svg viewBox="0 0 120 120" className={className} aria-hidden="true">
      <Hand id={id} />
      <g filter={`url(#${id})`} fill="none" strokeLinecap="round" strokeLinejoin="round">
        {children}
      </g>
    </svg>
  )
}

/** Admin console, the agent: a spark of thought, and a smaller one, before a warm sun. */
export function AgentSpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-agent" className={className}>
      <path className="fill-clay" d="M76 16C94 17 107 32 106 50C105 68 90 80 73 79C55 78 43 64 44 46C45 29 59 15 76 16Z" />
      <path className="fill-paper-raised stroke-ink" strokeWidth="3" d="M46 34C49 54 54 60 74 64C54 68 49 75 46 98C43 75 38 68 18 64C38 60 43 54 46 34Z" />
      <path className="fill-ink stroke-ink" strokeWidth="2" d="M89 80C90 87 92 89 99 91C92 93 90 95 89 103C88 95 86 93 79 91C86 89 88 87 89 80Z" />
      <circle cx="22" cy="30" r="2.6" className="fill-ink" />
      <circle cx="104" cy="104" r="2" className="fill-ink" />
    </Spot>
  )
}

/** Admin console, the accounts: two people side by side before a sage sun. */
export function PeopleSpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-people" className={className}>
      <path className="fill-sage" d="M46 14C65 13 80 27 80 46C80 64 66 78 47 78C29 78 15 65 15 47C15 29 28 15 46 14Z" />
      <path className="fill-paper-raised stroke-ink" strokeWidth="3" d="M60 98C61 79 70 70 82 70C94 70 103 79 104 98" />
      <circle cx="82" cy="52" r="12" className="fill-paper-raised stroke-ink" strokeWidth="3" />
      <path className="fill-paper-raised stroke-ink" strokeWidth="3" d="M22 104C23 83 35 75 50 75C65 75 77 83 78 104" />
      <circle cx="50" cy="56" r="14" className="fill-paper-raised stroke-ink" strokeWidth="3" />
      <path className="stroke-ink" strokeWidth="3" d="M12 105C44 101 78 107 110 103" />
    </Spot>
  )
}

/** A key (API keys, personal tokens): a ring and a toothed shaft across a warm sun. */
export function KeySpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-key" className={className}>
      <path className="fill-clay" d="M70 14C90 14 106 30 105 50C104 69 88 82 69 81C50 80 37 65 38 46C39 28 52 14 70 14Z" />
      <path className="stroke-ink" strokeWidth="4" d="M53 66C66 54 80 42 96 28" />
      <path className="stroke-ink" strokeWidth="3.5" d="M80 42L89 51M88 35L96 43" />
      <circle cx="40" cy="78" r="17" className="fill-paper-raised stroke-ink" strokeWidth="3" />
      <circle cx="36" cy="82" r="5" className="stroke-ink" strokeWidth="2.5" />
    </Spot>
  )
}

/** Where requests go (the Base URL): a globe, its meridian and equator, before a sage sun. */
export function GlobeSpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-globe" className={className}>
      <path className="fill-sage" d="M80 12C97 13 109 27 108 44C107 60 94 72 78 71C61 70 49 57 50 40C51 24 64 11 80 12Z" />
      <circle cx="54" cy="66" r="30" className="fill-paper-raised stroke-ink" strokeWidth="3" />
      <path className="stroke-ink" strokeWidth="2.5" d="M54 36C40 50 40 82 54 96C68 82 68 50 54 36Z" />
      <path className="stroke-ink" strokeWidth="2.5" d="M25 62C44 57 64 57 83 62" />
      <path className="stroke-stone" strokeWidth="2" d="M30 80C46 76 62 76 78 80" />
    </Spot>
  )
}

/** Tagging (the brief prompt and its model): a tag on a string before a warm sun. */
export function TagSpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-tag" className={className}>
      <path className="fill-clay" d="M42 12C60 11 74 25 74 43C74 60 60 73 43 73C26 73 13 60 13 43C13 26 26 13 42 12Z" />
      <path className="fill-paper-raised stroke-ink" strokeWidth="3" d="M28 64L62 30L96 28L98 62L64 96Z" />
      <circle cx="84" cy="42" r="5.5" className="stroke-ink" strokeWidth="2.5" />
      <path className="stroke-ink" strokeWidth="2" d="M84 42C90 24 102 18 110 24" />
      <path className="stroke-stone" strokeWidth="2" d="M52 66L70 48M60 74L74 60" />
    </Spot>
  )
}

/** Reading (the report prompt and its model): an open book before a sage sun. */
export function BookSpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-book" className={className}>
      <path className="fill-sage" d="M60 10C76 10 88 22 88 38C88 53 76 64 60 64C44 64 32 53 32 38C32 22 44 10 60 10Z" />
      <path className="fill-paper-raised stroke-ink" strokeWidth="2.5" d="M60 52C48 44 32 42 18 46L16 86C30 82 46 84 60 92Z" />
      <path className="fill-paper-raised stroke-ink" strokeWidth="2.5" d="M60 52C72 44 88 42 102 46L104 86C90 82 74 84 60 92Z" />
      <path className="stroke-ink" strokeWidth="3.5" d="M12 90C32 86 48 90 60 97C72 90 88 86 108 90" />
      <path className="stroke-stone" strokeWidth="2" d="M26 58C36 56 44 57 52 61M26 68C36 66 44 67 52 71M68 61C76 57 84 56 94 58M68 71C76 67 84 66 94 68" />
    </Spot>
  )
}

/** Judging what is new (the assess prompt): a lit bulb under a warm sun. */
export function BulbSpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-bulb" className={className}>
      <path className="fill-clay" d="M74 10C91 10 104 24 103 41C102 57 89 69 73 68C57 67 45 54 46 38C47 22 59 10 74 10Z" />
      <path className="fill-paper-raised stroke-ink" strokeWidth="3" d="M58 24C74 24 86 36 86 52C86 64 78 70 74 78L74 86L44 86L44 78C40 70 30 64 30 52C30 36 42 24 58 24Z" />
      <path className="stroke-ink" strokeWidth="2.5" d="M51 74C49 62 53 56 58 56C63 56 67 62 65 74" />
      <path className="stroke-ink" strokeWidth="3" d="M46 94L72 94M50 102L68 102" />
      <path className="stroke-ink" strokeWidth="2.5" d="M18 30L24 35M98 84L104 88M12 56L19 56" />
    </Spot>
  )
}

/** Merging duplicate topics (the topic-review prompt): two paths that become one. */
export function MergeSpot({ className }: { className?: string }) {
  return (
    <Spot id="spot-merge" className={className}>
      <path className="fill-sage" d="M60 30C78 30 92 44 92 62C92 79 78 92 61 92C43 92 30 79 30 62C30 44 43 30 60 30Z" />
      <path className="stroke-ink" strokeWidth="3.5" d="M26 20C26 50 60 54 60 80M94 20C94 50 60 54 60 80M60 80L60 100" />
      <circle cx="26" cy="18" r="7" className="fill-paper-raised stroke-ink" strokeWidth="2.5" />
      <circle cx="94" cy="18" r="7" className="fill-paper-raised stroke-ink" strokeWidth="2.5" />
      <circle cx="60" cy="104" r="8" className="fill-clay stroke-ink" strokeWidth="2.5" />
    </Spot>
  )
}

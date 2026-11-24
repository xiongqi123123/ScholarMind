// Motion presets. Durations and easing come from the tokens in index.css, read on first use,
// so they are tuned in one place. <MotionConfig reducedMotion="user"> in App honours the
// system's reduce-motion setting.

let tokens: { fast: number; base: number; slow: number; stagger: number; ease: [number, number, number, number] } | undefined

function t() {
  if (!tokens) {
    const css = getComputedStyle(document.documentElement)
    const seconds = (name: string) => parseFloat(css.getPropertyValue(name)) / 1000
    tokens = {
      fast: seconds('--duration-fast'),
      base: seconds('--duration-base'),
      slow: seconds('--duration-slow'),
      stagger: seconds('--stagger'),
      ease: (css.getPropertyValue('--ease-out').match(/[\d.]+/g) ?? [0, 0, 1, 1]).map(Number) as [number, number, number, number],
    }
  }
  return tokens
}

/** A page settling in. */
export const pageIn = () => ({
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: t().slow, ease: t().ease },
})

/** The nth row of a list, staggered (capped so long lists do not drag). */
export const rowIn = (n: number) => ({
  initial: { opacity: 0, y: 4 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: t().base, ease: t().ease, delay: Math.min(n, 12) * t().stagger },
})

/** A stroke drawing itself, `delay` in steps of the base duration. */
export const draw = (delay = 0) => ({
  initial: { pathLength: 0, opacity: 0 },
  animate: { pathLength: 1, opacity: 1 },
  transition: { duration: t().slow * 2, ease: t().ease, delay: delay * t().base },
})

/** A plain fade, for dashed strokes and groups. */
export const fade = (delay = 0) => ({
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  transition: { duration: t().slow, ease: t().ease, delay: delay * t().base },
})

/** A shape fading and growing in. */
export const grow = (delay = 0) => ({
  initial: { opacity: 0, scale: 0.6 },
  animate: { opacity: 1, scale: 1 },
  transition: { duration: t().slow, ease: t().ease, delay: delay * t().base },
})

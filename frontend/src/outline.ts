// The reading's Markdown, prepared for rendering, and its sections. Kept out of the Report chunk:
// the paper page lists the sections without loading the renderer.

/** \(…\) and \[…\] as $…$ and $$…$$ for remark-math; a line that is only $$…$$ as a display
 * block (remark-math reads it as inline, which cannot scroll and overflows narrow screens);
 * figure markers as paragraphs of their own. A title line the model sometimes opens with is
 * dropped: the reader's bar has the paper's title, and most readings start at their first section. */
export function prepare(markdown: string) {
  return markdown
    .replace(/^\s*#\s[^\n]*\n+/, '')
    .replace(/\\\((.+?)\\\)/g, (_, tex: string) => '$' + tex + '$')
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, tex: string) => '$$' + tex + '$$')
    .replace(/^[ \t]*\$\$(.+?)\$\$[ \t]*$/gm, (_, tex: string) => '\n$$\n' + tex.trim() + '\n$$\n')
    .replace(/^[ \t]*(\[\[(?:图|表)\s*[:：].+?\]\])[ \t]*$/gm, (_, marker: string) => `\n${marker}\n`)
}

export interface Heading {
  id: string // from the line number in the prepared Markdown, which the rendered <h2> knows too
  text: string
}

export function headings(prepared: string): Heading[] {
  return prepared.split('\n').flatMap((line, i) => {
    const h = line.match(/^##\s+(.+?)\s*#*$/)
    return h ? [{ id: `s${i + 1}`, text: h[1].replace(/[*`$]/g, '') }] : []
  })
}

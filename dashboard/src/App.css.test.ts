/// <reference types="node" />

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const appCss = readFileSync(resolve(process.cwd(), 'src/App.css'), 'utf8')

describe('mobile layout guards', () => {
  it('keeps controls and wide evidence tables contained at 320px', () => {
    expect(appCss).toContain('@media (max-width: 360px)')
    expect(appCss).toContain('max-width: calc(100vw - 32px)')
    expect(appCss).toMatch(/\.family-tabs button\s*\{[^}]*min-width:\s*0/s)
    expect(appCss).toMatch(/\.summary-table-wrap\s*\{[^}]*overflow-x:\s*auto/s)
    expect(appCss).toMatch(/\.comparison-table-wrap\s*\{[^}]*overflow-x:\s*auto/s)
  })
})

import { cpSync, rmSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = resolve(here, '../../python')
const dest = resolve(here, '../python')

if (!existsSync(src)) {
  console.error(`[copy-python] source engine not found at ${src}`)
  process.exit(1)
}

const SKIP = new Set([
  '.venv', '__pycache__', 'dist', 'build', '.pytest_cache', '.ruff_cache', 'tests',
  'coverage', 'htmlcov', '.nyc_output', 'coverage.xml', 'lcov.info',

  'latestDebugRun',
])

rmSync(dest, { recursive: true, force: true })
cpSync(src, dest, {
  recursive: true,
  filter: (p) =>
    !p
      .split(/[\\/]/)
      .some((seg) => SKIP.has(seg) || seg.endsWith('.egg-info') || seg.startsWith('.coverage')),
})
console.log('[copy-python] bundled python/ engine -> js/python/')

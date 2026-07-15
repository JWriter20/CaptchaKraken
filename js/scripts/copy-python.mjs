// Bundle the sibling `../python` (the `captchakraken` package) into `./python`
// so the published npm package is self-contained: the JS solver shells out to
// this bundled engine for OpenCV grid detection and the vLLM planner.
//
// npm can only pack files inside the package root, so the single source of truth
// at repo-root `python/` is copied in here at build time. Runtime source (src/)
// + pyproject are what the postinstall `pip install .` needs; tests, caches,
// venvs and build artifacts are skipped.
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

const SKIP = new Set(['.venv', '__pycache__', 'dist', 'build', '.pytest_cache', '.ruff_cache', 'tests'])

rmSync(dest, { recursive: true, force: true })
cpSync(src, dest, {
  recursive: true,
  filter: (p) => !p.split(/[\\/]/).some((seg) => SKIP.has(seg) || seg.endsWith('.egg-info')),
})
console.log('[copy-python] bundled python/ engine -> js/python/')

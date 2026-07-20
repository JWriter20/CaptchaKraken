/**
 * postinstall bootstrap: create a local venv under `python/.venv` and install the
 * bundled `captchakraken` package so `python -m captchakraken.cli` works out-of-the-box.
 *
 * Opt out:
 *   CAPTCHA_KRAKEN_SKIP_PYTHON_SETUP=1
 *
 * Force a specific system python:
 *   CAPTCHA_KRAKEN_PYTHON=/path/to/python3
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (res.error) throw res.error;
  if (res.status !== 0) {
    throw new Error(`Command failed (${res.status}): ${cmd} ${args.join(' ')}`);
  }
}

function exists(p) {
  try {
    fs.accessSync(p);
    return true;
  } catch {
    return false;
  }
}

function venvPython(venvDir) {
  const candidates = [
    path.join(venvDir, 'bin', 'python'),
    path.join(venvDir, 'bin', 'python3'),
    path.join(venvDir, 'Scripts', 'python.exe'),
    path.join(venvDir, 'Scripts', 'python'),
  ];
  for (const c of candidates) if (exists(c)) return c;
  return null;
}

function resolveSystemPython() {
  if (process.env.CAPTCHA_KRAKEN_PYTHON) return process.env.CAPTCHA_KRAKEN_PYTHON;
  // Try python3 first, then python.
  return process.platform === 'win32' ? 'python' : 'python3';
}

function main() {
  if (process.env.CAPTCHA_KRAKEN_SKIP_PYTHON_SETUP === '1') {
    console.log('[CaptchaKraken] Skipping python setup (CAPTCHA_KRAKEN_SKIP_PYTHON_SETUP=1).');
    return;
  }

  const pkgRoot = path.resolve(__dirname, '..');
  const cliRoot = path.join(pkgRoot, 'python');
  const venvDir = path.join(cliRoot, '.venv');

  if (!exists(path.join(cliRoot, 'pyproject.toml'))) {
    console.log(`[CaptchaKraken] No bundled python engine found at ${cliRoot}; skipping python setup.`);
    return;
  }

  let py = venvPython(venvDir);
  if (py) {
    console.log(`[CaptchaKraken] Found existing venv python at ${py}; ensuring package is installed...`);
  } else {
    const sysPy = resolveSystemPython();
    console.log(`[CaptchaKraken] Creating venv at ${venvDir} using ${sysPy}...`);
    run(sysPy, ['-m', 'venv', venvDir], { cwd: cliRoot });
    py = venvPython(venvDir);
    if (!py) {
      throw new Error('[CaptchaKraken] Failed to locate venv python after venv creation.');
    }
    console.log('[CaptchaKraken] Upgrading pip/setuptools/wheel...');
    run(py, ['-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel'], { cwd: cliRoot });
  }

  // Install the `captchakraken` package with its CORE (lightweight) deps only:
  // OpenCV grid detection + the HTTP planner that talks to a vLLM server. The
  // heavy serving stack (vllm/torch, the `[serve]` extra) is NOT installed here
  // — that's what setup.sh installs for people self-hosting the model.
  //
  // Prefer the PUBLISHED package from PyPI, pinned to THIS npm package's version
  // so the JS driver and the Python engine stay in lockstep. Only fall back to
  // the bundled source tree when PyPI can't satisfy that pin — offline installs,
  // or a dev/CI/from-git build whose version isn't on PyPI yet.
  const pkgVersion = require(path.join(pkgRoot, 'package.json')).version;
  try {
    console.log(`[CaptchaKraken] Installing captchakraken==${pkgVersion} from PyPI...`);
    run(py, ['-m', 'pip', 'install', `captchakraken==${pkgVersion}`]);
  } catch (err) {
    console.warn(
      `[CaptchaKraken] PyPI install failed (${err && err.message ? err.message : err}); ` +
      'falling back to the bundled source tree.',
    );
    run(py, ['-m', 'pip', 'install', '.'], { cwd: cliRoot });
  }

  console.log('[CaptchaKraken] Python environment ready.');
}

try {
  main();
} catch (err) {
  const strict = process.env.CAPTCHA_KRAKEN_PYTHON_SETUP_STRICT === '1';
  console.warn('[CaptchaKraken] Python setup failed during postinstall.');
  console.warn('Reason:', err && err.message ? err.message : String(err));
  console.warn(
    'You can re-run setup later, or skip it entirely with CAPTCHA_KRAKEN_SKIP_PYTHON_SETUP=1.\n' +
    '- Ensure you have Python 3.10+ installed\n' +
    '- Optionally set CAPTCHA_KRAKEN_PYTHON=/path/to/python3\n' +
    '- If you want the install to fail on setup errors, set CAPTCHA_KRAKEN_PYTHON_SETUP_STRICT=1'
  );
  if (strict) process.exit(1);
}



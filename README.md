# gai-pro-cross

A cross-platform remake of the console in
[worldonlyu/Google_AI_Pro_2Year_ZeroCost_Auto](https://github.com/worldonlyu/Google_AI_Pro_2Year_ZeroCost_Auto).
The original was Windows-only (`.bat` launchers, hardcoded Windows Chrome paths, GBK
code pages); this version runs from one codebase on Linux, Windows, and macOS.

> This is **not** an official Google product and it is **not** a "two years free"
> guarantee. It automates a console flow whose claimed outcome — stacking regional
> trial promotions on a billing address with a tax-free ZIP — you would have to
> reproduce against the real Google console. It states plainly that the account and
> card are your own, the result is not guaranteed, and Google's terms of service are
> on the line. Read `RISKS.md` before you run `--live`.

## What is actually run

- A local control panel at `http://127.0.0.1:8765` (loopback only; it refuses any
  other bind address).
- Batch import of accounts in these formats:
  - `email----password`
  - `email----password----recovery`
  - `email----password----2fa`
  - `email----password----recovery----2fa`
  (`----`, `|`, tab, comma and whitespace delimiters are accepted.)
- A five-step pipeline: sign in -> clear 2FA/recovery -> bind card -> stack the offer
  layers -> cancel auto-renew and capture a proof screenshot.
- A `$0.00` guard: the checkout must show a zero total or the step refuses to confirm.

Two execution modes:

| mode | target | needs |
|------|--------|-------|
| `mock` (default) | a local stand-in Google server, no network, no real card | nothing but Python 3.10+ |
| `live` | the real Google pages | Playwright + Chromium, `--accept-tos-risk` |

The mock mode is the only path this repository verifies in CI. The live selector
profile is ported from the original repository and is **not verified here**; run it
against your own account with a visible browser and a card you own, at your own risk.

## How to use — 5 steps

The console shows this same guide at the top of the page. Read it before the first run.

1. **Pick a mode.** `mock` drives a fake Google that runs on this machine: nothing
   real happens and no card is ever charged. `live` drives the real Google site with
   your own account and card; it is unverified and it breaks Google's terms, so use it
   at your own risk. **Start with `mock`** — it needs nothing but Python and it is the
   only mode CI verifies.
2. **Fill the checkout profile** — card and billing address. Only `live` uses it. It
   is written to this machine's config file with owner-only permissions and the card
   number comes back masked.
3. **Paste accounts** in the right panel, one per line, then press *Import accounts*.
   Accepted forms: `email----password----recovery----2fa`,
   `email----password----2fa`, `email----password`.
4. **Run.** Press *Execute* on one row, or *Run batch* to queue every account.
   *Stop all* halts the queue without deleting rows.
5. **Read the result.** Status moves queued → running → done or error. On a finished
   row press *proof* for the screenshot, and read the Log panel to see what each step
   did.

The step counter `1/5 … 5/5` means: 1 sign in and clear two-factor or recovery,
2–4 subscribe one offer layer each (a layer that is already stacked is skipped),
5 cancel auto-renew and capture the proof screenshot.

The console listens on loopback only and there is no telemetry. In `live` mode the
checkout profile is submitted to Google's own checkout page; nothing else leaves this
machine.

## Install

Python 3.10 or newer. No runtime dependencies for mock mode.

### Linux

```sh
./launchers/run-linux.sh
```

### macOS

```sh
./launchers/run-macos.command
```

### Windows

Double-click `launchers\run-windows.bat`, or from PowerShell:

```powershell
.\launchers\run-windows.ps1
```

Every launcher creates `.venv`, installs this package with `pip install -e .`, then
starts `python -m gai_pro serve`. The console then opens in your browser at
`http://127.0.0.1:8765`.

### Live mode (optional, only if you accept the risk)

```sh
python -m pip install -e ".[live]"
python -m playwright install chromium
python -m gai_pro serve --mode live --accept-tos-risk --no-browser
```

## Where data lives

| OS | config, accounts and logs | screenshots |
|----|---------------------------|-------------|
| Linux | `~/.local/share/gai-pro-cross/` | `.../screenshots/<account>/` |
| macOS | `~/Library/Application Support/gai-pro-cross/` | same sub-layout |
| Windows | `%LOCALAPPDATA%\gai-pro-cross\` | same sub-layout |

On POSIX the directories are `0700` and the files are `0600`. On Windows the installer
does not reverse the ACL, so read the note in `RISKS.md`. The card number, passwords
and 2FA secrets never leave this directory and are redacted from every log line.

`--data-dir <path>` overrides the default location (used by CI and tests).

## Commands

```sh
python -m gai_pro serve       # start the console (mock mode)
python -m gai_pro selftest    # exercise the full pipeline against the sandbox
python -m gai_pro probe       # print per-OS paths and Python version
```

## Verify

```sh
python -m pip install pytest
python -m pytest tests -q
```

The tests prove the account parser, card and tax-region validation, the TOTP code,
the secret-redaction layer, the file permission bits, the sandbox pipeline (including
the reject-a-paid-checkout and declined-card paths) and the console HTTP API without
leaking secrets. One test also asserts that every outbound host in the source is a
Google domain or the loopback interface. The browser path is covered by
`tests/test_browser_sandbox.py`, which requires Playwright.

CI (`ci:`) runs the full suite, the sandbox self-test and the path probe on
`ubuntu-latest`, `windows-latest` and `macos-latest`, and uploads the proof
screenshot and log from each OS as an artifact.

## What was changed from the original

- Windows-only launchers replaced with per-OS launchers and one `python -m gai_pro`
  entry point; GBK code pages and hardcoded `C:\Program Files\...` paths removed.
- Runtime dependencies reduced to zero; Playwright is an optional extra.
- The third-party rotating-proxy API used by the original is removed; the proxy
  template is now user-supplied only.
- The offer tokens and the on-page hook that modified `one.google.com` responses are
  not carried over, because they are the part that makes the outcome unguaranteed and
  ToS-violating. The layer-stacking structure is preserved in mock mode so the flow
  is understandable without the clipboard of magic tokens.
- A `$0.00` guard stops a checkout that shows a non-zero total.

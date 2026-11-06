# Risks and the honest picture

## This is not a "two years free" offer

The original repository markets "Google AI Pro (5 TB) zero cost two years
(US$0.00 -> 24 months)". That is **not an official Google offer**. The technique is:

1. Sign into a Google account you control.
2. Attach a Visa or Mastercard with an Oregon (97201) billing ZIP, a no-sales-tax
   state is chosen to suppress taxes on a paid renewal.
3. Stack several regional trial promotions so the free periods line up to about 24
   months.
4. Cancel auto-renew so no paid renewal follows the trial.

Each piece is a trial or promotion Google may change or withdraw at any time. Nothing
is guaranteed to stack, the total may not be zero, and the account may end up with a
paid renewal instead of 24 free months.

## Terms of service

Automating this flow against the real Google console is a violation of the Google
terms of service and the Google Play policies. Consequences can include declined
payments, promotion clawbacks, the loss of the promotion, or the account being
suspended. The account and the card are the operator's own.

Run `--live` only if you have read this file and accept that outcome. The program
does not start live mode without the explicit `--accept-tos-risk` flag.

## Card risks

- Mock mode never touches a real card; it accepts only test numbers like
  `4242 4242 4242 4242` and the test suite uses those.
- Live mode asks for a real card number and CVC. Treat that as handing the number to
  a program that fills it into a checkout form you cannot fully inspect. Use a
  card you can afford to have charged if the cancel step fails.
- The `$0.00` guard only confirms what the checkout surfaces on screen. A later
  price change or a missed cancellation is outside the program's control.

## Account and 2FA risks

- If you import a Google account with its password, you are storing a working
  credential in a plaintext JSON file (readable only by your user on POSIX). An
  attacker that can read your home directory can read it. Prefer a throwaway
  account, never a primary one. The password is only used by the local sandbox; the
  live path never sends it to Google and needs a session from *Login manual* instead.
- A stored session is itself a credential: `profiles/<account>/` and
  `sessions/<account>/storage_state.json` hold cookies that can sign in as the
  account. They are `0700`/`0600` on POSIX, and deleting the row in the console
  removes them.
- Recovery and 2FA are only as strong as the program's ability to read the challenge;
  the original could not unlock a hardware-key-only account.

## Privacy and where the data goes

- Credentials and card data stay in the local data directory and are redacted from
  logs and the control panel. The source has a test that rejects any outbound host
  that is not a Google domain or the loopback interface.
- There is no telemetry and no third-party server. The proxy feature only talks to a
  proxy you configure; it does not call any proxy provider's API.

## Windows ACL note

The POSIX launchers create owner-only files (`0600`) and directories (`0700`). On
Windows the code runs `icacls` to restrict the data directory to the current user,
but that only applies when the console is the one creating the directory. If you
point `--data-dir` at a network share or an existing directory with broad access,
restrict it yourself.

## Verification scope

The verified path is mock mode on the CI matrix (Linux, Windows, macOS): install,
the sandbox pipeline, the browser layer against the sandbox, the stored-session
contract (a fake session is created and reused, a missing session fails fast, the
live gateway never reads the password) and the file-permission checks. The live
Google path, including the real manual sign-in, is ported from the original selector
profile and cannot be verified in this repository.

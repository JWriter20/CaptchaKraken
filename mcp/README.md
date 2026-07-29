# captchakraken-mcp

The CaptchaKraken account, driven from an MCP client. It signs you in through
GitHub, mints and revokes API keys for the solving endpoint, and reads back what
you have spent.

**It does not solve captchas.** The key it mints is what does that, against the
OpenAI-compatible endpoint at `api.captchakraken.com/v1`.

```bash
claude mcp add captchakraken -- npx -y captchakraken-mcp
```

## Tools

| Tool | What it does |
| --- | --- |
| `sign_in` | Prints a code and a link; the human approves in a browser. Creates the account if it does not exist, with trial credits and a first key. |
| `sign_out` | Revokes this client's token on the server and deletes it from disk. |
| `get_account` | Who is signed in, the balance, and the base URL to point a solver at. |
| `get_balance` | The balance, and nothing else. |
| `get_usage` | Billable responses and credits, per day and in total, plus purchases. |
| `list_api_keys` | The account's solving keys, masked. |
| `create_api_key` | Mints one. **The secret is returned once.** |
| `revoke_api_key` | Kills one by id. Effective within ~30 seconds. |
| `get_topup_link` | A Stripe URL for the human to open. Charges nothing. |
| `get_pricing` | The rate card, and how many responses a captcha typically takes. |
| `get_models` | The lineup, so an agent can decide whether to self-host instead. |

## Signing in

The MCP server runs on a laptop, inside an editor, with no browser it controls
and nowhere for an OAuth redirect to land. It also cannot hold our GitHub client
secret — it is distributed to customers, so anything baked into it is public.

So it uses the device flow (RFC 8628):

```
sign_in  ──▶  "Open http://…/device?code=ABCD-EFGH, code ABCD-EFGH"
                    │
                    ├─ the human opens it, signs in with GitHub, approves
                    │
sign_in  ──▶  "Signed in as <login>. Balance: $0.50"
```

`sign_in` waits about 25 seconds and then hands control back rather than
blocking — MCP clients time tool calls out, and a call killed mid-wait would
strand the code. **Calling it again resumes the same request**; it does not
print a second code at a human who is already looking at the first.

## Two credentials, two blast radii

The token this server holds (`ckm_…`) manages the account: it reads the balance
and usage, mints and revokes API keys, and opens a checkout page. **It cannot
solve a captcha.**

An API key (`ck_live_…`) solves captchas. **It cannot manage the account** — the
account API returns 401 for one, exactly as it does for a random string.

That split is the point. If they were one credential, a key leaked from a
scraper could mint its own replacements faster than anyone could revoke them,
and the balance would stop being a bound on the damage. Either can be revoked
from the dashboard.

Nothing here can spend money. `get_topup_link` returns a URL; a person clicks
it, on a page Stripe hosts. There is no tool that charges a card.

## Where the token lives

`~/.config/captchakraken/mcp.json`, mode 0600 in a 0700 directory, keyed by the
control-plane origin — so rehearsing against a staging deployment and then
running against production switches identity cleanly instead of presenting a
staging token to production and 401ing with no explanation.

It is not encrypted. A local secret encrypted with a local key is ceremony, not
protection: whoever can read the file can read the key. The real defences are
the file mode, the token's one-year expiry, and the dashboard's disconnect
button.

Delete the file to forget everything, or run `sign_out`, which also revokes the
token server-side. Prefer `sign_out` — deleting the file alone leaves a live
credential that nothing can reach to revoke.

## Configuration

| Variable | Default | Why |
| --- | --- | --- |
| `CAPTCHAKRAKEN_BASE_URL` | `https://captchakraken.com` | Point at a staging or self-hosted control plane. |
| `CAPTCHAKRAKEN_CLIENT_NAME` | `MCP client` | Shown on the approval page. A person approving a code should see something they recognise. |

## Development

```bash
npm install
npm run typecheck
npm run build
CAPTCHAKRAKEN_BASE_URL=http://127.0.0.1:3000 node dist/index.js
```

The server it talks to is `captchakraken-cloud`; the endpoints are documented in
that package's README under **The MCP surface**, and covered by
`test/mcp-api.test.ts` there. Nothing on stdout but MCP frames — stdout *is* the
protocol channel on this transport, and one stray `console.log` corrupts the
stream and presents as a broken server.

# Plan: stock-trading demo app (target for demo chapter 3b)

A small web app that the UiPath automations from demo chapter 3b test against. It covers exactly the 8 seeded Xray tests. **It is fake: no real money and no real market data.**

## Recommended stack

| Layer | Choice | Why |
|---|---|---|
| UI | **Svelte 5 + Vite**, built to static files | A clean look for managers. It is built on the Mac and only static files go to the VM, so the VM needs no Node. |
| API | **FastAPI** (Python 3.13), run by **uv** | Matches the "FastAPI at `/api/`" plan for the VM. A typed API with OpenAPI docs that you can show on a slide. |
| State | **SQLite** (one file) | No database server. A reset is "delete the file". |
| Serving | nginx (already there) serves the static UI and proxies `/trade/api/` to uvicorn on `127.0.0.1` | Reuses the A+ TLS setup on reitermayer.com with no new certificate. |
| Process | **systemd** service `trade-demo` | Restarts on its own and logs to journald. |

What I'd push back on:
- **A single FastAPI app with Jinja + htmx would be simpler**: no Node toolchain and very stable selectors for UiPath. Svelte is still fine, because what matters for robots is not the framework but stable selectors. Rule: every control gets a `data-testid` and an accessible label, and the DOM has no random ids.
- **Robots hate nondeterminism.** Prices must not move randomly. The app has a *market simulator* that sets the prices: a hidden demo panel in the UI and `POST /trade/api/market/price`. The stop-loss and limit tests set the price themselves, so they always behave the same way.
- **Every test run starts from a known state.** `POST /trade/api/reset?account=<name>` resets cash and holdings to fixed values. TM test cases call it in their setup step.

## Path vs. subdomain

| Option | Effort | Recommendation |
|---|---|---|
| `https://reitermayer.com/trade/` | one nginx `location` block | **Use this.** The certificate already covers it. |
| `https://trade.reitermayer.com` | DNS record, new certificate (certbot webroot is already set up), new server block | Only if the app should stand on its own later |

## Features ↔ seeded Xray tests

| Xray test (seed) | Feature needed |
|---|---|
| Buy UiPath stock with a market order | Login, quote, market buy, portfolio |
| Sell UiPath stock with a limit order | Limit sell, fills when price ≥ limit |
| Buy Oracle stock with a limit order | Limit buy, fills when price ≤ limit, open-orders list |
| Protect an Oracle position with a stop-loss order | Stop order, triggers when price ≤ stop (set through the market simulator) |
| Buy fractional Amazon shares by dollar amount | Dollar-amount orders, fractional quantity (4 decimals) |
| Sell an entire Amazon position with a market order | "Sell all" on a holding |
| Reject a UiPath purchase with insufficient funds | Validation and a visible error message |
| Cancel an open Oracle limit order | Cancel an open order, status `Cancelled` |

Symbols: `PATH`, `ORCL`, `AMZN`. Demo accounts: `trader1` … `trader20`, so that each student gets their own and runs don't collide.

## API sketch

```
POST /trade/api/login                 {user, password} → session cookie
GET  /trade/api/quotes                → [{symbol, price}]
GET  /trade/api/portfolio             → {cash, holdings[]}
GET  /trade/api/orders?status=open
POST /trade/api/orders                {symbol, side, type: market|limit|stop, qty | amount, limit?, stop?}
DELETE /trade/api/orders/{id}
POST /trade/api/market/price          {symbol, price}   (demo: re-evaluates open orders)
POST /trade/api/reset?account=trader1
GET  /trade/api/health
```

## Deployment to n4a1 (IAP only, Debian 13 ARM64)

What the VM has today (checked 17.10.2026): nginx serving the static reitermayer.com site, Python 3.13 and 3.5 GB free disk. It has no uv, Node or Docker. RAM is 4 GB, enough for one uvicorn worker.

1. **On the VM, once:** install uv for user `jre`. Create `/srv/trade-demo/{app,web,data}` owned by a system user `trade`.
2. **Build on the Mac:** `npm run build` writes the Svelte `dist/`. Pack it with the FastAPI source, `pyproject.toml` and `uv.lock` into a tarball.
3. **Copy:** `vm-scp trade-demo.tgz /tmp/`, then on the VM `sudo tar -x -C /srv/trade-demo` (the `/tmp` → `sudo cp` convention).
4. **On the VM:** run `uv sync --frozen` in `/srv/trade-demo/app`, so uv pulls ARM64 wheels.
5. **systemd:** `trade-demo.service` runs `uv run uvicorn app:api --host 127.0.0.1 --port 8081 --root-path /trade/api`. Set `Restart=always`, `User=trade`, `ProtectSystem=strict`, and `ReadWritePaths=/srv/trade-demo/data`.
6. **nginx:** add to the reitermayer.com server block:
   ```
   location /trade/      { alias /srv/trade-demo/web/; try_files $uri $uri/ /trade/index.html; }
   location /trade/api/  { proxy_pass http://127.0.0.1:8081/; limit_req zone=trade burst=20; }
   ```
   Then run `nginx -t` and reload. Define a `limit_req_zone` because the app is public.
7. **Smoke test:** `curl https://reitermayer.com/trade/api/health`, then log in once in a browser.
8. **Redeploy:** a script `deploy.sh` repeats steps 2–5 and 7. No AI is needed for that.

## Open points

- **Robots.** Can the UiPath robot reach the public URL? Unattended or serverless Automation Cloud robots, yes. Does Studio for Mac make a cross-platform project that a serverless robot can run? Check this before building the automations.
- **Repo.** Should the app be its own repo, e.g. `reitermayer/trade-demo`? I'd keep it separate from the skills, because customers don't need it.
- **Access.** Make it public with demo accounts and rate limiting, or put it behind basic auth? Public is easier for students.

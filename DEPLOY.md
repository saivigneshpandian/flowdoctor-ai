# Deploying FlowDoctor AI

The end result:

| What | Where |
| --- | --- |
| App | `https://app.13-235-91-183.sslip.io` |
| n8n | `https://n8n.13-235-91-183.sslip.io` |

n8n, the backend, the frontend and Caddy all run in Docker on EC2. Caddy is the only container reachable from the internet, and it handles HTTPS. The backend has no public address: browsers talk to the frontend, which forwards `/api/*` to it over the private Docker network.

Commands are marked **Laptop** (PowerShell, in the project folder) or **EC2** (after `ssh`).

## Before you start

1. **Sign-up needs an invite code.** Anyone with `INVITE_CODE` can create an account, connect an n8n instance and spend your Gemini and Groq quota, so share it carefully. There's no rate limiting on login or sign-up. If you publish the demo login, every visitor can approve real edits to your n8n workflows, so point the demo account at a throwaway n8n or keep the login private.
2. **Rotate the n8n API key.** The current key was printed into a Claude Code session log. In n8n go to Settings > n8n API, create a new key, delete the old one, and put the new one in `backend/.env` as `N8N_API_KEY`.
3. **Have these ready:** a GitHub account and SSH access to the server. The commands assume the login user is `ubuntu`.

## 1. Put the code on GitHub

**Laptop**

```powershell
git init -b main
```
Starts a Git repository in this folder. The project isn't one yet.

```powershell
git add .
```
Stages every file that `.gitignore` doesn't exclude.

```powershell
git ls-files | Select-String -Pattern "\.env$|\.env\.local$|\.pem$|\.db$|\.venv|node_modules|\.next"
```
Lists any secret or generated files that got staged. **This must print nothing.** If it prints a file, fix that before continuing. Your API keys and `n8n_login.pem` must never reach GitHub. Pushing even once means treating those secrets as leaked.

```powershell
git commit -m "Prepare production deployment"
```
Saves the staged files as a commit.

Create an empty repository on github.com named `flowdoctor-ai`, without a README, then:

```powershell
git remote add origin https://github.com/<your-username>/flowdoctor-ai.git
git push -u origin main
```
Links the local repository to GitHub and uploads it.

`backend/tests/fixtures/execution_error_sample.json` is real execution data. It contains your server's IP address and a test lead. Check you're happy with that before making the repository public.

## 2. Connect to the server and back up n8n

**Laptop**

```powershell
ssh -i n8n_login.pem ubuntu@13.235.91.183
```
Logs in to the server. Everything marked **EC2** runs in this session.

**EC2**

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
```
Shows what's running now. You should see the n8n container.

```bash
docker volume ls
```
Lists Docker volumes. Find the n8n one, which is probably `flowdoctor_n8n_data`. **This exact name is your `N8N_VOLUME_NAME`.**

If no volume looks right, your current compose file may use a folder path for n8n's data instead. Stop here and sort that out first, because this deployment expects a named volume.

```bash
VOLUME=flowdoctor_n8n_data
```
Stores the name you just found, for the next command. Replace it if yours differs.

```bash
docker run --rm -v "$VOLUME":/data:ro -v "$HOME":/backup alpine tar czf /backup/n8n-backup-$(date +%F).tar.gz -C /data .
```
Starts a throwaway container that copies all of n8n's data (workflows, credentials, encryption key) to an archive in your home folder.

```bash
ls -lh ~/n8n-backup-*.tar.gz
```
Confirms the backup exists and isn't tiny.

## 3. Read the current n8n config

**EC2**

```bash
cat ~/flowdoctor/docker-compose.yml
```
Prints the n8n config running today. `deploy/docker-compose.yml` could not be written from this, because the file was never available locally. Compare the two by hand:

- **`image:`** becomes `N8N_IMAGE` in step 5. Using the same value avoids an accidental n8n upgrade. If it has no version tag, the command below finds the version.
- **Any other `environment:` entries**, such as `N8N_ENCRYPTION_KEY`, `GENERIC_TIMEZONE`, `TZ` or `DB_*`, must be copied into the `n8n` service in `deploy/docker-compose.yml`. **`N8N_ENCRYPTION_KEY` matters most**: without it n8n can't decrypt your saved credentials.
- **Don't copy** `N8N_HOST`, `N8N_PROTOCOL`, `N8N_PORT`, `WEBHOOK_URL` or `N8N_EDITOR_BASE_URL`. Those change for HTTPS.
- **`volumes:`** must mount at `/home/node/.n8n`. If yours is different, change the new file to match.

```bash
docker exec $(docker ps -qf name=n8n | head -1) n8n --version
```
Prints the running n8n version, so you can pin an exact image such as `docker.n8n.io/n8nio/n8n:<version>`.

If you changed `deploy/docker-compose.yml`, commit and push it from your laptop before step 4.

## 4. Open the firewall

In the AWS console, go to EC2 > Security Groups and edit the group attached to this instance. Set these inbound rules:

| Port | Source | Why |
| --- | --- | --- |
| 22 | Your IP | SSH |
| 80 | Anywhere | Let's Encrypt verification and redirect to HTTPS |
| 443 | Anywhere | HTTPS |
| 443 UDP | Anywhere | HTTP/3 (optional) |

Leave port 5678 open until step 8, so rolling back stays easy.

Check that the instance has an **Elastic IP**. Without one, the public IP can change when the instance stops, and the addresses below stop working because the IP is part of the hostname.

## 5. Get the code and config onto the server

**EC2**

```bash
git clone https://github.com/<your-username>/flowdoctor-ai.git ~/flowdoctor-ai
```
Downloads the code. For a private repository, this needs a GitHub token or deploy key.

```bash
cp ~/flowdoctor-ai/deploy/.env.example ~/flowdoctor-ai/deploy/.env
nano ~/flowdoctor-ai/deploy/.env
```
Creates the deployment settings and opens them for editing. Set `N8N_IMAGE` and `N8N_VOLUME_NAME` from steps 2 and 3, and fill in the account settings described next. Save with Ctrl+O, Enter, and exit with Ctrl+X.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```
Prints a random `SECRET_KEY`, which signs login sessions. Changing it later logs everyone out.

```bash
docker run --rm python:3.11-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```
Prints an `ENCRYPTION_KEY`, which encrypts stored n8n API keys. **Keep a copy somewhere safe.** If it changes or is lost, stored keys can't be read and every user must reconnect n8n.

Also set `DEMO_EMAIL` and a strong `DEMO_PASSWORD`. On every start the backend makes sure this account exists and links it to `N8N_URL` and `N8N_API_KEY`. Any older incidents and proposals move to it. If the account already exists, changing `DEMO_PASSWORD` doesn't change its password.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(12))"
```
Prints a random `INVITE_CODE`, which people must enter to sign up. While it's empty, sign-up is closed and only the demo account can log in. To change it, edit `deploy/.env` and run `docker compose up -d --force-recreate backend`.

To show the demo login on the login page, also set `NEXT_PUBLIC_DEMO_HINT=true`, `NEXT_PUBLIC_DEMO_EMAIL` and `NEXT_PUBLIC_DEMO_PASSWORD`. **That password is then public to every visitor.** These are compiled into the frontend, so changing them later means rebuilding it.

**Laptop** (a second terminal)

```powershell
scp -i n8n_login.pem backend/.env ubuntu@13.235.91.183:~/flowdoctor-ai/backend/.env
```
Copies your secrets over SSH. Git doesn't carry them, which is the point.

**EC2**

```bash
chmod 600 ~/flowdoctor-ai/backend/.env ~/flowdoctor-ai/deploy/.env
```
Makes both files readable only by your user.

```bash
grep -cE '^(GEMINI_API_KEY|GROQ_API_KEY|GROQ_MODEL|N8N_API_KEY)=.' ~/flowdoctor-ai/backend/.env
```
Counts the required settings that have a value, without printing any secrets. It should print `4`.

`N8N_URL` and `DATABASE_PATH` in that file can stay as they are, because the compose file overrides them.

## 6. Add a 2 GB swap file

This instance has about 2 GB of memory, and building the frontend while n8n is running can use more than that. Without swap, the build can be killed partway through with no clear error. Swap lets Linux use disk as overflow memory.

**EC2**

```bash
free -h
```
Shows memory and swap. If the `Swap:` line already shows 2 GB or more, skip the rest of this step.

```bash
df -h /
```
Shows free disk space. You need at least 2 GB for swap plus about 5 GB for the Docker images. If you have less, increase the EBS volume first.

```bash
sudo fallocate -l 2G /swapfile
```
Creates an empty 2 GB file to use as swap.

```bash
sudo chmod 600 /swapfile
```
Makes the file readable only by root, because it can contain anything that was in memory, including secrets.

```bash
sudo mkswap /swapfile
```
Formats the file as swap space.

```bash
sudo swapon /swapfile
```
Starts using it right away.

```bash
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
Adds it to the list of disks used at boot, so swap comes back after a restart. Run this only once, or the line is added twice.

```bash
swapon --show
```
Confirms it's active. You should see `/swapfile` with a size of 2G.

## 7. Build and start the stack

**EC2**

```bash
cd ~/flowdoctor-ai/deploy
```
Compose reads `docker-compose.yml` and `.env` from this folder.

```bash
docker compose config --quiet && echo OK
```
Checks the config without starting anything. It should print `OK`. A message about a missing variable means `deploy/.env` isn't filled in.

```bash
docker volume inspect "$(grep '^N8N_VOLUME_NAME=' .env | cut -d= -f2)" --format '{{.Name}} OK'
```
Confirms the volume name matches a real volume.

```bash
docker compose build backend
```
Builds the backend image. Your current n8n keeps running while this happens.

```bash
docker compose build frontend
```
Builds the frontend image, which can take several minutes on this instance. Building the two separately keeps memory use down. If this ends with `Killed` or exit code 137, it ran out of memory: check step 6 worked, then run it again.

```bash
cd ~/flowdoctor && docker compose down
```
Stops the old n8n so its ports and volume are free. **Never add `-v`**: that flag deletes volumes. n8n is offline from here until the next command finishes.

```bash
cd ~/flowdoctor-ai/deploy && docker compose up -d
```
Starts all four containers in the background, using the images you just built.

```bash
docker compose ps
```
All four should show `running`, and the backend and frontend should become `healthy` within a minute.

```bash
docker compose logs caddy --tail 30
```
Look for `certificate obtained successfully` for both hostnames. If it reports rate limits or failed challenges, confirm ports 80 and 443 are open (step 4). `sslip.io` is a shared domain, so Let's Encrypt limits can occasionally apply; Caddy keeps retrying.

```bash
curl -s https://app.13-235-91-183.sslip.io/api/health
```
Should print `{"ok":true}`. This request goes through Caddy, the frontend and the backend, so it proves HTTPS and the whole chain work.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://n8n.13-235-91-183.sslip.io/healthz
```
Should print `200`.

Open `https://n8n.13-235-91-183.sslip.io` in a browser and log in. **Check that your workflows are there, and that a credential still works.** If n8n asks you to create a new owner account, it isn't using your old data: go to [Roll back](#roll-back).

Open `https://app.13-235-91-183.sslip.io`. You should land on the login page. Log in with the demo account: the dashboard should open with Lead Capture and its real status in the sidebar.

Everything the browser needs comes from this one address, so the session cookie is first-party and the backend needs no CORS settings.

## 8. Finish up

**Close port 5678.** In the security group, remove the 5678 rule. n8n is now only reachable through Caddy. The old `http://13.235.91.183:5678` address stops working.

**Update webhook URLs.** Anything sending to the old address must switch:

- Lead form: `https://n8n.13-235-91-183.sslip.io/webhook/lead-capture`
- In n8n, open each workflow with a webhook and confirm it shows the new URL.

**Create the Error Handler workflow** (SPEC section 11), if you haven't yet: an Error Trigger followed by an HTTP Request node.

- Method and URL: `POST` to your own webhook URL, copied from the user menu at the bottom of the sidebar. It looks like `https://app.13-235-91-183.sslip.io/api/webhook/n8n-error/<token>`. For the n8n on this server you can use `http://backend:8000/webhook/n8n-error/<token>` instead, which stays on the Docker network. Each user has a different token, and it's the only credential: anyone with this URL can file failures against that account, so keep it private.
- Body: the Error Trigger's output.
- Set it as the Error Workflow in Lead Capture's settings.

**Test end to end.** Trigger a failure, ask why it failed, and approve the fix in the app.

The backend starts with an empty database on the server, so past local incidents won't appear. Users who sign up add their own Telegram chat ID on the Connect screen. `TELEGRAM_CHAT_ID` from the env file only applies to the demo account.

## Updating later

**Laptop:** `git push`, after committing your changes.

**EC2**

```bash
cd ~/flowdoctor-ai && git pull
```
Gets the new code.

```bash
cd deploy && docker compose build backend && docker compose build frontend
```
Rebuilds both images, one at a time to keep memory use down. The running app isn't affected yet.

```bash
docker compose up -d
```
Restarts only the containers whose image or settings changed. Volumes are kept, so n8n data, incidents and certificates survive.

```bash
docker image prune -f
```
Deletes old image layers that nothing uses any more, so repeated builds don't fill the disk.

## Turn on approving fixes from Telegram

The backend fetches button taps and replies from Telegram itself (long polling), so Telegram never has to reach your server and no secret is needed. Do this once on a server that's already deployed. A new deployment only needs the checks at the end.

**EC2**

```bash
cd ~/flowdoctor-ai && git pull
```
Gets the new code.

```bash
cd deploy && docker compose build backend
```
Rebuilds the backend. The frontend hasn't changed, so it doesn't need a rebuild.

```bash
docker compose up -d
```
Restarts the backend. It adds any new database columns by itself. Every time it starts, it clears any Telegram webhook and starts polling. `TELEGRAM_WEBHOOK_SECRET` is now optional, so you can remove it from `deploy/.env` or leave it.

```bash
docker compose logs backend --tail 50 | grep -i telegram
```
Should show `Telegram polling started`, and no `getUpdates failed` or `deleteWebhook failed` lines after it. A one-off `getUpdates failed` during a restart is normal. If it repeats, check `TELEGRAM_BOT_TOKEN`, and that nothing else is polling with this bot.

```bash
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' ../backend/.env | cut -d= -f2); curl -s "https://api.telegram.org/bot$TOKEN/getWebhookInfo"; unset TOKEN
```
Asks Telegram what it has on file. `url` should be empty, which means polling is in charge. The command puts your bot token in this shell only briefly, and the output doesn't include it.

**Who can decide a fix.** Only the chat ID saved on the owner's Connect screen. If that's a group chat, **every member of the group can approve fixes**, so use a private chat unless that's what you want. Alerts show the cause and a summary of the fix, not the full diff, so open the app first if you want to read the code.

**Taps during a restart.** A tap or reply sent while the backend is down is discarded when it starts, so an old approval can't edit a workflow later. Nothing happens to the fix, and you can tap the button again.

**Only one backend can poll a bot.** It only polls when `PUBLIC_API_URL` starts with `https://`. If you run the backend on your laptop with the same bot, leave `PUBLIC_API_URL` empty there, or it will take updates away from the server.

## The "Simulate failure" buttons

Logged in as the demo account (`DEMO_EMAIL`), workflows with a known demo bug get a small **Simulate failure** button in the sidebar, so judges can try the full flow. Other workflows, such as Error Handler, don't show it. No other account sees it, and the backend refuses it for anyone else. It needs no new settings.

**EC2**, from `~/flowdoctor-ai`:

```bash
git pull
```
Gets the new code.

```bash
cd deploy && docker compose build backend frontend
```
Rebuilds both, because both changed.

```bash
docker compose up -d
```
Restarts them with the new code.

| Workflow (exact name) | Node it breaks | Test request |
| --- | --- | --- |
| `Lead Capture` | `Normalize Lead` | A lead with a capital `Email` field |
| `Order Processing` | `Calculate Total` | An order without the `order` wrapper |

Each click opens that workflow's chat and does this through the n8n API, at most once per 2 minutes per workflow:

1. Rejects that workflow's open fixes.
2. Puts the original bug back in the node and republishes the workflow.
3. Sends one test request to the workflow's webhook.

The workflow also needs a webhook node. If the node or webhook is missing, the button shows the problem and changes nothing.

**What this means for you:**

- **It overwrites those nodes.** Point the demo account at an n8n where it's fine for these workflows to be broken on purpose.
- **It can message you.** If a workflow has the Error Handler workflow set up, every click also starts a background diagnosis, which uses LLM quota and sends an alert to the demo account's Telegram chat.
- **Judges share one account.** One judge's click can reject a fix another judge is looking at, and all of them can approve fixes.
- **The limit resets on restart.** It's kept in the backend's memory.

## Hide a workflow from FlowDoctor

Add the tag `flowdoctor-ignore` to a workflow in n8n, for example to Error Handler. FlowDoctor then leaves it out of the sidebar and the health counts, won't diagnose it from chat, and skips its failures with no diagnosis and no Telegram alert. It reads tags live from n8n, so adding or removing a tag needs no restart. The sidebar catches up within 15 seconds.

To use a different tag name, set `IGNORE_TAG` in `deploy/.env`. The match ignores upper and lower case.

**EC2**, from `~/flowdoctor-ai`, once to get this feature:

```bash
git pull
```
Gets the new code.

```bash
cd deploy && docker compose build backend
```
Rebuilds the backend. The frontend hasn't changed.

```bash
docker compose up -d
```
Restarts it with the new code.

Chats and incidents from before you added the tag stay visible, and a fix that was already open can still be decided.

## Useful commands

**EC2**, from `~/flowdoctor-ai/deploy`:

```bash
docker compose logs -f backend
```
Follows the backend logs, including which LLM provider served each call. Press Ctrl+C to stop.

```bash
docker compose restart caddy
```
Restarts Caddy, for example after editing the `Caddyfile`.

## Roll back

**EC2**

```bash
cd ~/flowdoctor-ai/deploy && docker compose down
```
Stops the new stack. Your n8n volume is external, so it's kept.

```bash
cd ~/flowdoctor && docker compose up -d
```
Starts the old setup again at `http://13.235.91.183:5678`. Port 5678 must still be open.

If n8n's data itself is damaged, restore the backup while n8n is stopped. **This overwrites the current data.**

```bash
docker run --rm -v "$VOLUME":/data -v "$HOME":/backup alpine sh -c "rm -rf /data/* /data/.[!.]* ; tar xzf /backup/n8n-backup-<date>.tar.gz -C /data"
```
Empties the volume and unpacks the archive from step 2. Set `VOLUME` again first if this is a new session.

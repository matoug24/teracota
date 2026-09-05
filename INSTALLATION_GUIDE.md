# TeraCota Lightsail Installation and Operations Guide

This is the complete runbook for installing, updating, backing up, troubleshooting,
and rebuilding TeraCota on an Amazon Lightsail Ubuntu server.

It is written for the current deployment:

- Public URL: `https://teracota.matoug.com`
- GitHub repository: `https://github.com/matoug24/teracota.git`
- Git branch: `main`
- Server operating system: Ubuntu 24.04 LTS
- Application directory: `/opt/teracota`
- Database: `/var/lib/teracota/teracota.sqlite3`
- Database backups: `/var/backups/teracota`
- Linux service account: `teracota`
- Public repository during the normal installation

A separate section near the end explains exactly what changes if the GitHub
repository becomes private.

## 1. Understand the deployment

The request path is:

```text
Browser
  -> HTTPS port 443
  -> Nginx
  -> Gunicorn on 127.0.0.1:8000
  -> Flask
  -> SQLite
```

Each part has one job:

- **Lightsail** provides the Ubuntu server and public IP address.
- **DNS** sends `teracota.matoug.com` to the Lightsail static IP.
- **Nginx** accepts public HTTP/HTTPS traffic and forwards it to Gunicorn.
- **Certbot** installs and renews the HTTPS certificate.
- **Gunicorn** runs the Flask application in production.
- **systemd** starts Gunicorn at boot and restarts it after a failure.
- **Flask** serves pages and APIs and applies additive SQLite migrations at startup.
- **SQLite** stores systems, visits, issues, tasks, settings, deleted records, and visitor logs.

Do not expose ports `8000` or `8765` through the Lightsail firewall. Nginx is the
only public web entry point.

SQLite is appropriate for one TeraCota server. Keep the configured single
Gunicorn worker. Move to PostgreSQL before running multiple application servers
or multiple Lightsail instances against the same data.

## 2. Know what is stored where

Keeping code, secrets, and data separate makes Git updates and future rebuilds safer.

| Content | Location | Stored in GitHub? |
| --- | --- | --- |
| Application code | `/opt/teracota` | Yes |
| Production settings and password | `/opt/teracota/.env` | No |
| SQLite database | `/var/lib/teracota/teracota.sqlite3` | No |
| Automatic/manual backups | `/var/backups/teracota` | No |
| systemd service | `/etc/systemd/system/teracota.service` | Installed from the repo |
| Live Nginx configuration | `/etc/nginx/sites-available/teracota` | Initially installed from the repo |
| HTTPS certificates | `/etc/letsencrypt` | No |

The repository is public, so assume anyone can read every committed file. This
is safe only because `.env`, databases, keys, logs, and backups are excluded by
`.gitignore`.

Never put a real password, session secret, private key, database, or exported CSV
backup into Git. If a secret is ever committed, deleting it later is not enough
because it remains in Git history. Rotate the exposed secret immediately.

## 3. Fresh-install checklist

For a completely new server, follow these sections in order:

1. Prepare and push the public GitHub repository.
2. Create the Lightsail instance and static IP.
3. Configure the Lightsail firewall and DNS.
4. Connect over SSH and install Ubuntu packages.
5. Create the `teracota` service account and directories.
6. Clone the public repository.
7. Create the Python virtual environment.
8. Create the production `.env` file.
9. Install and start the systemd service.
10. Install the Nginx configuration.
11. Install HTTPS with Certbot.
12. Run the verification checklist.

For a replacement server with existing data, follow the same process, then
restore a SQLite or CSV backup before users add new records.

## 4. Prepare the public GitHub repository

Run these commands in Windows PowerShell from the local project directory:

```powershell
git remote -v
git branch --show-current
git status --short
git check-ignore -v .env teracota.sqlite3
```

Expected results:

- `origin` points to `https://github.com/matoug24/teracota.git`.
- The active branch is `main`.
- `.env` and `teracota.sqlite3` are reported as ignored.

Review and push the application:

```powershell
git add .
git status --short
git commit -m "Prepare TeraCota deployment"
git push origin main
```

Inspect the public repository in a browser. Confirm that it contains
`.env.example`, but does not contain `.env`, any `*.sqlite3` file, a private key,
or a real password.

## 5. Create the Lightsail server

In the Amazon Lightsail console:

1. Create an instance using the OS-only Ubuntu 24.04 LTS image.
2. Choose at least 1 GB RAM. A 2 GB plan provides more room for updates.
3. Create a Lightsail static IP in the same region.
4. Attach the static IP to the instance.

The static IP is important because the ordinary public IPv4 address can change
after the instance is stopped and started. DNS should point to the static IP.

Configure the instance's IPv4 firewall:

| Application | Port | Source |
| --- | ---: | --- |
| SSH | 22 | Your public IP, when practical |
| HTTP | 80 | All IPv4 addresses |
| HTTPS | 443 | All IPv4 addresses |

Only add equivalent IPv6 rules if you publish an IPv6 DNS record. Lightsail's
IPv4 and IPv6 firewalls are separate.

## 6. Configure DNS

At the DNS provider for `matoug.com`, create:

| Record type | Name | Value | TTL |
| --- | --- | --- | ---: |
| A | `teracota` | Lightsail static IPv4 address | 300 |

Do not modify the root `matoug.com` record or the `www` record.

Verify DNS from your local computer:

```powershell
nslookup teracota.matoug.com
```

The returned address must match the Lightsail static IP before requesting HTTPS.

## 7. Connect to Ubuntu

The default username for the Lightsail Ubuntu image is `ubuntu`. Use the
Lightsail browser SSH terminal, or connect from Windows PowerShell:

```powershell
ssh -i "C:\path\to\lightsail-key.pem" ubuntu@YOUR_LIGHTSAIL_STATIC_IP
```

Commands in the remaining installation sections run in the Ubuntu SSH terminal.

## 8. Install Ubuntu packages

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip nginx sqlite3 curl snapd ufw
```

Allow SSH before enabling UFW so the current SSH session is not locked out:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
sudo ufw status
```

Both the Lightsail firewall and UFW apply. A port must be allowed through both.

## 9. Create the service account and directories

The app should not run as `ubuntu` or `root`. Create a dedicated account:

```bash
sudo adduser --system --group \
  --home /var/lib/teracota \
  --shell /usr/sbin/nologin \
  teracota
```

Create the code, data, and backup directories:

```bash
sudo install -d -o teracota -g teracota -m 0750 /opt/teracota
sudo install -d -o teracota -g teracota -m 0750 /var/lib/teracota
sudo install -d -o teracota -g teracota -m 0750 /var/backups/teracota
```

The mode `0750` lets the service account use these directories while blocking
ordinary users from reading application data.

### Important permission behavior

This may fail for the `ubuntu` user:

```bash
cd /opt/teracota
```

`Permission denied` is expected. It does not mean the deployment is broken. Do
not loosen the directory to `0777`.

Run repository commands as the service account instead:

```bash
sudo -u teracota git -C /opt/teracota status
sudo ls -la /opt/teracota
```

The update command described later works from `/home/ubuntu`; you do not need to
enter `/opt/teracota`.

## 10. Clone the public repository

Because the current repository is public, no GitHub token or SSH key is needed:

```bash
sudo -u teracota git clone \
  https://github.com/matoug24/teracota.git \
  /opt/teracota
```

Verify the clone:

```bash
sudo -u teracota git -C /opt/teracota remote -v
sudo -u teracota git -C /opt/teracota branch --show-current
sudo -u teracota git -C /opt/teracota status --short
```

The remote should use HTTPS, the branch should be `main`, and status should be clean.

## 11. Create the Python environment

```bash
sudo -u teracota python3 -m venv /opt/teracota/.venv
sudo -u teracota /opt/teracota/.venv/bin/python -m pip install --upgrade pip
sudo -u teracota /opt/teracota/.venv/bin/pip install \
  -r /opt/teracota/requirements.txt
```

The virtual environment isolates TeraCota's dependencies from Ubuntu's Python.

Verify the imports:

```bash
sudo -u teracota /opt/teracota/.venv/bin/python -c \
  "import flask, gunicorn; print('Python dependencies are ready')"
```

## 12. Create the production environment file

Generate a random Flask session secret:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Copy the safe template into a server-only `.env` file:

```bash
sudo install -o root -g teracota -m 0640 \
  /opt/teracota/.env.example \
  /opt/teracota/.env
sudo nano /opt/teracota/.env
```

Set the file to:

```text
TERACOTA_ENV=production
TERACOTA_SECRET_KEY=PASTE_THE_RANDOM_64_CHARACTER_VALUE
TERACOTA_USERNAME=teraview
TERACOTA_PASSWORD="USE_A_NEW_LONG_UNIQUE_PASSWORD"
TERACOTA_DB_PATH=/var/lib/teracota/teracota.sqlite3
TERACOTA_BEHIND_PROXY=true
TERACOTA_COOKIE_SECURE=true
TERACOTA_SEED_DEMO=false
```

| Variable | Purpose |
| --- | --- |
| `TERACOTA_ENV` | Enables production safety checks. |
| `TERACOTA_SECRET_KEY` | Signs session cookies. Changing it logs everyone out. |
| `TERACOTA_USERNAME` | Shared application login username. |
| `TERACOTA_PASSWORD` | Shared application login password. |
| `TERACOTA_DB_PATH` | Keeps the database outside the Git checkout. |
| `TERACOTA_BEHIND_PROXY` | Trusts Nginx's forwarded protocol and IP headers. |
| `TERACOTA_COOKIE_SECURE` | Sends the login cookie only over HTTPS. |
| `TERACOTA_SEED_DEMO` | Keeps a new production database empty. |

The app refuses to start in production if the password is still `pythagorus` or
the session key is still the development default.

Confirm names and permissions without printing secret values:

```bash
sudo sed -n 's/^\(TERACOTA_[A-Z_]*\)=.*/\1=<set>/p' /opt/teracota/.env
sudo stat -c '%U %G %a %n' /opt/teracota/.env
```

Expected permission output:

```text
root teracota 640 /opt/teracota/.env
```

The `.env` file is ignored by Git, so a pull will not overwrite the password.

## 13. Install and start systemd

```bash
sudo install -o root -g root -m 0644 \
  /opt/teracota/deploy/teracota.service \
  /etc/systemd/system/teracota.service
sudo systemctl daemon-reload
sudo systemctl enable --now teracota
```

Check the service and private health endpoint:

```bash
sudo systemctl status teracota --no-pager
curl -fsS http://127.0.0.1:8000/healthz
```

Expected response:

```json
{"status":"ok"}
```

At startup, `wsgi.py` calls `init_db()`. It creates a database when none exists
and adds required columns when the schema grows. Existing records are not removed
by these additive migrations.

If startup fails:

```bash
sudo journalctl -u teracota -n 100 --no-pager
```

## 14. Install the Nginx configuration

```bash
sudo install -o root -g root -m 0644 \
  /opt/teracota/deploy/nginx-teracota.conf \
  /etc/nginx/sites-available/teracota
sudo ln -sfn /etc/nginx/sites-available/teracota /etc/nginx/sites-enabled/teracota
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Test Nginx locally before HTTPS:

```bash
curl -fsS -H 'Host: teracota.matoug.com' http://127.0.0.1/healthz
```

Do not troubleshoot login cookies over HTTP. Production cookies are HTTPS-only.

## 15. Enable HTTPS

Wait until DNS returns the Lightsail static IP. Then install Certbot:

```bash
sudo apt-get remove -y certbot
sudo snap install core
sudo snap refresh core
sudo snap install --classic certbot
sudo ln -sfn /snap/bin/certbot /usr/local/bin/certbot
```

Request the certificate and let Certbot update Nginx:

```bash
sudo certbot --nginx -d teracota.matoug.com
```

Choose the HTTP-to-HTTPS redirect when prompted. Test renewal:

```bash
sudo certbot renew --dry-run
```

The final address is `https://teracota.matoug.com/`.

## 16. Verify a new installation

Run on the server:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS https://teracota.matoug.com/healthz
curl -I https://teracota.matoug.com/
curl -i https://teracota.matoug.com/api/state
sudo systemctl is-active teracota
sudo systemctl is-enabled teracota
sudo systemctl is-active nginx
sudo nginx -t
sudo ss -lntp | grep -E ':80|:443|:8000'
```

Expected behavior:

- Health checks return `{"status":"ok"}`.
- `/` redirects to `/login` while signed out.
- `/api/state` returns HTTP `401` while signed out. This is expected.
- `teracota` and `nginx` report `active`.
- Gunicorn listens on `127.0.0.1:8000`, not `0.0.0.0:8000`.

Verify in a browser:

1. The URL uses `https://` and has a valid certificate.
2. Login appears before application data.
3. Login works with the `.env` credentials.
4. The Systems dashboard loads.
5. `/admin`, `/statistics`, and `/logs` load after login.
6. Add a temporary system, issue, and visit and test editing.
7. Restart the service and confirm the records remain.
8. Delete the temporary records.

With `TERACOTA_SEED_DEMO=false`, a new database has no systems. The **Add System**
button should still be visible after login.

## 17. Update from the public repository

First push tested local changes:

```powershell
git status --short
git add .
git status --short
git commit -m "Describe the TeraCota update"
git push origin main
```

For the first update that includes the updater:

```bash
sudo -u teracota git -C /opt/teracota pull --ff-only origin main
sudo bash /opt/teracota/deploy/update_lightsail.sh
```

Every later deployment is one command, run from any directory:

```bash
sudo update-teracota
```

Do not `cd /opt/teracota` as `ubuntu`, and do not run Git as `root`.

The updater:

1. Prevents two simultaneous updates.
2. Stops if the server checkout has uncommitted changes.
3. Creates a timestamped SQLite backup.
4. Fast-forwards `origin/main`.
5. Installs current Python requirements.
6. Compiles Python files to catch syntax errors.
7. Updates and reloads the systemd service definition.
8. Validates the live Nginx configuration.
9. Restarts TeraCota, which runs database migrations.
10. Waits for `/healthz` before reloading Nginx.
11. Prints commits and the database backup path.

It preserves `.env`, `.venv`, the SQLite database, backups, and Certbot's live
HTTPS configuration.

After updating, hard-refresh with `Ctrl+F5` so HTML, JavaScript, and CSS come from
the same release.

Verify the deployment:

```bash
sudo -u teracota git -C /opt/teracota rev-parse --short HEAD
sudo -u teracota git -C /opt/teracota status --short
sudo systemctl status teracota --no-pager
curl -fsS http://127.0.0.1:8000/healthz
```

### Change the application password later

The production password exists only in the server's `.env` file. It is not
changed through GitHub and does not require reinstalling the application.

```bash
sudo nano /opt/teracota/.env
sudo systemctl restart teracota
curl -fsS http://127.0.0.1:8000/healthz
```

Change only `TERACOTA_PASSWORD`, keep the file ownership and permissions intact,
then log in again using the new password. Existing browser sessions may remain
valid until the session cookie expires; change `TERACOTA_SECRET_KEY` as well when
you intentionally need to invalidate every existing session.

## 18. Database operations

The database contains application records and visitor logs. It does not contain
the login password; that remains in `.env`.

The admin CSV export excludes visitor logs. Use SQLite backup for a complete copy.

### Confirm path, size, and integrity

```bash
sudo grep '^TERACOTA_DB_PATH=' /opt/teracota/.env
sudo ls -lh /var/lib/teracota/
sudo du -h /var/lib/teracota/teracota.sqlite3
sudo du -ch /var/lib/teracota/teracota.sqlite3*
sudo stat -c '%n: %s bytes' /var/lib/teracota/teracota.sqlite3*
df -h /
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 \
  'PRAGMA integrity_check;'
```

The integrity result should be `ok`. Substitute the configured database path if
it differs from `/var/lib/teracota/teracota.sqlite3`.

### Create a consistent backup

SQLite's `.backup` command is safe while the app is running:

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 \
  ".backup '/var/backups/teracota/teracota-${STAMP}.sqlite3'"
sudo ls -lh /var/backups/teracota/
```

Keep a backup outside Lightsail. A backup on the same disk does not protect
against loss of the whole server.

### Restore a SQLite backup

```bash
sudo systemctl stop teracota
sudo rm -f -- \
  /var/lib/teracota/teracota.sqlite3-wal \
  /var/lib/teracota/teracota.sqlite3-shm
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 \
  ".restore '/var/backups/teracota/teracota-YYYYMMDD-HHMMSS.sqlite3'"
sudo systemctl start teracota
curl -fsS http://127.0.0.1:8000/healthz
```

Inspect several records after restoration.

### Reset the database and start fresh

This permanently removes systems, visits, issues, development items, settings,
deleted records, and visitor logs. It does not remove `.env`, so credentials stay.

Stop the app and create a final backup:

```bash
sudo systemctl stop teracota
STAMP=$(date +%Y%m%d-%H%M%S)
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 \
  ".backup '/var/backups/teracota/before-reset-${STAMP}.sqlite3'"
```

Delete the database and its working files using the explicit path:

```bash
sudo rm -f -- \
  /var/lib/teracota/teracota.sqlite3 \
  /var/lib/teracota/teracota.sqlite3-wal \
  /var/lib/teracota/teracota.sqlite3-shm
```

Start the app to create a fresh schema:

```bash
sudo systemctl start teracota
sudo systemctl status teracota --no-pager
curl -fsS http://127.0.0.1:8000/healthz
sudo ls -lh /var/lib/teracota/
```

`TERACOTA_SEED_DEMO=false` means no sample systems are added.

### Move data to a replacement server

Preferred application-data method:

1. Export CSV from `/admin` on the old server.
2. Install and verify the new server.
3. Restore the CSV from `/admin` on the new server.
4. Verify systems, visits, issues, tasks, settings, and deleted records.

CSV does not move visitor logs. To move everything, create a SQLite `.backup`,
securely copy it, stop the new app, and restore it. Do not copy a live SQLite file
directly while the old app may be writing to it.

## 19. Troubleshooting

### `/opt/teracota` says `Permission denied`

This is expected for `ubuntu`. Use:

```bash
sudo -u teracota git -C /opt/teracota status
sudo -u teracota git -C /opt/teracota log -1 --oneline
sudo ls -la /opt/teracota
```

For updates, run `sudo update-teracota` without changing directory.

### Dashboard empty, Add System missing, or `/admin` blank

The running process may have an older HTML template while newer JavaScript files
are on disk, or `/api/state` may have failed.

```bash
sudo systemctl restart teracota
curl -fsS http://127.0.0.1:8000/healthz
sudo systemctl status teracota --no-pager
sudo journalctl -u teracota -n 100 --no-pager
```

Then hard-refresh with `Ctrl+F5` or use a private browser window.

Confirm data exists before replacing anything:

```bash
sudo grep '^TERACOTA_DB_PATH=' /opt/teracota/.env
sudo ls -lh /var/lib/teracota/
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 \
  'SELECT COUNT(*) AS systems FROM systems;'
```

### `Unable to save. Please log in and try again.`

Use `https://teracota.matoug.com/`, not HTTP or the server IP. Secure cookies are
not sent over HTTP.

```bash
sudo journalctl -u teracota -n 100 --no-pager
sudo grep -E '^TERACOTA_(ENV|BEHIND_PROXY|COOKIE_SECURE)=' /opt/teracota/.env
```

Expected values:

```text
TERACOTA_ENV=production
TERACOTA_BEHIND_PROXY=true
TERACOTA_COOKIE_SECURE=true
```

After changing `.env`, restart, log out, hard-refresh, and log in again.

### Service refuses to start

```bash
sudo systemctl status teracota --no-pager
sudo journalctl -u teracota -n 100 --no-pager
```

If the journal mentions the default password or secret:

```bash
sudo systemctl stop teracota
sudo nano /opt/teracota/.env
sudo chown root:teracota /opt/teracota/.env
sudo chmod 0640 /opt/teracota/.env
sudo systemctl start teracota
```

Do not add `export` or spaces around `=` in `.env`.

### Database is read-only or cannot be opened

```bash
sudo grep '^TERACOTA_DB_PATH=' /opt/teracota/.env
sudo namei -l /var/lib/teracota/teracota.sqlite3
sudo ls -la /var/lib/teracota/
sudo chown -R teracota:teracota /var/lib/teracota
sudo chmod 0750 /var/lib/teracota
sudo find /var/lib/teracota -maxdepth 1 -type f -name 'teracota.sqlite3*' \
  -exec chmod 0640 {} \;
sudo systemctl restart teracota
curl -fsS http://127.0.0.1:8000/healthz
```

### `502 Bad Gateway`

```bash
sudo systemctl status teracota --no-pager
sudo journalctl -u teracota -n 100 --no-pager
curl -v http://127.0.0.1:8000/healthz
sudo ss -lntp | grep ':8000'
```

### Updater reports local changes

```bash
sudo -u teracota git -C /opt/teracota status --short
sudo -u teracota git -C /opt/teracota diff
```

Do not use `git reset --hard` until you understand the changes. Edit application
code locally, commit it, push it, then deploy it.

### Certificate request or renewal fails

```bash
nslookup teracota.matoug.com
sudo ufw status
sudo nginx -t
sudo systemctl status nginx --no-pager
sudo certbot certificates
sudo certbot renew --dry-run
```

Ports 80 and 443 must be open in Lightsail and UFW, and DNS must use the static IP.

### Useful live logs

```bash
sudo journalctl -u teracota -f
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

## 20. If the GitHub repository becomes private

This section is not needed while the repository is public.

Making a formerly public repository private does not retract copies that may
already exist. Continue treating all committed history as public and never rely
on the visibility change to protect a secret that was previously committed.

### What changes

- Lightsail can no longer pull anonymously over public HTTPS.
- The server needs GitHub authentication.
- The server Git remote changes to an SSH deploy-key URL.

### What does not change

- Domain, DNS, Nginx, Certbot, or systemd
- `.env`, login credentials, or SQLite database
- Backup process
- The `sudo update-teracota` command after Git access is configured

Use a read-only GitHub deploy key. Do not put a personal access token in a Git
URL, shell history, `.env`, or deployment script.

### A. Create the deploy key

```bash
sudo -u teracota install -d -m 0700 /var/lib/teracota/.ssh
sudo -u teracota ssh-keygen -t ed25519 \
  -C 'teracota-lightsail-deploy' \
  -f /var/lib/teracota/.ssh/github_deploy \
  -N ''
sudo cat /var/lib/teracota/.ssh/github_deploy.pub
```

In GitHub, open `matoug24/teracota`, then **Settings > Deploy keys > Add deploy
key**. Name it `TeraCota Lightsail production`, paste the public key, and leave
**Allow write access** unchecked.

### B. Configure server SSH

```bash
sudo tee /var/lib/teracota/.ssh/config >/dev/null <<'EOF'
Host github.com-teracota
    HostName github.com
    User git
    IdentityFile /var/lib/teracota/.ssh/github_deploy
    IdentitiesOnly yes
EOF

sudo ssh-keyscan github.com \
  | sudo tee /var/lib/teracota/.ssh/known_hosts >/dev/null
sudo chown -R teracota:teracota /var/lib/teracota/.ssh
sudo chmod 0700 /var/lib/teracota/.ssh
sudo chmod 0600 /var/lib/teracota/.ssh/config
sudo chmod 0600 /var/lib/teracota/.ssh/github_deploy
sudo chmod 0644 /var/lib/teracota/.ssh/github_deploy.pub
sudo chmod 0644 /var/lib/teracota/.ssh/known_hosts
```

Compare the collected key with GitHub's published SSH fingerprints, then test:

```bash
sudo -u teracota ssh -T git@github.com-teracota || true
```

GitHub normally says authentication succeeded but shell access is unavailable.

### C. Change the existing server remote

Do this before the first update after making the repository private:

```bash
sudo -u teracota git -C /opt/teracota remote set-url origin \
  git@github.com-teracota:matoug24/teracota.git
sudo -u teracota git -C /opt/teracota remote -v
sudo -u teracota git -C /opt/teracota fetch origin main
sudo update-teracota
```

### D. Brand-new installation from a private repository

Complete the deploy-key steps before cloning. Replace the public clone command
in Section 10 with:

```bash
sudo -u teracota git clone \
  git@github.com-teracota:matoug24/teracota.git \
  /opt/teracota
```

All later installation steps remain unchanged.

Your local computer can continue using HTTPS through GitHub Desktop or Git
Credential Manager. Never copy the Lightsail deploy private key to your computer.

## 21. Rebuild or replace the server later

Before deleting the old instance:

1. Create and download a current SQLite backup.
2. Export an admin CSV as a second backup.
3. Record the current Git commit.
4. Securely record the `.env` values in a password manager.
5. Confirm the static IP can be detached and reassigned.
6. Take a Lightsail snapshot when practical.

Useful commands:

```bash
sudo -u teracota git -C /opt/teracota rev-parse HEAD
sudo grep '^TERACOTA_DB_PATH=' /opt/teracota/.env
sudo ls -lh /var/backups/teracota/
```

On the replacement instance:

1. Follow Sections 5 through 16.
2. Reassign the static IP, or update DNS for a new static IP.
3. Restore the SQLite backup or admin CSV.
4. Restart TeraCota.
5. Run the complete verification checklist.
6. Keep the old instance available until the new one is verified.

## 22. Security and maintenance reminders

- Keep `.env`, databases, backups, exports, and keys out of GitHub.
- Use a unique password stored in a password manager.
- Restrict SSH port 22 to your public IP when practical.
- Keep Ubuntu updated with `sudo apt update && sudo apt upgrade`.
- Keep HTTPS enabled because the production login cookie is secure-only.
- Maintain regular off-server backups.
- Review `/logs` for unexpected access.
- The current app has one shared administrative login.
- Add individual accounts, roles, rate limiting, and stronger auditing before expanding access.

## Official references

- [Lightsail static IP documentation](https://docs.aws.amazon.com/lightsail/latest/userguide/lightsail-create-static-ip.html)
- [Lightsail firewall documentation](https://docs.aws.amazon.com/lightsail/latest/userguide/understanding-firewall-and-port-mappings-in-amazon-lightsail.html)
- [Lightsail domain routing](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-routing-to-instance.html)
- [Certbot Nginx instructions](https://certbot.eff.org/instructions?ws=nginx&os=snap)
- [Gunicorn deployment documentation](https://docs.gunicorn.org/en/stable/deploy.html)
- [GitHub cloning documentation](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)
- [GitHub deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
- [GitHub SSH key fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)

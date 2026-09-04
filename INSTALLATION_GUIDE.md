# TeraCota Lightsail Installation Guide

This guide deploys TeraCota at `https://teracota.matoug.com` on one Amazon Lightsail Ubuntu server using:

- Nginx for public HTTP and HTTPS traffic.
- Gunicorn as the production Flask server.
- systemd to start the app at boot and restart it after failures.
- SQLite on the server's persistent disk.
- Certbot for a free Let's Encrypt TLS certificate.

## 1. Deployment architecture

The public application address is:

```text
https://teracota.matoug.com
```

Only the `teracota` subdomain will point to Lightsail. The existing `matoug.com` website and its `www` record remain unchanged.

The final request path is:

```text
Browser -> HTTPS 443 -> Nginx -> Gunicorn 127.0.0.1:8000 -> Flask -> SQLite
```

Do not expose ports `8000` or `8765` to the internet.

SQLite is suitable for this single-server deployment. Keep one Gunicorn worker, as configured in `gunicorn.conf.py`. Do not run this database from multiple Lightsail instances. Move to PostgreSQL before adding multiple application servers.

## 2. Create the Lightsail instance

1. Open the Lightsail console.
2. Create an instance using an OS-only Ubuntu 24.04 LTS image.
3. Select a plan with at least 1 GB RAM. A 2 GB plan leaves more room for updates and backups.
4. Create and attach a Lightsail Static IP from the **Networking** page.
5. On the instance's **Networking** tab, configure these IPv4 firewall rules:

| Application | Port | Source |
| --- | ---: | --- |
| SSH | 22 | Your public IP only, if practical |
| HTTP | 80 | All IPv4 addresses |
| HTTPS | 443 | All IPv4 addresses |

Configure equivalent IPv6 rules only if you plan to publish an IPv6 DNS record. Lightsail handles IPv4 and IPv6 firewall rules separately.

## 3. Point `teracota.matoug.com` to Lightsail

At the DNS provider that manages `matoug.com`, create this record:

| Record type | Name | Value | TTL |
| --- | --- | --- | ---: |
| A | `teracota` | Your Lightsail Static IP | 300 |

Do not change the existing `matoug.com` root record or its `www` record.

After saving the record, check it from your computer:

```powershell
nslookup teracota.matoug.com
```

The returned address must match the server's static public IP before requesting the TLS certificate.

## 4. Connect to Ubuntu

The standard Lightsail Ubuntu SSH username is `ubuntu`. You can use the Lightsail browser SSH terminal, or download the Lightsail SSH key and connect from Windows PowerShell:

```powershell
ssh -i "C:\path\to\lightsail-key.pem" ubuntu@YOUR_LIGHTSAIL_STATIC_IP
```

If OpenSSH rejects a key because its Windows permissions are too broad, use the Lightsail browser terminal or correct the key permissions before continuing.

## 5. Push the application to GitHub

The project is configured to use:

```text
https://github.com/matoug24/teracota.git
```

The deployment templates, `.env.example`, and this guide should be committed. The real `.env`, SQLite databases, private keys, virtual environments, logs, and backups are excluded by `.gitignore` and must never be pushed.

From PowerShell in the local TeraCota project folder:

```powershell
git status --short
git check-ignore -v .env teracota.sqlite3
```

The second command should show that `.env` and `teracota.sqlite3` are ignored. Review the staged files before pushing:

```powershell
git add .
git status --short
git commit -m "Add AWS production deployment"
git push origin main
```

Confirm that neither `.env` nor `teracota.sqlite3` appears in the GitHub repository. Keep `.env.example`; it contains names and placeholders only.

## 6. Install Ubuntu packages

Reconnect to the server, then run:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip nginx sqlite3 curl snapd
```

Enable the Ubuntu firewall only after allowing SSH:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
sudo ufw status
```

The Lightsail firewall and UFW both apply. Traffic must be permitted by both layers.

## 7. Create the service account and clone from GitHub

```bash
sudo adduser --system --group --home /var/lib/teracota --shell /usr/sbin/nologin teracota
sudo install -d -o teracota -g teracota -m 0750 /opt/teracota
sudo install -d -o teracota -g teracota -m 0750 /var/lib/teracota
sudo install -d -o teracota -g teracota -m 0750 /var/backups/teracota
```

For a public repository, clone over HTTPS:

```bash
sudo -u teracota git clone https://github.com/matoug24/teracota.git /opt/teracota
```

### Private repository option

For a private repository, use a read-only GitHub deploy key instead of putting a personal access token in a command or file.

Generate the key as the `teracota` service user:

```bash
sudo -u teracota install -d -m 0700 /var/lib/teracota/.ssh
sudo -u teracota ssh-keygen -t ed25519 \
  -C "teracota-lightsail-deploy" \
  -f /var/lib/teracota/.ssh/github_deploy \
  -N ""
sudo cat /var/lib/teracota/.ssh/github_deploy.pub
```

In GitHub, open the `matoug24/teracota` repository, then go to **Settings > Deploy keys > Add deploy key**. Paste the displayed public key and leave write access disabled.

Create an SSH configuration that always uses this deploy key:

```bash
sudo tee /var/lib/teracota/.ssh/config >/dev/null <<'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile /var/lib/teracota/.ssh/github_deploy
    IdentitiesOnly yes
EOF

sudo ssh-keyscan github.com \
  | sudo tee /var/lib/teracota/.ssh/known_hosts >/dev/null
sudo chown -R teracota:teracota /var/lib/teracota/.ssh
sudo chmod 0600 /var/lib/teracota/.ssh/config
sudo chmod 0600 /var/lib/teracota/.ssh/github_deploy
sudo chmod 0644 /var/lib/teracota/.ssh/github_deploy.pub
sudo chmod 0644 /var/lib/teracota/.ssh/known_hosts

sudo -u teracota ssh -T git@github.com || true
sudo -u teracota git clone git@github.com:matoug24/teracota.git /opt/teracota
```

Before relying on `ssh-keyscan`, compare the collected host key with GitHub's published SSH key fingerprints. GitHub normally reports that authentication succeeded but shell access is unavailable. That is expected.

After either clone method, protect the application files:

```bash
sudo chown -R teracota:teracota /opt/teracota
sudo find /opt/teracota -type d -exec chmod 0750 {} \;
sudo find /opt/teracota -type f -exec chmod 0640 {} \;
sudo chmod 0750 /opt/teracota/.git
```

### Optional: copy the current local database

The database is intentionally not stored in GitHub. To carry the current systems, issues, and visits to AWS, run this from local PowerShell:

```powershell
$KEY = "C:\path\to\lightsail-key.pem"
$SERVER_IP = "YOUR_LIGHTSAIL_STATIC_IP"
scp -i $KEY teracota.sqlite3 "ubuntu@${SERVER_IP}:/tmp/teracota.sqlite3"
```

Then install it from the Ubuntu terminal:

```bash
if [ -f /tmp/teracota.sqlite3 ]; then
  sudo install -o teracota -g teracota -m 0640 \
    /tmp/teracota.sqlite3 \
    /var/lib/teracota/teracota.sqlite3
  rm /tmp/teracota.sqlite3
fi
```

Skip these database commands for a fresh cloud database. Production has demo seeding disabled, so a new database starts without sample systems.

## 8. Create the Python environment

```bash
sudo -u teracota python3 -m venv /opt/teracota/.venv
sudo -u teracota /opt/teracota/.venv/bin/python -m pip install --upgrade pip
sudo -u teracota /opt/teracota/.venv/bin/pip install -r /opt/teracota/requirements.txt
```

The pinned versions in `requirements.txt` require Python 3.10 or newer. Ubuntu 24.04 satisfies that requirement.

## 9. Configure production secrets

Generate a random session secret:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Copy the environment template and protect it:

```bash
sudo install -o root -g teracota -m 0640 \
  /opt/teracota/.env.example \
  /opt/teracota/.env
sudo nano /opt/teracota/.env
```

Set every value below. Do not retain the example password or secret:

```text
TERACOTA_ENV=production
TERACOTA_SECRET_KEY=PASTE_THE_RANDOM_64_CHARACTER_VALUE
TERACOTA_USERNAME=teraview
TERACOTA_PASSWORD=USE_A_NEW_LONG_UNIQUE_PASSWORD
TERACOTA_DB_PATH=/var/lib/teracota/teracota.sqlite3
TERACOTA_BEHIND_PROXY=true
TERACOTA_COOKIE_SECURE=true
TERACOTA_SEED_DEMO=false
```

The application intentionally refuses to start in production when the old default password or development session secret is used.

The real `.env` exists only on the Lightsail server. Git ignores it, so `git pull` will not overwrite the password or secret.

To change the login password later, edit only the server file and restart the service:

```bash
sudo nano /opt/teracota/.env
sudo systemctl restart teracota
```

Update the `TERACOTA_PASSWORD` line. Do not put the real password in `.env.example`, `app.py`, the installation guide, or any Git commit.

## 10. Install and start the systemd service

```bash
sudo cp /opt/teracota/deploy/teracota.service /etc/systemd/system/teracota.service
sudo systemctl daemon-reload
sudo systemctl enable --now teracota
sudo systemctl status teracota --no-pager
```

Check the private Gunicorn health endpoint:

```bash
curl -fsS http://127.0.0.1:8000/healthz
```

Expected output:

```json
{"status":"ok"}
```

If startup fails, inspect the logs:

```bash
sudo journalctl -u teracota -n 100 --no-pager
```

## 11. Configure Nginx

Install the included Nginx configuration. It is already set to `teracota.matoug.com`:

```bash
sudo cp /opt/teracota/deploy/nginx-teracota.conf /etc/nginx/sites-available/teracota
sudo ln -sfn /etc/nginx/sites-available/teracota /etc/nginx/sites-enabled/teracota
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Test through Nginx:

```bash
curl -fsS -H "Host: teracota.matoug.com" http://127.0.0.1/healthz
```

At this point the public HTTP login page should load, but login cookies are configured for HTTPS and should be tested only after the next section.

## 12. Enable HTTPS with Certbot

Confirm that the domain resolves to this server, then install Certbot using its recommended snap package:

```bash
sudo apt-get remove -y certbot
sudo snap install core
sudo snap refresh core
sudo snap install --classic certbot
sudo ln -sfn /snap/bin/certbot /usr/local/bin/certbot
```

Request and install the certificate:

```bash
sudo certbot --nginx -d teracota.matoug.com
```

Choose the HTTPS redirect when prompted. Then test renewal:

```bash
sudo certbot renew --dry-run
```

Open the final site:

```text
https://teracota.matoug.com/
```

Log in using `TERACOTA_USERNAME` and `TERACOTA_PASSWORD` from the protected environment file. The unlinked `/logs` page shows recent visitor IP, device, browser, platform, and page information.

The admin page can export a portable CSV containing all application data except visitor logs. Its restore control replaces application data from that CSV while retaining the server's existing visitor logs. Keep exported CSV files protected because they contain operational records and deleted-item recovery data.

## 13. Verify the deployment

Run these checks on the server:

```bash
curl -fsS https://teracota.matoug.com/healthz
curl -I https://teracota.matoug.com/
curl -i https://teracota.matoug.com/api/state
sudo systemctl is-active teracota
sudo systemctl is-enabled teracota
sudo systemctl is-active nginx
sudo nginx -t
```

Then verify in a browser:

1. The address begins with `https://` and shows a valid certificate.
2. Opening `/`, a system URL, a location URL, `/admin`, or `/logs` while signed out redirects to `/login`.
3. The dashboard is not visible until the username and password are accepted.
4. The unauthenticated `/api/state` request returns HTTP `401` without operational data.
5. Login works and returns to the originally requested page.
6. A test visit can be created and deleted.
7. Logging out immediately returns to the login page.
8. Restarting the server does not remove the SQLite data.

## 14. Back up the SQLite database

SQLite's `.backup` command creates a consistent backup while the app is running:

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 \
  ".backup '/var/backups/teracota/teracota-${STAMP}.sqlite3'"
sudo ls -lh /var/backups/teracota/
```

Download important backups to another computer or an S3 bucket. A backup stored only on the same server will not help if that server's disk is lost.

From local PowerShell:

```powershell
scp -i $KEY "ubuntu@${SERVER_IP}:/var/backups/teracota/teracota-YYYYMMDD-HHMMSS.sqlite3" .
```

The `ubuntu` user cannot normally read the protected backup directory. Copy the selected backup to `/tmp` first:

```bash
sudo cp /var/backups/teracota/teracota-YYYYMMDD-HHMMSS.sqlite3 /tmp/
sudo chown ubuntu:ubuntu /tmp/teracota-YYYYMMDD-HHMMSS.sqlite3
```

### Restore a backup

```bash
sudo systemctl stop teracota
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 \
  ".restore '/var/backups/teracota/teracota-YYYYMMDD-HHMMSS.sqlite3'"
sudo systemctl start teracota
curl -fsS http://127.0.0.1:8000/healthz
```

## 15. Update the application later with Git

First push the tested changes from the local computer:

```powershell
git add .
git commit -m "Describe the TeraCota update"
git push origin main
```

For the first update that includes the deployment script, connect to Lightsail and run:

```bash
cd /opt/teracota
sudo -u teracota git pull --ff-only origin main
sudo bash /opt/teracota/deploy/update_lightsail.sh
```

The script installs a small command wrapper. Every later deployment is one command:

```bash
sudo update-teracota
```

The updater performs the following work automatically:

1. Refuses to continue if the server checkout contains local changes.
2. Creates a timestamped SQLite backup in `/var/backups/teracota`.
3. Pulls `origin/main` using fast-forward-only Git behavior.
4. Installs the pinned Python dependencies and checks the Python source.
5. Updates the systemd service and preserves Certbot's live HTTPS configuration.
6. Validates Nginx, restarts TeraCota, and waits for a successful health check.
7. Prints the old and new Git commits and the database backup path.

The ignored `/opt/teracota/.env`, virtual environment, and `/var/lib/teracota/teracota.sqlite3` remain unchanged during the pull. If the script reports local changes, investigate them before updating rather than discarding them.

To update from a branch other than `main` for a deliberate test deployment, override the branch for that run:

```bash
sudo TERACOTA_BRANCH=branch-name update-teracota
```

## 16. Operations and troubleshooting

Application status and logs:

```bash
sudo systemctl status teracota --no-pager
sudo journalctl -u teracota -f
```

Nginx status and logs:

```bash
sudo systemctl status nginx --no-pager
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

Confirm the private app port is not publicly bound:

```bash
sudo ss -lntp | grep -E ':80|:443|:8000'
```

Gunicorn must show `127.0.0.1:8000`, not `0.0.0.0:8000`.

Check database ownership:

```bash
sudo ls -l /var/lib/teracota/
sudo -u teracota sqlite3 /var/lib/teracota/teracota.sqlite3 'PRAGMA integrity_check;'
```

The integrity check should return `ok`.

### Common failures

**502 Bad Gateway**

```bash
sudo systemctl status teracota --no-pager
sudo journalctl -u teracota -n 100 --no-pager
curl -v http://127.0.0.1:8000/healthz
```

**Application refuses to start**

If the journal reports `TERACOTA_PASSWORD must be changed in production`, the
environment file is being read but the password is missing, misspelled, or still
set to `pythagorus`. Stop the restart loop and edit the protected server file:

```bash
sudo systemctl stop teracota
sudo nano /opt/teracota/.env
```

Make sure the file contains the exact variable names below, with no `export`
prefix and no spaces around `=`. Replace both example values with real secrets:

```text
TERACOTA_ENV=production
TERACOTA_SECRET_KEY=YOUR_RANDOM_64_CHARACTER_SECRET
TERACOTA_USERNAME=teraview
TERACOTA_PASSWORD="YOUR_NEW_LOGIN_PASSWORD"
TERACOTA_DB_PATH=/var/lib/teracota/teracota.sqlite3
TERACOTA_BEHIND_PROXY=true
TERACOTA_COOKIE_SECURE=true
TERACOTA_SEED_DEMO=false
```

Confirm the required entries exist without displaying their values:

```bash
sudo sed -n 's/^\(TERACOTA_[A-Z_]*\)=.*/\1=<set>/p' /opt/teracota/.env
```

Restore the expected ownership and permissions, then restart and verify the app:

```bash
sudo chown root:teracota /opt/teracota/.env
sudo chmod 0640 /opt/teracota/.env
sudo systemctl restart teracota
sudo systemctl status teracota --no-pager
curl -fsS http://127.0.0.1:8000/healthz
```

The health check should return `{"status":"ok"}`. If startup still fails, run
`sudo journalctl -u teracota -n 50 --no-pager` and resolve the newest error.

**Database is read-only**

```bash
sudo chown -R teracota:teracota /var/lib/teracota
sudo chmod 0750 /var/lib/teracota
sudo chmod 0640 /var/lib/teracota/teracota.sqlite3
sudo systemctl restart teracota
```

**Certificate request fails**

Confirm `teracota.matoug.com` resolves to the Lightsail Static IP and that TCP ports 80 and 443 are open in the Lightsail firewall and UFW.

## 17. Security notes

- Never commit `/opt/teracota/.env` or the SQLite database to source control.
- Use a unique password and store it in a password manager.
- Restrict SSH port 22 to your public IP when possible.
- Keep Ubuntu updated with `sudo apt update && sudo apt upgrade`.
- Keep HTTPS enabled because login uses a session cookie.
- Only `/login` and `/healthz` are intentionally public; all application pages, data APIs, and application assets require a session.
- Take regular off-server backups.
- The current app has one shared administrative login. For a larger user base, add individual accounts, roles, per-user audit identity, and stronger login protection before expanding access.

## Official references

- Lightsail static IP: https://docs.aws.amazon.com/lightsail/latest/userguide/lightsail-create-static-ip.html
- Lightsail firewall rules: https://docs.aws.amazon.com/lightsail/latest/userguide/understanding-firewall-and-port-mappings-in-amazon-lightsail.html
- Certbot with Nginx on Ubuntu: https://certbot.eff.org/instructions?ws=nginx&os=snap
- Gunicorn deployment documentation: https://docs.gunicorn.org/
- GitHub cloning documentation: https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository
- GitHub deploy keys for private repositories: https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys

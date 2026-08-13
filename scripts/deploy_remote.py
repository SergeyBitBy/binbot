import os
import time
import paramiko

HOST = os.environ.get("DEPLOY_HOST", "")
PORT = 22
USER = os.environ.get("DEPLOY_USER", "root")
PASS = os.environ.get("DEPLOY_PASSWORD")
REMOTE_DIR = os.environ.get("DEPLOY_REMOTE_DIR", "/root/binbot")
REPO_URL = "https://github.com/SergeyBitBy/binbot.git"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

ENV_CONTENT = f"""# Telegram Bot Configuration
BOT_TOKEN={BOT_TOKEN}

# Default Admin Security Credentials
INITIAL_ADMIN_USERNAME=sergebybitp2p
INITIAL_ALLOWED_CHAT_ID=930460307

# System Settings
TIMEZONE=Europe/Kyiv
LOG_LEVEL=INFO

# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///./data/bot.db

# Optional Google Sheets Integration
GOOGLE_SHEETS_ENABLED=false
GOOGLE_SERVICE_ACCOUNT_FILE=data/google_credentials.json
GOOGLE_SPREADSHEET_ID=
"""

SERVICE_CONTENT = """[Unit]
Description=Binance P2P Telegram Monitoring Bot Service
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/binbot
ExecStart=/root/binbot/.venv/bin/python run.py
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=35
EnvironmentFile=/root/binbot/.env

[Install]
WantedBy=multi-user.target
"""

def execute_remote():
    if not HOST or not BOT_TOKEN:
        raise RuntimeError("DEPLOY_HOST and BOT_TOKEN environment variables are required")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    key_path = os.path.expanduser("~/.ssh/id_ed25519")
    pkey = None
    if os.path.exists(key_path):
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
            print(f"Loaded SSH key from {key_path}")
        except Exception as e:
            print(f"Could not load SSH key: {e}")

    print(f"Connecting to {HOST}...")
    try:
        if pkey:
            try:
                ssh.connect(HOST, port=PORT, username=USER, pkey=pkey, timeout=15)
                print("Connected using SSH key!")
            except Exception as ke:
                print(f"Key auth failed ({ke}), trying password auth...")
                ssh.connect(HOST, port=PORT, username=USER, password=PASS, timeout=15, look_for_keys=False, allow_agent=False)
                print("Connected using Password!")
        else:
            ssh.connect(HOST, port=PORT, username=USER, password=PASS, timeout=15, look_for_keys=False, allow_agent=False)
            print("Connected using Password!")
    except Exception as e:
        print(f"Connection failed: {e}")
        raise e

    def run_cmd(cmd):
        print(f"\n>>> Running: {cmd}")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        if out:
            print(out)
        if err:
            print("STDERR:", err)
        print(f"Exit code: {exit_code}")
        if exit_code != 0 and "Already up to date" not in out and "already exists" not in err:
            raise Exception(f"Command failed with exit code {exit_code}: {cmd}")
        return out

    # 1. Install prerequisites on Ubuntu
    run_cmd("apt-get update -y && apt-get install -y python3 python3-venv python3-pip git")

    # 2. Setup project directory & git clone/pull
    run_cmd(f"mkdir -p {REMOTE_DIR}")
    
    # Check if git repository is initialized
    check_git = ssh.exec_command(f"test -d {REMOTE_DIR}/.git")[2].channel.recv_exit_status()
    if check_git == 0:
        print("Git repo exists. Pulling latest main...")
        run_cmd(f"cd {REMOTE_DIR} && git fetch origin && git reset --hard origin/main && git pull origin main")
    else:
        print("Cloning repository...")
        run_cmd(f"rm -rf {REMOTE_DIR} && git clone {REPO_URL} {REMOTE_DIR}")

    # 3. Create .venv and install dependencies
    run_cmd(f"cd {REMOTE_DIR} && python3 -m venv .venv")
    run_cmd(f"{REMOTE_DIR}/.venv/bin/python -m pip install --upgrade pip")
    run_cmd(f"{REMOTE_DIR}/.venv/bin/pip install -r {REMOTE_DIR}/requirements.txt")

    # 4. Write .env file directly on server
    print("Writing .env file...")
    sftp = ssh.open_sftp()
    with sftp.open(f"{REMOTE_DIR}/.env", "w") as f:
        f.write(ENV_CONTENT)
    sftp.close()

    # 5. Run Alembic migrations
    run_cmd(f"cd {REMOTE_DIR} && {REMOTE_DIR}/.venv/bin/alembic upgrade head")

    # 6. Install systemd service
    print("Writing systemd service file...")
    sftp = ssh.open_sftp()
    with sftp.open("/etc/systemd/system/binbot.service", "w") as f:
        f.write(SERVICE_CONTENT)
    sftp.close()

    # 7. Enable and start systemd service
    run_cmd("systemctl daemon-reload")
    run_cmd("systemctl enable binbot")
    run_cmd("systemctl restart binbot")
    
    time.sleep(3)
    status_out = run_cmd("systemctl status binbot")
    print("\n================ DEPLOYMENT COMPLETE ================")
    print(status_out)

    ssh.close()

if __name__ == "__main__":
    execute_remote()

#!/usr/bin/env bash
# 龙虾服务器部署脚本（Ubuntu/Debian/CentOS 通用）
# 用法: bash deploy.sh [服务器IP] [用户名]
set -euo pipefail

HOST="${1:?用法: bash deploy.sh <服务器IP> <用户名>}"
USER="${2:?用法: bash deploy.sh <服务器IP> <用户名>}"
REMOTE_DIR="/opt/stock-sentiment"
PORT="${PORT:-5000}"

echo "=== [1/6] 打包项目 ==="
cd "$(dirname "$0")"
rm -rf /tmp/stock-sentiment-pack
mkdir -p /tmp/stock-sentiment-pack
cp -r crawlers analysis templates static app.py requirements.txt /tmp/stock-sentiment-pack/ 2>/dev/null || true
# 排除缓存
rm -rf /tmp/stock-sentiment-pack/cache 2>/dev/null || true
# 生成 requirements.txt（若不存在）
if [ ! -f requirements.txt ]; then
  cat > requirements.txt <<'EOF'
flask>=3.0
requests>=2.31
beautifulsoup4>=4.12
lxml>=5.1
jieba>=0.42
selenium>=4.20
webdriver-manager>=4.0
EOF
  cp requirements.txt /tmp/stock-sentiment-pack/
fi

echo "=== [2/6] 推送代码到 ${USER}@${HOST} ==="
ssh "${USER}@${HOST}" "mkdir -p ${REMOTE_DIR}"
scp -r /tmp/stock-sentiment-pack/* "${USER}@${HOST}:${REMOTE_DIR}/"

echo "=== [3/6] 服务器环境准备（Python + 依赖 + Chrome）==="
ssh "${USER}@${HOST}" bash -s <<'REMOTE'
set -e
REMOTE_DIR="/opt/stock-sentiment"

# Python3 检查
if ! command -v python3 >/dev/null 2>&1; then
  echo "安装 Python3..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y && sudo apt-get install -y python3 python3-pip python3-venv
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 python3-pip
  fi
fi

# Chrome（雪球 WAF 必需，无头浏览器过验证）
if ! command -v google-chrome >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
  echo "安装 Google Chrome..."
  if command -v apt-get >/dev/null 2>&1; then
    wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
    sudo apt-get install -y ./tmp/chrome.deb 2>/dev/null || sudo dpkg -i /tmp/chrome.deb || sudo apt-get install -f -y
    rm -f /tmp/chrome.deb
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm
  fi
fi

# 虚拟环境 + 依赖
cd "${REMOTE_DIR}"
if [ ! -d venv ]; then
  python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

echo "环境就绪: $(./venv/bin/python3 --version)"
REMOTE

echo "=== [4/6] 配置 systemd 服务 ==="
ssh "${USER}@${HOST}" bash -s <<REMOTE
set -e
REMOTE_DIR="/opt/stock-sentiment"
PORT="${PORT}"
SERVICE="stock-sentiment"
sudo tee /etc/systemd/system/\${SERVICE}.service > /dev/null <<EOF2
[Unit]
Description=Stock Sentiment Analysis Web
After=network.target

[Service]
Type=simple
WorkingDirectory=\${REMOTE_DIR}
ExecStart=\${REMOTE_DIR}/venv/bin/python3 \${REMOTE_DIR}/app.py
Restart=always
RestartSec=5
Environment=PORT=\${PORT}

[Install]
WantedBy=multi-user.target
EOF2
sudo systemctl daemon-reload
sudo systemctl enable \${SERVICE}
sudo systemctl restart \${SERVICE}
sleep 2
sudo systemctl status \${SERVICE} --no-pager | head -8
REMOTE

echo "=== [5/6] 防火墙放行端口 ${PORT} ==="
ssh "${USER}@${HOST}" bash -s <<REMOTE
PORT="${PORT}"
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow \${PORT}/tcp 2>/dev/null || true
fi
if command -v firewall-cmd >/dev/null 2>&1; then
  sudo firewall-cmd --permanent --add-port=\${PORT}/tcp 2>/dev/null || true
  sudo firewall-cmd --reload 2>/dev/null || true
fi
REMOTE

echo "=== [6/6] 验证 ==="
sleep 2
curl -s "http://${HOST}:${PORT}/api/health" || echo "首次启动较慢（雪球初始化），稍后重试"
echo ""
echo "✅ 部署完成！浏览器访问: http://${HOST}:${PORT}"
echo "  查看日志: ssh ${USER}@${HOST} 'sudo journalctl -u stock-sentiment -f'"

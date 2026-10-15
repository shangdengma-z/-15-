#!/usr/bin/env bash
# 上线前的一次性准备（Ubuntu 22.04 / Debian 12）。可重复执行，不删除任何已有内容。
#
#   bash deploy/server-setup.sh your.domain.com
#
# 它只做三件事：加 swap、建目录、申请证书。
# 防火墙与 Docker 安装故意只打印命令不执行——远程 SSH 下替你 ufw enable 有把你锁在门外的风险。
set -euo pipefail

DOMAIN="${1:?用法: bash deploy/server-setup.sh your.domain.com}"
cd "$(dirname "$0")/.."

# 1) 2G 内存的机器没有 swap 时，MySQL + uvicorn 同时起来很容易被 OOM killer 挑走一个
if [ "$(free -m | awk '/^Swap:/{print $2}')" -lt 512 ] && [ ! -f /swapfile ]; then
  echo ">> 创建 2G swapfile（需要 root）"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# 2) 数据与证书目录：容器以 root 写入这些路径，先建好避免 compose 起来时报 permission denied
mkdir -p data/cache data/db deploy/certs deploy/certbot-www

# 3) 证书（http-01 校验走 proxy 容器里那条 /.well-known/ 规则，所以 compose 必须先起来）
echo ">> 申请证书：确认 ${DOMAIN} 的 A 记录已指向本机公网 IP"
sudo certbot certonly --webroot -w deploy/certbot-www -d "${DOMAIN}" \
  --non-interactive --agree-tos -m "admin@${DOMAIN}" || \
  echo "!! 证书申请失败：多半是 DNS 未生效或 80 端口不通，改好后重跑本脚本"

# 4) 剩下的手工做（脚本不替你动，避免远程锁死自己）
cat <<EOF

接下来请自己确认这几项：

  # 防火墙：先放行 SSH，再开 80/443
  sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw status verbose

  # 域名写进 nginx.conf（两处 server_name + 证书路径里的目录名）
  sed -i 's/circle.example.com/${DOMAIN}/g' deploy/nginx.conf

  # 根目录 .env：AUTH_ENABLED=1、DB_BACKEND=mysql、DB_*(含 DB_ROOT_PASSWORD)、SECRET_KEY、MASTER_KEY、VITE_BMAP_AK
  #   生成随机串：python3 -c "import secrets;print(secrets.token_urlsafe(32))"
  #   上线实例里不要放 BMAP_SERVER_AK / LLM_API_KEY —— 每个用户填自己的，默认只存在他浏览器里

  # 百度开放控制台（两件不做就等于把 AK 送给别人刷）：
  #   服务端 AK -> IP 白名单填本机公网 IPv4
  #   浏览器端 AK -> Referer 白名单填 https://${DOMAIN}/*

  docker compose --project-directory . -f deploy/docker-compose.prod.yml up -d --build
  curl -i https://${DOMAIN}/api/health
EOF

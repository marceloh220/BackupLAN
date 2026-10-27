#!/usr/bin/env bash
# Configures a NAS (Network Attached Storage) with RAID, encryption, Samba, DNS/DHCP, and a firewall.
# Usage: sudo ./nas-setup.sh
set -euo pipefail

###############################################################################
# CONFIGURATION
###############################################################################

# --- Network ---
LAN_INTERFACE=""                   # NAS LAN interface; empty = detected automatically
IP_INET="192.168.0.1"              # IP address of the NAS network interface
IP_GATEWAY="192.168.0.254"         # Internet gateway/router for the NAS network interface
LAN_NETMASK="255.255.255.0"        # NAS local network mask
LAN_CIDR=""                        # Local network in CIDR format; empty = calculated from LAN_NETMASK
IP_RANGE_START="192.168.0.100"     # DHCP range start
IP_RANGE_END="192.168.0.200"       # DHCP range end
DNS_UPSTREAM=("1.1.1.1" "9.9.9.9") # External resolvers (Cloudflare and Quad9)
LOCAL_DOMAIN="lan"                 # Local domain used for network and Samba configuration

# --- Storage ---
APP_STORAGE="/srv/app"
NAS_STORAGE="/srv/nas"

# --- Primary NAS user ---
USER_NAME="meneceu"                 # Primary NAS user
USER_PASSWORD=""                    # Primary user password; empty = prompt in the terminal
USER_HOME="$NAS_STORAGE/$USER_NAME" # Primary NAS user's home directory (the Samba share capitalizes the first letter)

# --- System ---
SBIN_DIR="/sbin"                    # Administrative executable directory

# --- RAID ---
RAID_DEVICES=(/dev/sdb /dev/sdc)   # Disks/partitions that will be part of the RAID
RAID_LEVEL=1                       # 0 (for performance), 1 (mirroring), 5 (striping with parity), 6 (dual parity), 10 (mirrored striping)
RAID_DEVICE="/dev/md0"             # dispositivo RAID a ser criado

# --- Unencrypted filesystem ---
# Used only when CRYPT_DEVICE is empty.
RAID_MOUNTPOINT="$NAS_STORAGE"

# --- RAID volume filesystem (or filesystem inside LUKS) ---
# Options: ext4, xfs, or btrfs.
RAID_FILESYSTEM="ext4"

# --- Encryption (LUKS) ---
# Leave CRYPT_DEVICE empty to disable encryption.
CRYPT_DEVICE="/dev/md0"            # e.g. "/dev/md0" (or another partition/disk)
CRYPT_MOUNTPOINT="$NAS_STORAGE"        # Mount point for the decrypted volume
CRYPT_KEYFILE="/etc/nas.key"       # e.g. "/root/nas.key". Empty = do not create a keyfile (prompt for password)
CRYPT_AUTO_UNLOCK="yes"            # yes = decrypt and mount at boot; no = manual
CRYPT_NAME="nas_crypt"             # Mapping name under /dev/mapper

# --- Samba ---
# Format: "share_name:/path/to/directory"
SHARES=(
  "Publico:$NAS_STORAGE/publico"
  "Privado:$NAS_STORAGE/privado"
  "Backup:$NAS_STORAGE/backup"
  "Midia:$NAS_STORAGE/midia"
  "${USER_NAME^}:$USER_HOME"
  "Monitor:$APP_STORAGE/monitor"
  "Anytype:$APP_STORAGE/anytype"
)
PUBLIC_SHARES=("Publico")                          # Share names accessible without a password (guest)
GROUP_SHARES=("Backup" "Midia")                    # Shares accessible to the SAMBA_GROUP group
READONLY_SHARES=("Monitor")                        # Visible, read-only shares (written locally by marcelinoServer)
USER_SHARES=("${USER_NAME^}:$USER_NAME" 
             "Privado:$USER_NAME" 
             "Anytype:$USER_NAME")                 # share:user pairs accessible only to that user
SAMBA_USER="$USER_NAME"                            # Samba user (created if it does not exist)
SAMBA_GROUP="nasgroup"                             # Samba group (created if it does not exist)
WORKGROUP="WORKGROUP"                              # Samba workgroup
DOMAIN="$LOCAL_DOMAIN"                             # Local network domain (for Samba)

# --- Static IP ---
NET_STATIC="yes"                   # yes = configure a static IP on Debian
NAS_IP="$IP_INET"                  # NAS IP address (also used for DNS/DHCP)
NAS_PREFIX=24                      # Network prefix length (24 = 255.255.255.0)
NET_IFACE=""                       # Network interface. Empty = detect from the default route
NET_GATEWAY="$IP_GATEWAY"          # Internet gateway/router. Empty = use the current gateway

# --- DNS (dnsmasq) with ad, tracker, and malware filtering ---
DNS_ENABLE="yes"
# Effective resolvers: DNS_UPSTREAM; if empty, use Cloudflare and Quad9
DNS_UPSTREAM_int=("1.1.1.1" "9.9.9.9")
[[ -z "${DNS_UPSTREAM[*]:-}" ]] || DNS_UPSTREAM_int=("${DNS_UPSTREAM[@]}")
DNS_DOMAIN="$LOCAL_DOMAIN"         # Local domain provided to clients
# Lists in hosts or dnsmasq format (local=/domain/)
DNS_BLOCKLISTS=(
  "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"                    # Ads + malware
  "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/dnsmasq/pro.txt"        # Ads, trackers, spyware
  "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/dnsmasq/tif.medium.txt" # Threats (malware, phishing)
)

# --- DHCP (dnsmasq) ---
DHCP_ENABLE="yes"
DHCP_RANGE_START="$IP_RANGE_START"
DHCP_RANGE_END="$IP_RANGE_END"
DHCP_LEASE="12h"

# --- SSH (Secure Shell) ---
SSH_PORT=22                        # NAS server SSH port
SSH_MAX_SESSION=600                # Idle seconds before closing the SSH session (10 min)
SSH_ALLOW_ROOT="no"                # yes = allow SSH login as root; no = regular users only
SSH_PASSWORD_AUTH="yes"            # yes = allow password authentication; no = SSH keys only
SSH_BANNER="yes"                   # Optional message displayed at SSH login

# --- NTP (local network time server) ---
NTP_ENABLE="yes"                              # yes = install and configure chrony as the LAN NTP server
NTP_UPSTREAM=("pool.ntp.br" "a.st1.ntp.br")   # External time sources

# --- Internet speed test ---
SPEEDTEST_ENABLE="yes"                                  # yes = install marcelinoSpeedtest and its timers
SPEEDTEST_LOG="/var/log/marcelinoSpeedtest.jsonl"             # Test log
SPEEDTEST_OUT="$USER_HOME/internet_test"        # Charts saved in year/month subdirectories here
SPEEDTEST_GRAPH_TIME="23:55"                             # Daily chart time (last 24 hours)

# --- Web dashboard (marcelinoWeb) ---
WEB_ENABLE="yes"                                         # yes = install the dashboard with login
WEB_PORT=443                                             # HTTP port (LAN access only)
WEB_STATUS="$APP_STORAGE/monitor/.marcelinoSrv.json"         # JSON published by marcelinoServer
WEB_USER="marcelinoweb"                                  # System user that runs the service
WEB_ADMIN="$USER_NAME"                                   # Initial web user (uses USER_PASSWORD; empty = add later with marcelinoWeb useradd)
WEB_TLS="yes"                                            # yes = generate a self-signed certificate and use HTTPS

# --- Anytype (local sync host) ---
ANYTYPE_ENABLE="yes"
ANYTYPE_IMAGE="ghcr.io/grishy/any-sync-bundle:1.7.0-2026-10-12"
ANYTYPE_DATA="$APP_STORAGE/anytype"
ANYTYPE_BACKUP="$NAS_STORAGE/backup/anytype"
ANYTYPE_HOST="$NAS_IP"

# --- Syncthing ---
SYNCTHING_ENABLE="yes"
SYNCTHING_HOME="$APP_STORAGE/syncthing"    # Configuration, identity, and index database

# --- DLNA (media server) ---
DLNA_ENABLE="yes"
DLNA_PORT=8200
DLNA_MEDIA_DIR="$NAS_STORAGE/midia"
DLNA_MEDIA_MUSIC="$DLNA_MEDIA_DIR/Music"
DLNA_MEDIA_VIDEO="$DLNA_MEDIA_DIR/Video"
DLNA_MEDIA_PICTURES="$DLNA_MEDIA_DIR/Pictures"
DLNA_CACHE="$APP_STORAGE/dlna/.dlna.cache"
DLNA_DB="$APP_STORAGE/dlna/.dlna.db"
DLNA_LOG="/var/log/minidlna/minidlna.log"

###############################################################################
# FUNCTIONS
###############################################################################

log()  { echo -e "\e[1;32m[+]\e[0m $*"; }
warn() { echo -e "\e[1;33m[!]\e[0m $*"; }
die()  { echo -e "\e[1;31m[x]\e[0m $*" >&2; exit 1; }

validate_raid_filesystem() {
  case "$RAID_FILESYSTEM" in
    ext4|xfs|btrfs) ;;
    *) die "RAID_FILESYSTEM inválido: $RAID_FILESYSTEM. Use ext4, xfs ou btrfs." ;;
  esac
}

detect_iface() {
  [[ -n "$NET_IFACE" ]] || NET_IFACE="$LAN_INTERFACE"
  [[ -n "$NET_IFACE" ]] || NET_IFACE=$(ip -4 route show default | awk '{print $5; exit}')
  [[ -n "$NET_IFACE" ]] || die "Interface padrão não encontrada; defina NET_IFACE."
}

detect_lan_cidr() {
  if [[ -n "$LAN_CIDR" ]]; then
    printf '%s\n' "$LAN_CIDR"
    return
  fi
  detect_iface
  if [[ "$NET_STATIC" == "yes" ]]; then
    python3 -c "import sys,ipaddress; print(ipaddress.ip_interface(sys.argv[1]).network)" "$IP_INET/$LAN_NETMASK"
    return
  fi
  ip -4 -o addr show dev "$NET_IFACE" scope global | awk '{print $4; exit}' |
    python3 -c "import sys,ipaddress; print(ipaddress.ip_interface(sys.stdin.read().strip()).network)"
}

APT_UPDATED="no"
FIREWALL_READY="no"

ensure_packages() {
  export DEBIAN_FRONTEND=noninteractive
  if [[ "$APT_UPDATED" != "yes" ]]; then
    apt-get update -qq
    APT_UPDATED="yes"
  fi
  apt-get install -y "$@"
}

purge_packages() {
  local package
  local -a installed=()
  for package in "$@"; do
    if dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
      installed+=("$package")
    fi
  done
  if [[ ${#installed[@]} -gt 0 ]]; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y "${installed[@]}"
  fi
}

ensure_firewall() {
  [[ "$FIREWALL_READY" != "yes" ]] || return 0
  ensure_packages ufw
  "$SBIN_DIR/ufw" default deny incoming
  "$SBIN_DIR/ufw" default allow outgoing
  if systemctl is-active --quiet ssh \
    || systemctl is-active --quiet sshd; then
    "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port "$SSH_PORT" proto tcp comment 'NAS SSH'
  fi
  FIREWALL_READY="yes"
}

firewall_allow() {
  local protocol=$1 port=$2 comment=$3
  "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port "$port" proto "$protocol" comment "$comment"
  "$SBIN_DIR/ufw" --force enable
}

firewall_delete() {
  local protocol=$1 port=$2
  command -v ufw >/dev/null 2>&1 || return 0
  "$SBIN_DIR/ufw" --force delete allow from "$LAN_CIDR" to any port "$port" proto "$protocol" >/dev/null 2>&1 || true
}

firewall_allow_dhcp() {
  detect_iface
  "$SBIN_DIR/ufw" allow in on "$NET_IFACE" to any port 67 proto udp comment 'NAS DHCP'
  "$SBIN_DIR/ufw" --force enable
}

firewall_delete_dhcp() {
  command -v ufw >/dev/null 2>&1 || return 0
  detect_iface
  "$SBIN_DIR/ufw" --force delete allow in on "$NET_IFACE" to any port 67 proto udp >/dev/null 2>&1 || true
}

show_help() {
  cat <<EOF
Uso: sudo $0 [opção ...]

Opções gerais:
  --all              Configura o NAS completo, incluindo RAID e armazenamento
  --raid             Cria e configura o RAID
  --storage          Configura sistema de arquivos e criptografia/montagem
  --user             Cria o usuário principal (USER_NAME) no sistema e no Samba
  --network          Configura o IP estático (NAS_IP)

Serviços:
  --samba / --uninstall-samba
  --discovery / --uninstall-discovery
  --ntp / --uninstall-ntp
  --dns / --uninstall-dns       DNS e DHCP (dnsmasq)
  --speedtest / --uninstall-speedtest
  --marcelino-server / --uninstall-marcelino-server
  --web / --uninstall-web
  --anytype / --uninstall-anytype
  --syncthing / --uninstall-syncthing
  --ssh / --uninstall-ssh
  --dlna / --uninstall-dlna
  -h, --help                    Mostra esta ajuda

Cada serviço instala suas dependências e configura suas regras do firewall.
As desinstalações preservam dados e não alteram RAID, discos, LUKS ou montagem.
É possível combinar opções; --all não pode ser combinado com desinstalações.
EOF
}

detect_wsdd_package() {
  local package
  for package in wsdd-server wsdd wsdd2; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      WSDD_PKG="$package"
      return 0
    fi
  done
  WSDD_PKG=""
}

ensure_user_password() {
  if [[ -z "$USER_PASSWORD" ]]; then
    read -rsp "Senha para $USER_NAME: " USER_PASSWORD </dev/tty \
      || die "Não foi possível ler a senha no terminal. Defina USER_PASSWORD para execução não interativa."
    printf '\n' >&2
  fi
  [[ -n "$USER_PASSWORD" && "$USER_PASSWORD" != "sua_senha_aqui" ]] \
    || die "Defina USER_PASSWORD no topo do script ou deixe vazio para solicitar no terminal."
  [[ ${#USER_PASSWORD} -ge 8 ]] || die "USER_PASSWORD deve ter ao menos 8 caracteres."
}

setup_user() {
  [[ -z "${USER_READY:-}" ]] || return 0
  ensure_packages samba
  [[ -n "$USER_NAME" ]] || die "USER_NAME vazio."
  [[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "USER_NAME inválido: $USER_NAME."
  local create_user="no" create_samba_user="no" samba_users user_groups
  id "$USER_NAME" >/dev/null 2>&1 || create_user="yes"
  samba_users=$(pdbedit -L) || die "Não foi possível consultar os usuários Samba."
  if ! grep -q "^$USER_NAME:" <<< "$samba_users"; then
    create_samba_user="yes"
  fi
  if [[ "$create_user" == "yes" || "$create_samba_user" == "yes" ]]; then
    ensure_user_password
  fi
  log "Configurando usuário $USER_NAME..."

  getent group "$SAMBA_GROUP" >/dev/null || "$SBIN_DIR/groupadd" "$SAMBA_GROUP"
  if [[ "$create_user" == "yes" ]]; then
    "$SBIN_DIR/useradd" -M -d "$USER_HOME" -s /bin/bash -G "$SAMBA_GROUP" "$USER_NAME"
  else
    log "Usuário Linux $USER_NAME já existe; preservando home e senha."
    user_groups=$(id -nG "$USER_NAME")
    if [[ " $user_groups " != *" $SAMBA_GROUP "* ]]; then
      gpasswd -a "$USER_NAME" "$SAMBA_GROUP"
    fi
  fi
  mkdir -p "$USER_HOME"
  chown "$USER_NAME":"$(id -gn "$USER_NAME")" "$USER_HOME"
  if [[ "$create_user" == "yes" ]]; then
    printf '%s:%s\n' "$USER_NAME" "$USER_PASSWORD" | "$SBIN_DIR/chpasswd"
  fi
  if [[ "$create_samba_user" == "yes" ]]; then
    printf '%s\n%s\n' "$USER_PASSWORD" "$USER_PASSWORD" | smbpasswd -s -a "$USER_NAME" >/dev/null
  else
    log "Usuário Samba $USER_NAME já existe; preservando cadastro e senha."
  fi
  USER_READY=1
}

storage_is_configured() {
  local storage_mountpoint="$RAID_MOUNTPOINT"
  [[ -z "$CRYPT_DEVICE" ]] || storage_mountpoint="$CRYPT_MOUNTPOINT"
  mountpoint -q "$storage_mountpoint" \
    || findmnt --fstab --noheadings --mountpoint "$storage_mountpoint" >/dev/null 2>&1
}

setup_raid() {
  if storage_is_configured; then
    warn "Armazenamento já montado ou registrado no fstab; pulando configuração do RAID."
    return
  fi
  if [[ -e "$RAID_DEVICE" ]]; then
    warn "$RAID_DEVICE já existe; pulando criação do RAID."
    return
  fi
  ensure_packages mdadm
  [[ ${#RAID_DEVICES[@]} -ge 1 ]] || die "RAID_DEVICES vazio."
  warn "Todos os dados serão APAGADOS em: ${RAID_DEVICES[*]}"
  read -rp "Digite 'SIM' para continuar: " ans
  [[ "$ans" == "SIM" ]] || die "Cancelado."

  for d in "${RAID_DEVICES[@]}"; do
    [[ -b "$d" ]] || die "$d não é um dispositivo de bloco."
    "$SBIN_DIR/wipefs" -a "$d"
  done

  log "Criando RAID $RAID_LEVEL em $RAID_DEVICE..."
  "$SBIN_DIR/mdadm" --create "$RAID_DEVICE" --run --level="$RAID_LEVEL" \
        --raid-devices="${#RAID_DEVICES[@]}" "${RAID_DEVICES[@]}"

  "$SBIN_DIR/mdadm" --detail --scan > /etc/mdadm/mdadm.conf.new
  grep -v '^ARRAY' /etc/mdadm/mdadm.conf > /etc/mdadm/mdadm.conf.base 2>/dev/null || true
  cat /etc/mdadm/mdadm.conf.base /etc/mdadm/mdadm.conf.new > /etc/mdadm/mdadm.conf
  rm -f /etc/mdadm/mdadm.conf.base /etc/mdadm/mdadm.conf.new
  "$SBIN_DIR/update-initramfs" -u
}

add_fstab() { # uuid mountpoint filesystem options
  sed -i "\|[[:space:]]$2[[:space:]]|d" /etc/fstab
  echo "UUID=$1 $2 $3 $4 0 2" >> /etc/fstab
}

format_raid_filesystem() {
  local device=$1 existing_filesystem
  existing_filesystem=$("$SBIN_DIR/blkid" -s TYPE -o value "$device" 2>/dev/null || true)
  if [[ -n "$existing_filesystem" ]]; then
    [[ "$existing_filesystem" == "$RAID_FILESYSTEM" ]] || die \
      "$device já contém $existing_filesystem; RAID_FILESYSTEM está definido como $RAID_FILESYSTEM."
    return 0
  fi

  case "$RAID_FILESYSTEM" in
    ext4)  "$SBIN_DIR/mkfs.ext4" -F "$device" ;;
    xfs)   "$SBIN_DIR/mkfs.xfs" -f "$device" ;;
    btrfs) "$SBIN_DIR/mkfs.btrfs" -f "$device" ;;
  esac
}

setup_storage() {
  if storage_is_configured; then
    warn "Armazenamento já montado ou registrado no fstab; pulando configuração de storage."
    return
  fi
  ensure_packages e2fsprogs util-linux
  case "$RAID_FILESYSTEM" in
    xfs) ensure_packages xfsprogs ;;
    btrfs) ensure_packages btrfs-progs ;;
  esac
  if [[ -n "$CRYPT_DEVICE" ]]; then
    ensure_packages cryptsetup
    ensure_packages systemd-cryptsetup || true
  fi
  if [[ -z "$CRYPT_DEVICE" ]]; then
    log "Criptografia desativada; preparando $RAID_DEVICE em $RAID_FILESYSTEM."
    format_raid_filesystem "$RAID_DEVICE"
    mkdir -p "$RAID_MOUNTPOINT"
    add_fstab "$("$SBIN_DIR/blkid" -s UUID -o value "$RAID_DEVICE")" \
      "$RAID_MOUNTPOINT" "$RAID_FILESYSTEM" "defaults,nofail"
    systemctl daemon-reload
    mount "$RAID_MOUNTPOINT"
    return
  fi

  [[ -b "$CRYPT_DEVICE" ]] || die "$CRYPT_DEVICE não é um dispositivo de bloco."
  local keyopt=() crypttab_key="none" crypttab_opts="luks"

  if [[ -n "$CRYPT_KEYFILE" ]]; then
    if [[ ! -f "$CRYPT_KEYFILE" ]]; then
      log "Gerando keyfile em $CRYPT_KEYFILE"
      mkdir -p "$(dirname "$CRYPT_KEYFILE")"
      (umask 077; dd if=/dev/urandom of="$CRYPT_KEYFILE" bs=512 count=8 status=none)
    fi
    chmod 400 "$CRYPT_KEYFILE"
    keyopt=(--key-file "$CRYPT_KEYFILE")
    crypttab_key="$CRYPT_KEYFILE"
  fi

  if ! "$SBIN_DIR/cryptsetup" isLuks "$CRYPT_DEVICE" 2>/dev/null; then
    warn "$CRYPT_DEVICE será formatado com LUKS (dados serão perdidos)."
    read -rp "Digite 'SIM' para continuar: " ans
    [[ "$ans" == "SIM" ]] || die "Cancelado."
    [[ ${#keyopt[@]} -eq 0 ]] && log "Defina a senha de criptografia quando solicitado."
    "$SBIN_DIR/cryptsetup" luksFormat --batch-mode "${keyopt[@]}" "$CRYPT_DEVICE" \
      || die "Falha no luksFormat."
  fi

  [[ -e "/dev/mapper/$CRYPT_NAME" ]] || "$SBIN_DIR/cryptsetup" open "${keyopt[@]}" "$CRYPT_DEVICE" "$CRYPT_NAME"
  format_raid_filesystem "/dev/mapper/$CRYPT_NAME"
  mkdir -p "$CRYPT_MOUNTPOINT"

  local luks_uuid fs_uuid
  luks_uuid=$("$SBIN_DIR/cryptsetup" luksUUID "$CRYPT_DEVICE")
  fs_uuid=$("$SBIN_DIR/blkid" -s UUID -o value "/dev/mapper/$CRYPT_NAME")

  sed -i "/^${CRYPT_NAME}[[:space:]]/d" /etc/crypttab 2>/dev/null || true
  if [[ "$CRYPT_AUTO_UNLOCK" == "yes" ]]; then
    # With a keyfile it opens automatically; without one ("none"), prompt for the password at boot.
    echo "$CRYPT_NAME UUID=$luks_uuid $crypttab_key $crypttab_opts,nofail" >> /etc/crypttab
    add_fstab "$fs_uuid" "$CRYPT_MOUNTPOINT" "$RAID_FILESYSTEM" \
      "defaults,nofail,x-systemd.requires=systemd-cryptsetup@$CRYPT_NAME.service"
  else
    echo "$CRYPT_NAME UUID=$luks_uuid $crypttab_key $crypttab_opts,noauto" >> /etc/crypttab
    add_fstab "$fs_uuid" "$CRYPT_MOUNTPOINT" "$RAID_FILESYSTEM" "defaults,noauto,nofail"
    warn "Modo manual: $SBIN_DIR/cryptsetup open ${keyopt[*]:-} $CRYPT_DEVICE $CRYPT_NAME && mount $CRYPT_MOUNTPOINT"
  fi
  [[ -n "$CRYPT_KEYFILE" ]] && warn "A keyfile fica no disco do sistema; faça backup dela em local seguro."
  systemctl daemon-reload
  mountpoint -q "$CRYPT_MOUNTPOINT" || mount "$CRYPT_MOUNTPOINT"
  "$SBIN_DIR/update-initramfs" -u
}

setup_samba() {
  ensure_packages samba
  log "Configurando Samba..."
  getent group "$SAMBA_GROUP" >/dev/null || "$SBIN_DIR/groupadd" "$SAMBA_GROUP"
  setup_user

  cp -n /etc/samba/smb.conf /etc/samba/smb.conf.orig || true
  cat > /etc/samba/smb.conf <<EOF
[global]
   workgroup = $WORKGROUP
   realm = $DOMAIN
   server string = NAS %h
   server role = standalone server
   map to guest = Bad User
   server min protocol = SMB2
   restrict anonymous = 2
   client signing = mandatory
   hosts allow = $LAN_CIDR 127.0.0.1
   hosts deny = 0.0.0.0/0
   disable netbios = no
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   log file = /var/log/samba/log.%m
   max log size = 1000
EOF

  local entry name path category_share category_count private_entry private_share private_user
  for entry in "${SHARES[@]}"; do
    name="${entry%%:*}"; path="${entry#*:}"
    mkdir -p "$path"
    category_count=0
    private_user=""
    for category_share in "${PUBLIC_SHARES[@]}" "${GROUP_SHARES[@]}" "${READONLY_SHARES[@]}"; do
      [[ "$category_share" == "$name" ]] && ((category_count += 1))
    done
    for private_entry in "${USER_SHARES[@]}"; do
      private_share="${private_entry%%:*}"
      if [[ "$private_share" == "$name" ]]; then
        private_user="${private_entry#*:}"
        ((category_count += 1))
      fi
    done
    [[ $category_count -eq 1 ]] || die "O share $name deve pertencer a exatamente uma categoria."

    if [[ " ${PUBLIC_SHARES[*]:-} " == *" $name "* ]]; then
      chown -R nobody:"$SAMBA_GROUP" "$path"
      chmod -R u=rwX,g=rwX,o=rwX "$path"
      chmod 2777 "$path"
      cat >> /etc/samba/smb.conf <<EOF

[$name]
   path = $path
   browseable = yes
   read only = no
   guest ok = yes
   valid users =
   invalid users =
   read list =
   write list =
   admin users =
   force user = nobody
   force group = $SAMBA_GROUP
   create mask = 0666
   directory mask = 2777
EOF
      continue
    fi
    if [[ " ${READONLY_SHARES[*]:-} " == *" $name "* ]]; then
      chown root:root "$path"
      chmod 0755 "$path"
      cat >> /etc/samba/smb.conf <<EOF

[$name]
   path = $path
   browseable = no
   read only = yes
   guest ok = yes
   write list =
   admin users =
EOF
      continue
    fi
    if [[ -n "$private_user" ]]; then
      id "$private_user" >/dev/null 2>&1 || "$SBIN_DIR/useradd" -M -s "$SBIN_DIR/nologin" -g "$SAMBA_GROUP" "$private_user"
      if ! pdbedit -L 2>/dev/null | grep -q "^$private_user:"; then
        log "Defina a senha Samba do usuário $private_user:"
        smbpasswd -a "$private_user"
      fi
      chown -R "$private_user":"$(id -gn "$private_user")" "$path"
      chmod -R u=rwX,go= "$path"
      chmod 0700 "$path"
      cat >> /etc/samba/smb.conf <<EOF

[$name]
   path = $path
   browseable = yes
   read only = no
   guest ok = no
   valid users = $private_user
   invalid users =
   read list =
   write list =
   admin users =
   force user = $private_user
   create mask = 0600
   directory mask = 0700
EOF
      continue
    fi
    [[ " ${GROUP_SHARES[*]:-} " == *" $name "* ]] || die "Categoria inválida para o share $name."
    chgrp -R "$SAMBA_GROUP" "$path"
    chmod -R u=rwX,g=rwX,o= "$path"
    chmod 2770 "$path"
    cat >> /etc/samba/smb.conf <<EOF

[$name]
   path = $path
   browseable = yes
   read only = no
   guest ok = no
   valid users = @$SAMBA_GROUP
   invalid users =
   read list =
   write list =
   admin users =
   force group = $SAMBA_GROUP
   create mask = 0660
   directory mask = 2770
EOF
  done

  testparm -s >/dev/null
  systemctl enable --now smbd nmbd
  systemctl restart smbd nmbd
  firewall_allow tcp 139 'NAS Samba NetBIOS'
  firewall_allow tcp 445 'NAS Samba SMB'
  firewall_allow udp 137 'NAS Samba NetBIOS'
  firewall_allow udp 138 'NAS Samba NetBIOS'
}

setup_discovery() {
  ensure_packages avahi-daemon
  local package
  WSDD_PKG=""
  for package in wsdd-server wsdd wsdd2; do
    if ensure_packages "$package"; then
      WSDD_PKG="$package"
      break
    fi
    warn "Pacote $package indisponível; tentando o próximo."
  done
  [[ -n "$WSDD_PKG" ]] || warn "Nenhum pacote wsdd encontrado; Windows pode não listar o servidor automaticamente."
  log "Ativando descoberta (mDNS/Avahi e WS-Discovery)..."
  cat > /etc/avahi/services/smb.service <<'EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">%h</name>
  <service>
    <type>_smb._tcp</type>
    <port>445</port>
  </service>
</service-group>
EOF
  systemctl enable --now avahi-daemon
  systemctl restart avahi-daemon

  if [[ -n "${WSDD_PKG:-}" ]]; then
    local svc
    case "$WSDD_PKG" in
      wsdd-server) svc=wsdd-server ;;
      *)           svc="$WSDD_PKG" ;;
    esac
    systemctl enable --now "$svc" || warn "Não foi possível iniciar $svc."
  fi
  firewall_allow udp 5353 'NAS mDNS'
  firewall_allow udp 3702 'NAS WS-Discovery'
  firewall_allow tcp 5357 'NAS WS-Discovery HTTP'
}

setup_network() {
  [[ "$NET_STATIC" == "yes" ]] || return 0
  ensure_packages iproute2 python3
  detect_iface
  local gw="$NET_GATEWAY"
  [[ -n "$gw" ]] || gw=$(ip -4 route show default | awk '{print $3; exit}')
  [[ -n "$gw" ]] || die "Gateway não encontrado; defina NET_GATEWAY."
  log "Configurando IP estático $NAS_IP/$NAS_PREFIX em $NET_IFACE (gateway $gw)..."
  warn "A conexão atual (ex.: SSH) pode cair; reconecte em $NAS_IP."

  if systemctl is-active --quiet NetworkManager; then
    local con
    con=$(nmcli -g GENERAL.CONNECTION device show "$NET_IFACE")
    [[ -n "$con" ]] || die "Nenhuma conexão NetworkManager em $NET_IFACE."
    nmcli connection modify "$con" ipv4.method manual ipv4.addresses "$NAS_IP/$NAS_PREFIX" \
      ipv4.gateway "$gw" ipv4.dns "127.0.0.1 ${DNS_UPSTREAM_int[*]}"
    nmcli connection up "$con" || warn "Falha ao reativar a conexão; reinicie o sistema."
  elif [[ -f /etc/network/interfaces ]]; then
    cp -n /etc/network/interfaces /etc/network/interfaces.orig || true
    # Remove the previous interface configuration
    sed -i -E "/^(auto|allow-hotplug|iface)[[:space:]]+$NET_IFACE([[:space:]]|\$)/,/^$/d" /etc/network/interfaces
    cat >> /etc/network/interfaces <<EOF

auto $NET_IFACE
iface $NET_IFACE inet static
    address $NAS_IP/$NAS_PREFIX
    gateway $gw

EOF
    systemctl restart networking || warn "Falha ao reiniciar a rede; reinicie o sistema."
  else
    die "Gerenciador de rede não suportado (use NetworkManager ou ifupdown)."
  fi
}

update_blocklist_script() {
  local urls="" u
  for u in "${DNS_BLOCKLISTS[@]}"; do urls+="  \"$u\""$'\n'; done
  mkdir -p /var/lib/nas-dns
  cat > /usr/local/sbin/update-dns-blocklist <<EOF
#!/usr/bin/env bash
# Generated by nas-setup.sh: downloads and consolidates dnsmasq blocklists.
set -uo pipefail
OUT=/var/lib/nas-dns/blocklist.hosts
TMP=\$(mktemp)
URLS=(
$urls)
for url in "\${URLS[@]}"; do
  curl -fsSL --max-time 120 "\$url" || echo "Falha ao baixar \$url" >&2
done | awk '
  \$1=="0.0.0.0" || \$1=="127.0.0.1" { d=tolower(\$2) }
  \$0 ~ /^(local|address|server)=\\// { split(\$0, a, "/"); d=tolower(a[2]) }
  d != "" { if (d!="" && d!~/^(localhost|localhost.localdomain|local|broadcasthost|0.0.0.0)\$/ && !(d in s)) { s[d]=1; print "0.0.0.0 " d }; d="" }' > "\$TMP"
if [[ -s "\$TMP" ]]; then
  install -m 644 "\$TMP" "\$OUT"
  systemctl reload dnsmasq 2>/dev/null || systemctl restart dnsmasq
fi
rm -f "\$TMP"
EOF
  chmod 755 /usr/local/sbin/update-dns-blocklist

  cat > /etc/systemd/system/nas-dns-blocklist.service <<'EOF'
[Unit]
Description=Atualiza listas de bloqueio do DNS
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/update-dns-blocklist
EOF
  cat > /etc/systemd/system/nas-dns-blocklist.timer <<'EOF'
[Unit]
Description=Atualização semanal das listas de bloqueio do DNS

[Timer]
OnCalendar=weekly
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now nas-dns-blocklist.timer
}

setup_dns() {
  [[ "$DNS_ENABLE" == "yes" || "$DHCP_ENABLE" == "yes" ]] || return 0
  ensure_packages dnsmasq curl
  detect_iface
  local gw="$NET_GATEWAY"
  [[ -n "$gw" ]] || gw=$(ip -4 route show default | awk '{print $3; exit}')
  log "Configurando dnsmasq (DNS: $DNS_ENABLE, DHCP: $DHCP_ENABLE)..."

  if [[ "$DNS_ENABLE" == "yes" ]]; then
    # systemd-resolved occupies port 53
    if systemctl is-enabled --quiet systemd-resolved 2>/dev/null \
      || systemctl is-active --quiet systemd-resolved 2>/dev/null; then
      mkdir -p /etc/nas-setup
      touch /etc/nas-setup/systemd-resolved.was-enabled
    fi
    systemctl disable --now systemd-resolved 2>/dev/null || true
  fi

  {
    echo "# Generated by nas-setup.sh"
    echo "interface=$NET_IFACE"
    echo "bind-interfaces"
    echo "domain-needed"
    echo "bogus-priv"
    echo "no-resolv"
    echo "no-hosts"
    echo "addn-hosts=/var/lib/nas-dns/hosts"
    echo "domain=$DNS_DOMAIN"
    echo "local=/$DNS_DOMAIN/"
    echo "expand-hosts"
    echo "log-queries=extra"
    echo "log-dhcp"
    # Prevent /etc/hosts (127.0.1.1) from being provided to clients as the NAS address
    echo "host-record=$(hostname),$(hostname).$DNS_DOMAIN,$NAS_IP"
    local s
    for s in "${DNS_UPSTREAM_int[@]}"; do echo "server=$s"; done
    if [[ "$DNS_ENABLE" == "yes" ]]; then
      echo "cache-size=10000"
      echo "addn-hosts=/var/lib/nas-dns/blocklist.hosts"
    else
      echo "port=0"
    fi
    if [[ "$DHCP_ENABLE" == "yes" ]]; then
      echo "dhcp-authoritative"
      echo "dhcp-range=$DHCP_RANGE_START,$DHCP_RANGE_END,$DHCP_LEASE"
      [[ -n "$gw" ]] && echo "dhcp-option=option:router,$gw"
      echo "dhcp-option=option:dns-server,$NAS_IP"
      [[ "$NTP_ENABLE" == "yes" ]] && echo "dhcp-option=option:ntp-server,$NAS_IP"
    fi
  } > /etc/dnsmasq.d/nas.conf

  mkdir -p /var/lib/nas-dns
  awk '$1 !~ /^127\./ && $1 != "::1" { print }' /etc/hosts > /var/lib/nas-dns/hosts
  touch /var/lib/nas-dns/blocklist.hosts
  if [[ "$DNS_ENABLE" == "yes" ]]; then
    mkdir -p /etc/nas-setup
    if [[ ! -e /etc/nas-setup/resolv.conf.orig && -e /etc/resolv.conf ]]; then
      cp -a /etc/resolv.conf /etc/nas-setup/resolv.conf.orig
    fi
    touch /etc/nas-setup/dns-enabled
    update_blocklist_script
    log "Baixando listas de bloqueio..."
    /usr/local/sbin/update-dns-blocklist || warn "Falha ao baixar as listas."
    # Make the NAS itself use the local DNS
    if [[ -L /etc/resolv.conf ]]; then rm -f /etc/resolv.conf; fi
    chattr -i /etc/resolv.conf 2>/dev/null || true
    printf 'nameserver 127.0.0.1\nsearch %s\n' "$DNS_DOMAIN" > /etc/resolv.conf
  fi

  dnsmasq --test || die "Configuração do dnsmasq inválida."
  systemctl enable dnsmasq
  systemctl restart dnsmasq
  if [[ "$DNS_ENABLE" == "yes" ]]; then
    firewall_allow tcp 53 'NAS DNS TCP'
    firewall_allow udp 53 'NAS DNS UDP'
  else
    firewall_delete tcp 53
    firewall_delete udp 53
  fi
  if [[ "$DHCP_ENABLE" == "yes" ]]; then
    firewall_allow_dhcp
  else
    firewall_delete_dhcp
  fi
}

setup_ntp() {
  [[ "$NTP_ENABLE" == "yes" ]] || return 0
  ensure_packages chrony
  log "Configurando servidor NTP (chrony) para $LAN_CIDR..."
  if systemctl is-enabled --quiet systemd-timesyncd 2>/dev/null \
    || systemctl is-active --quiet systemd-timesyncd 2>/dev/null; then
    mkdir -p /etc/nas-setup
    touch /etc/nas-setup/systemd-timesyncd.was-enabled
  fi
  systemctl disable --now systemd-timesyncd 2>/dev/null || true

  {
    echo "# Generated by nas-setup.sh"
    local s
    for s in "${NTP_UPSTREAM[@]}"; do echo "pool $s iburst"; done
    cat <<EOF
driftfile /var/lib/chrony/chrony.drift
makestep 1.0 3
rtcsync
allow $LAN_CIDR
local stratum 10
EOF
  } > /etc/chrony/chrony.conf

  systemctl enable chrony
  systemctl restart chrony
  firewall_allow udp 123 'NAS NTP'
}

setup_speedtest() {
  [[ "$SPEEDTEST_ENABLE" == "yes" ]] || return 0
  ensure_packages python3 speedtest-cli python3-matplotlib
  local src
  src="$(dirname "$(readlink -f "$0")")/marcelinoSpeedtest.py"
  [[ -f "$src" ]] || die "$src não encontrado."
  log "Instalando teste de velocidade da internet..."
  setup_user

  install -m 755 "$src" /usr/local/bin/marcelinoSpeedtest
  sed -i "s|^DEFAULT_LOG = .*|DEFAULT_LOG = \"$SPEEDTEST_LOG\"|; s|^DEFAULT_OUT = .*|DEFAULT_OUT = \"$SPEEDTEST_OUT\"|" /usr/local/bin/marcelinoSpeedtest

  mkdir -p "$SPEEDTEST_OUT"
  id "$SAMBA_USER" >/dev/null 2>&1 && chown "$SAMBA_USER":"$(id -gn "$SAMBA_USER")" "$SPEEDTEST_OUT"
  touch "$SPEEDTEST_LOG"
  chmod 644 "$SPEEDTEST_LOG"

  cat > /etc/systemd/system/marcelinoSpeedtest.service <<'EOF'
[Unit]
Description=Teste de velocidade da internet
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/marcelinoSpeedtest test
EOF
  cat > /etc/systemd/system/marcelinoSpeedtest.timer <<'EOF'
[Unit]
Description=Teste de velocidade da internet de hora em hora

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
EOF
  cat > /etc/systemd/system/marcelinoSpeedtest-graph.service <<'EOF'
[Unit]
Description=Gráfico diário da velocidade da internet

[Service]
Type=oneshot
ExecStart=/usr/local/bin/marcelinoSpeedtest graph --hours 24
EOF
  cat > /etc/systemd/system/marcelinoSpeedtest-graph.timer <<EOF
[Unit]
Description=Gráfico diário da velocidade da internet

[Timer]
OnCalendar=*-*-* $SPEEDTEST_GRAPH_TIME:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now marcelinoSpeedtest.timer marcelinoSpeedtest-graph.timer
}

setup_marcelino_server() {
  local dir config_tmp
  dir="$(dirname "$(readlink -f "$0")")"
  [[ -f "$dir/marcelinoServer.py" && -f "$dir/marcelinoServer.service" ]] \
    || die "marcelinoServer.py/.service não encontrados em $dir."
  [[ "$WEB_STATUS" == /* && "$WEB_STATUS" != *[[:space:]\"\\%]* ]] \
    || die "WEB_STATUS deve ser um caminho absoluto sem espaços, aspas, barras invertidas ou %."
  command -v systemctl >/dev/null 2>&1 || die "O servidor requer systemd."
  ensure_packages python3 python3-psutil smartmontools
  log "Instalando marcelinoServer..."
  install -d -o root -g root -m 0755 /opt/marcelinoServer /etc/default
  install -d -o root -g root -m 0755 "$(dirname "$WEB_STATUS")"
  install -o root -g root -m 0644 "$dir/marcelinoServer.py" /opt/marcelinoServer/marcelinoServer.py
  install -o root -g root -m 0644 "$dir/marcelinoServer.service" /etc/systemd/system/marcelinoServer.service
  if [[ ! -f /etc/default/marcelinoServer ]]; then
    cat > /etc/default/marcelinoServer <<'EOF'
MARCELINOSRV_INTERVAL=5
MARCELINOSRV_FILE_MODE=0644
EOF
    chown root:root /etc/default/marcelinoServer
    chmod 0644 /etc/default/marcelinoServer
  fi
  config_tmp=$(mktemp /etc/default/marcelinoServer.XXXXXX)
  awk -v status_path="$WEB_STATUS" '
    /^MARCELINOSRV_(SCRIPT|STATUS|FILE_MODE)=/ { next }
    { print }
    END {
      print "MARCELINOSRV_SCRIPT=/opt/marcelinoServer/marcelinoServer.py"
      print "MARCELINOSRV_STATUS=" status_path
      print "MARCELINOSRV_FILE_MODE=0644"
    }
  ' /etc/default/marcelinoServer > "$config_tmp"
  chown --reference=/etc/default/marcelinoServer "$config_tmp"
  chmod --reference=/etc/default/marcelinoServer "$config_tmp"
  mv "$config_tmp" /etc/default/marcelinoServer
  if [[ -f "$WEB_STATUS" ]]; then
    chown root:root "$WEB_STATUS"
    chmod 0644 "$WEB_STATUS"
  fi
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify /etc/systemd/system/marcelinoServer.service
  fi
  systemctl daemon-reload
  systemctl enable marcelinoServer.service
  systemctl restart marcelinoServer.service
  log "Métricas publicadas em $WEB_STATUS."
}

setup_web() {
  [[ "$WEB_ENABLE" == "yes" ]] || return 0
  ensure_packages python3 sudo
  [[ "$WEB_TLS" != "yes" ]] || ensure_packages openssl
  local dir src tls_args=""
  dir="$(dirname "$(readlink -f "$0")")"
  src="$dir/marcelinoWeb.py"
  [[ -f "$src" && -f "$dir/marcelinoWeb.service" ]] || die "marcelinoWeb.py/.service não encontrados em $dir."
  log "Instalando dashboard web..."

  id "$WEB_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$WEB_USER"
  install -d -o root -g root -m 0755 /opt/marcelinoWeb
  install -o root -g root -m 0644 "$src" /opt/marcelinoWeb/marcelinoWeb.py
  install -d -o "$WEB_USER" -g "$WEB_USER" -m 0700 /etc/marcelinoWeb

  if [[ "$WEB_TLS" == "yes" ]]; then
    if [[ ! -f /etc/marcelinoWeb/cert.pem ]]; then
      openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=$(hostname)" \
        -addext "subjectAltName=DNS:$(hostname),DNS:$(hostname).$LOCAL_DOMAIN,IP:$NAS_IP" \
        -keyout /etc/marcelinoWeb/key.pem -out /etc/marcelinoWeb/cert.pem 2>/dev/null
    fi
    chown "$WEB_USER":"$WEB_USER" /etc/marcelinoWeb/key.pem /etc/marcelinoWeb/cert.pem
    chmod 600 /etc/marcelinoWeb/key.pem
    tls_args="--cert /etc/marcelinoWeb/cert.pem --key /etc/marcelinoWeb/key.pem"
  fi

  cat > /etc/default/marcelinoWeb <<EOF
MARCELINOWEB_HOST=0.0.0.0
MARCELINOWEB_PORT=$WEB_PORT
MARCELINOWEB_STATUS=$WEB_STATUS
MARCELINOWEB_TLS_ARGS="$tls_args"
EOF
  install -o root -g root -m 0644 "$dir/marcelinoWeb.service" /etc/systemd/system/marcelinoWeb.service
  cat > /usr/local/bin/marcelinoWeb <<'EOF'
#!/bin/sh
exec sudo -u marcelinoweb /usr/bin/python3 /opt/marcelinoWeb/marcelinoWeb.py --users /etc/marcelinoWeb/users.json "$@"
EOF
  chmod 755 /usr/local/bin/marcelinoWeb

  if [[ -n "$WEB_ADMIN" ]]; then
    local web_cmd=useradd
    log "Cadastrando o usuário web '$WEB_ADMIN'..."
    [[ "$WEB_ADMIN" == "$USER_NAME" ]] || die "WEB_ADMIN deve ser igual a USER_NAME ou ficar vazio."
    setup_user
    ensure_user_password
    /usr/local/bin/marcelinoWeb users | grep -qx "$WEB_ADMIN" && web_cmd=passwd
    printf '%s\n' "$USER_PASSWORD" | /usr/local/bin/marcelinoWeb "$web_cmd" "$WEB_ADMIN" \
      || warn "Usuário web não cadastrado."
  else
    warn "Nenhum usuário web cadastrado. Execute: sudo marcelinoWeb useradd NOME"
  fi

  systemctl daemon-reload
  systemctl enable marcelinoWeb.service
  systemctl restart marcelinoWeb.service
  firewall_allow tcp "$WEB_PORT" 'NAS dashboard web'
}

setup_anytype() {
  [[ "$ANYTYPE_ENABLE" == "yes" ]] || return 0
  ensure_packages docker.io tar gzip
  [[ "$ANYTYPE_DATA" == /* && "$ANYTYPE_DATA" != *[[:space:]\"\\%]* ]] \
    || die "ANYTYPE_DATA deve ser um caminho absoluto sem espaços, aspas, barras invertidas ou %."
  [[ "$ANYTYPE_BACKUP" == /* && "$ANYTYPE_BACKUP" != *[[:space:]\"\\%]* ]] \
    || die "ANYTYPE_BACKUP deve ser um caminho absoluto sem espaços, aspas, barras invertidas ou %."
  local data_path backup_path
  data_path="$(realpath -m -- "$ANYTYPE_DATA")"
  backup_path="$(realpath -m -- "$ANYTYPE_BACKUP")"
  [[ "$backup_path/" != "$data_path/"* ]] \
    || die "ANYTYPE_BACKUP não pode ficar dentro de ANYTYPE_DATA."
  [[ -n "$ANYTYPE_HOST" && "$ANYTYPE_HOST" != *[[:space:]\"\\%]* ]] \
    || die "ANYTYPE_HOST inválido. Use o IP ou hostname acessível pela LAN."
  [[ "$ANYTYPE_IMAGE" =~ ^[a-zA-Z0-9./_:@-]+$ ]] || die "ANYTYPE_IMAGE inválida."
  log "Configurando sync host local do Anytype..."
  systemctl enable --now docker
  install -d -o root -g root -m 0700 "$ANYTYPE_DATA" "$ANYTYPE_BACKUP"
  docker pull "$ANYTYPE_IMAGE"

  cat > /etc/systemd/system/nas-anytype.service <<EOF
[Unit]
Description=Anytype local sync host (any-sync-bundle)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor="$ANYTYPE_DATA"

[Service]
Restart=on-failure
RestartSec=10
TimeoutStopSec=150
ExecStart=/usr/bin/docker run --rm --name nas-anytype --network host --stop-timeout 120 --env "ANY_SYNC_BUNDLE_INIT_EXTERNAL_ADDRS=$ANYTYPE_HOST" --volume "$ANYTYPE_DATA:/data" $ANYTYPE_IMAGE
ExecStop=/usr/bin/docker stop --time 120 nas-anytype

[Install]
WantedBy=multi-user.target
EOF
  cat > /usr/local/sbin/nas-anytype-backup <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
data_path="$1"
backup_path="$2"
temporary=""
restart_service=no
cleanup() {
  local status=$?
  trap - EXIT
  [[ -z "$temporary" ]] || rm -f -- "$temporary" || status=$?
  if [[ "$restart_service" == yes ]]; then
    systemctl start nas-anytype.service || status=$?
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if systemctl is-active --quiet nas-anytype.service; then
  restart_service=yes
  systemctl stop nas-anytype.service
fi
temporary="$(mktemp "$backup_path/anytype-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.tar.gz.tmp")"
tar -czf "$temporary" -C "$data_path" .
mv -- "$temporary" "${temporary%.tmp}"
EOF
  chmod 700 /usr/local/sbin/nas-anytype-backup
  cat > /etc/systemd/system/nas-anytype-backup.service <<EOF
[Unit]
Description=Daily backup of Anytype data
After=docker.service nas-anytype.service
RequiresMountsFor="$ANYTYPE_DATA" "$ANYTYPE_BACKUP"

[Service]
Type=oneshot
UMask=0077
TimeoutStartSec=infinity
TimeoutStopSec=180
ExecStart=/usr/local/sbin/nas-anytype-backup "$ANYTYPE_DATA" "$ANYTYPE_BACKUP"
EOF
  cat > /etc/systemd/system/nas-anytype-backup.timer <<'EOF'
[Unit]
Description=Daily backup of Anytype data

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable nas-anytype.service
  systemctl restart nas-anytype.service
  systemctl enable --now nas-anytype-backup.timer
  firewall_allow tcp 33010 'NAS Anytype sync'
  firewall_allow udp 33020 'NAS Anytype QUIC'
  log "Anytype iniciado. Após a inicialização, importe $ANYTYPE_DATA/client-config.yml nos clientes."
  log "Backup diário do Anytype habilitado em $ANYTYPE_BACKUP (nas-anytype-backup.timer)."
  warn "O endereço anunciado é persistido no primeiro início; alterações posteriores exigem editar bundle-config.yml."
}

setup_syncthing() {
  [[ "$SYNCTHING_ENABLE" == "yes" ]] || return 0
  [[ "$SYNCTHING_HOME" == /* && "$SYNCTHING_HOME" != *[[:space:]\"\\%]* ]] \
    || die "SYNCTHING_HOME deve ser um caminho absoluto sem espaços, aspas, barras invertidas ou %."
  ensure_packages syncthing
  setup_user
  log "Configurando Syncthing para $USER_NAME..."
  install -d -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" -m 0700 "$SYNCTHING_HOME"
  cat > /etc/systemd/system/nas-syncthing.service <<EOF
[Unit]
Description=Syncthing NAS
Wants=network-online.target
After=network-online.target
RequiresMountsFor="$SYNCTHING_HOME" "$USER_HOME"

[Service]
User=$USER_NAME
Group=$(id -gn "$USER_NAME")
Environment=STNOUPGRADE=1
UMask=0077
ExecStart=/usr/bin/syncthing --no-browser --no-restart --home="$SYNCTHING_HOME" --gui-address=127.0.0.1:8384
Restart=on-failure
RestartSec=10
SuccessExitStatus=3 4

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable nas-syncthing.service
  systemctl restart nas-syncthing.service
  firewall_allow tcp 22000 'NAS Syncthing sync'
  firewall_allow udp 22000 'NAS Syncthing QUIC'
  firewall_allow udp 21027 'NAS Syncthing discovery'
  log "Syncthing iniciado. Painel local: http://127.0.0.1:8384 (use um túnel SSH)."
}

setup_ssh() {
  ensure_packages openssh-server
  log "Configurando SSH na porta $SSH_PORT..."
  cat > /etc/profile.d/ssh-timeout.sh <<EOF
# Generated by nas-setup.sh
TMOUT=$SSH_MAX_SESSION
readonly TMOUT
export TMOUT
EOF
  chmod 644 /etc/profile.d/ssh-timeout.sh
  mkdir -p /etc/ssh/sshd_config.d
  local root_login=no banner=none
  [[ "$SSH_ALLOW_ROOT" != "yes" ]] || root_login=yes
  [[ "$SSH_BANNER" != "yes" ]] || banner=/etc/issue.net
  cat > /etc/ssh/sshd_config.d/10-nas-setup.conf <<EOF
Port $SSH_PORT
PermitRootLogin $root_login
PasswordAuthentication $SSH_PASSWORD_AUTH
Banner $banner
ClientAliveInterval 60
ClientAliveCountMax 3
EOF
  /usr/sbin/sshd -t
  firewall_allow tcp "$SSH_PORT" 'NAS SSH'
  systemctl enable --now ssh
  systemctl reload ssh
}

setup_dlna() {
  [[ "$DLNA_ENABLE" == "yes" ]] || return 0
  ensure_packages minidlna
  local storage_mountpoint="$RAID_MOUNTPOINT"
  [[ -z "$CRYPT_DEVICE" ]] || storage_mountpoint="$CRYPT_MOUNTPOINT"
  mountpoint -q "$storage_mountpoint" || die "Armazenamento não montado em $storage_mountpoint; monte-o antes de configurar DLNA."
  [[ "$DLNA_PORT" =~ ^[0-9]+$ && "$DLNA_PORT" -ge 1 && "$DLNA_PORT" -le 65535 ]] \
    || die "DLNA_PORT inválida: $DLNA_PORT."
  getent group "$SAMBA_GROUP" >/dev/null || "$SBIN_DIR/groupadd" "$SAMBA_GROUP"
  id minidlna >/dev/null 2>&1 && "$SBIN_DIR/usermod" -aG "$SAMBA_GROUP" minidlna
  install -d -o root -g "$SAMBA_GROUP" -m 2770 \
    "$DLNA_MEDIA_MUSIC" "$DLNA_MEDIA_VIDEO" "$DLNA_MEDIA_PICTURES" "$DLNA_CACHE" "$DLNA_DB"
  detect_iface
  install -d -o minidlna -g minidlna -m 0750 "$(dirname "$DLNA_LOG")"
  cat > /etc/minidlna.conf <<EOF
port=$DLNA_PORT
network_interface=$NET_IFACE
friendly_name=$(hostname) NAS
media_dir=A,$DLNA_MEDIA_MUSIC
media_dir=V,$DLNA_MEDIA_VIDEO
media_dir=P,$DLNA_MEDIA_PICTURES
db_dir=$DLNA_DB
log_dir=$(dirname "$DLNA_LOG")
inotify=yes
EOF
  systemctl enable --now minidlna
  systemctl restart minidlna
  firewall_allow udp 1900 'NAS DLNA SSDP'
  firewall_allow tcp "$DLNA_PORT" 'NAS DLNA HTTP'
}

uninstall_samba() {
  systemctl disable --now smbd nmbd 2>/dev/null || true
  firewall_delete tcp 139,445
  firewall_delete udp 137,138
  firewall_delete tcp 139
  firewall_delete tcp 445
  firewall_delete udp 137
  firewall_delete udp 138
  purge_packages samba
  if [[ -f /etc/samba/smb.conf.orig ]]; then
    install -d /etc/samba
    cp -a /etc/samba/smb.conf.orig /etc/samba/smb.conf
  fi
  systemctl daemon-reload
}

uninstall_discovery() {
  local package service
  for service in avahi-daemon wsdd-server wsdd wsdd2; do
    systemctl disable --now "$service" 2>/dev/null || true
  done
  rm -f /etc/avahi/services/smb.service
  firewall_delete udp 5353
  firewall_delete udp 3702
  firewall_delete tcp 5357
  purge_packages avahi-daemon wsdd-server wsdd wsdd2
  systemctl daemon-reload
}

uninstall_ntp() {
  systemctl disable --now chrony 2>/dev/null || true
  firewall_delete udp 123
  purge_packages chrony
  if [[ -e /etc/nas-setup/systemd-timesyncd.was-enabled ]]; then
    systemctl enable --now systemd-timesyncd 2>/dev/null || true
    rm -f /etc/nas-setup/systemd-timesyncd.was-enabled
  fi
}

uninstall_dns() {
  systemctl disable --now nas-dns-blocklist.timer dnsmasq 2>/dev/null || true
  rm -f /etc/dnsmasq.d/nas.conf \
    /etc/systemd/system/nas-dns-blocklist.service \
    /etc/systemd/system/nas-dns-blocklist.timer \
    /usr/local/sbin/update-dns-blocklist
  firewall_delete tcp 53
  firewall_delete udp 53
  firewall_delete_dhcp
  purge_packages dnsmasq curl
  if [[ -e /etc/nas-setup/dns-enabled ]]; then
    if [[ -e /etc/nas-setup/resolv.conf.orig ]]; then
      rm -f /etc/resolv.conf
      cp -a /etc/nas-setup/resolv.conf.orig /etc/resolv.conf
    else
      warn "Backup de /etc/resolv.conf não encontrado; restaure o DNS do sistema manualmente."
    fi
    rm -f /etc/nas-setup/dns-enabled
  fi
  if [[ -e /etc/nas-setup/systemd-resolved.was-enabled ]]; then
    systemctl enable --now systemd-resolved 2>/dev/null || true
    rm -f /etc/nas-setup/systemd-resolved.was-enabled
  fi
  systemctl daemon-reload
}

uninstall_speedtest() {
  systemctl disable --now marcelinoSpeedtest.timer marcelinoSpeedtest-graph.timer 2>/dev/null || true
  rm -f /etc/systemd/system/marcelinoSpeedtest.service \
    /etc/systemd/system/marcelinoSpeedtest.timer \
    /etc/systemd/system/marcelinoSpeedtest-graph.service \
    /etc/systemd/system/marcelinoSpeedtest-graph.timer \
    /usr/local/bin/marcelinoSpeedtest
  purge_packages speedtest-cli python3-matplotlib
  systemctl daemon-reload
}

uninstall_marcelino_server() {
  systemctl disable --now marcelinoServer.service 2>/dev/null || true
  rm -f /etc/systemd/system/marcelinoServer.service
  rm -f /opt/marcelinoServer/marcelinoServer.py
  rmdir /opt/marcelinoServer 2>/dev/null || true
  systemctl daemon-reload
  log "Configuração em /etc/default/marcelinoServer e métricas preservadas."
}

uninstall_web() {
  systemctl disable --now marcelinoWeb.service 2>/dev/null || true
  rm -f /etc/systemd/system/marcelinoWeb.service /etc/default/marcelinoWeb /usr/local/bin/marcelinoWeb
  rm -rf /opt/marcelinoWeb
  firewall_delete tcp "$WEB_PORT"
  systemctl daemon-reload
  log "Configuração, certificados e usuários web preservados em /etc/marcelinoWeb."
}

uninstall_anytype() {
  systemctl disable --now nas-anytype-backup.timer 2>/dev/null || true
  systemctl stop nas-anytype-backup.service 2>/dev/null || true
  systemctl disable --now nas-anytype.service 2>/dev/null || true
  rm -f /etc/systemd/system/nas-anytype.service \
    /etc/systemd/system/nas-anytype-backup.service \
    /etc/systemd/system/nas-anytype-backup.timer \
    /usr/local/sbin/nas-anytype-backup
  firewall_delete tcp 33010
  firewall_delete udp 33020
  systemctl daemon-reload
  if command -v docker >/dev/null 2>&1 && systemctl is-active --quiet docker; then
    local active_containers
    active_containers=$(docker ps -q 2>/dev/null) || active_containers=unknown
    if [[ -z "$active_containers" ]]; then
      systemctl disable --now docker.socket docker 2>/dev/null || true
      purge_packages docker.io
    else
      warn "Docker mantido porque há outros contêineres ativos."
    fi
  else
    purge_packages docker.io
  fi
  log "Dados do Anytype preservados em $ANYTYPE_DATA e backups em $ANYTYPE_BACKUP."
}

uninstall_syncthing() {
  systemctl disable --now nas-syncthing.service 2>/dev/null || true
  rm -f /etc/systemd/system/nas-syncthing.service
  firewall_delete tcp 22000
  firewall_delete udp 22000
  firewall_delete udp 21027
  purge_packages syncthing
  systemctl daemon-reload
  log "Configuração e identidade preservadas em $SYNCTHING_HOME; pastas sincronizadas preservadas."
}

uninstall_ssh() {
  systemctl disable --now ssh 2>/dev/null || systemctl disable --now sshd 2>/dev/null || true
  rm -f /etc/profile.d/ssh-timeout.sh /etc/ssh/sshd_config.d/10-nas-setup.conf
  firewall_delete tcp "$SSH_PORT"
  purge_packages openssh-server
  systemctl daemon-reload
}

uninstall_dlna() {
  systemctl disable --now minidlna 2>/dev/null || true
  rm -f /etc/minidlna.conf
  firewall_delete udp 1900
  firewall_delete tcp "$DLNA_PORT"
  purge_packages minidlna
  log "Mídias, banco/cache DLNA e logs preservados."
}

###############################################################################
# EXECUTION
###############################################################################

WSDD_PKG=""
RUN_ALL="no"
HAS_UNINSTALL="no"
declare -a SELECTED_STEPS=()

if [[ $# -eq 0 ]]; then
  show_help
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)       RUN_ALL="yes" ;;
    --raid)      SELECTED_STEPS+=(raid) ;;
    --storage)   SELECTED_STEPS+=(storage) ;;
    --user)      SELECTED_STEPS+=(user) ;;
    --samba)     SELECTED_STEPS+=(samba) ;;
    --discovery) SELECTED_STEPS+=(discovery) ;;
    --network)   SELECTED_STEPS+=(network) ;;
    --ntp)       SELECTED_STEPS+=(ntp) ;;
    --dns)       SELECTED_STEPS+=(dns) ;;
    --speedtest) SELECTED_STEPS+=(speedtest) ;;
    --marcelino-server) SELECTED_STEPS+=(marcelino_server) ;;
    --web)       SELECTED_STEPS+=(web) ;;
    --anytype)   SELECTED_STEPS+=(anytype) ;;
    --syncthing) SELECTED_STEPS+=(syncthing) ;;
    --ssh)       SELECTED_STEPS+=(ssh) ;;
    --dlna)      SELECTED_STEPS+=(dlna) ;;
    --uninstall-samba|--unistall-samba) SELECTED_STEPS+=(uninstall_samba); HAS_UNINSTALL="yes" ;;
    --uninstall-discovery|--unistall-discovery) SELECTED_STEPS+=(uninstall_discovery); HAS_UNINSTALL="yes" ;;
    --uninstall-ntp|--unistall-ntp) SELECTED_STEPS+=(uninstall_ntp); HAS_UNINSTALL="yes" ;;
    --uninstall-dns|--unistall-dns) SELECTED_STEPS+=(uninstall_dns); HAS_UNINSTALL="yes" ;;
    --uninstall-speedtest|--unistall-speedtest) SELECTED_STEPS+=(uninstall_speedtest); HAS_UNINSTALL="yes" ;;
    --uninstall-marcelino-server|--unistall-marcelino-server) SELECTED_STEPS+=(uninstall_marcelino_server); HAS_UNINSTALL="yes" ;;
    --uninstall-web|--unistall-web) SELECTED_STEPS+=(uninstall_web); HAS_UNINSTALL="yes" ;;
    --uninstall-anytype|--unistall-anytype) SELECTED_STEPS+=(uninstall_anytype); HAS_UNINSTALL="yes" ;;
    --uninstall-syncthing|--unistall-syncthing) SELECTED_STEPS+=(uninstall_syncthing); HAS_UNINSTALL="yes" ;;
    --uninstall-ssh|--unistall-ssh) SELECTED_STEPS+=(uninstall_ssh); HAS_UNINSTALL="yes" ;;
    --uninstall-dlna|--unistall-dlna) SELECTED_STEPS+=(uninstall_dlna); HAS_UNINSTALL="yes" ;;
    -h|--help)   show_help; exit 0 ;;
    *)           die "Opção desconhecida: $1. Use --help para ver as opções." ;;
  esac
  shift
done

if [[ "$RUN_ALL" != "yes" && ${#SELECTED_STEPS[@]} -eq 0 ]]; then
  die "Informe ao menos uma opção ou use --all."
fi

[[ "$RUN_ALL" != "yes" && " ${SELECTED_STEPS[*]} " != *" raid "* && " ${SELECTED_STEPS[*]} " != *" storage "* ]] \
  || validate_raid_filesystem

[[ "$RUN_ALL" != "yes" || "$HAS_UNINSTALL" != "yes" ]] \
  || die "--all não pode ser combinado com opções de desinstalação."

[[ $EUID -eq 0 ]] || die "Execute como root."

NEEDS_LAN="no"
for step in "${SELECTED_STEPS[@]}"; do
  case "$step" in
    marcelino_server|uninstall_marcelino_server) ;;
    samba|discovery|ntp|dns|speedtest|web|anytype|syncthing|ssh|dlna|uninstall_*) NEEDS_LAN="yes" ;;
  esac
done
[[ "$RUN_ALL" != "yes" ]] || NEEDS_LAN="yes"
if [[ "$NEEDS_LAN" == "yes" ]]; then
  LAN_CIDR=$(detect_lan_cidr)
  log "Rede local: $LAN_CIDR"
fi

if [[ "$RUN_ALL" == "yes" ]]; then
  SELECTED_STEPS=(network raid storage user ssh samba discovery ntp dns speedtest marcelino_server web anytype syncthing dlna)
fi

# Run in canonical order, regardless of the argument order
ORDERED_STEPS=()
for s in network raid storage user ssh samba discovery ntp dns speedtest marcelino_server web anytype syncthing dlna \
  uninstall_samba uninstall_discovery uninstall_ntp uninstall_dns uninstall_speedtest \
  uninstall_marcelino_server uninstall_web uninstall_anytype uninstall_syncthing uninstall_ssh uninstall_dlna; do
  for step in "${SELECTED_STEPS[@]}"; do
    [[ "$step" == "$s" ]] && ORDERED_STEPS+=("$s")
  done
done
SELECTED_STEPS=("${ORDERED_STEPS[@]}")

for step in "${SELECTED_STEPS[@]}"; do
  case "$step" in
    samba|discovery|ssh) ;;
    ntp)     [[ "$NTP_ENABLE" == "yes" ]] || continue ;;
    dns)     [[ "$DNS_ENABLE" == "yes" || "$DHCP_ENABLE" == "yes" ]] || continue ;;
    web)     [[ "$WEB_ENABLE" == "yes" ]] || continue ;;
    anytype) [[ "$ANYTYPE_ENABLE" == "yes" ]] || continue ;;
    syncthing) [[ "$SYNCTHING_ENABLE" == "yes" ]] || continue ;;
    dlna)    [[ "$DLNA_ENABLE" == "yes" ]] || continue ;;
    *) continue ;;
  esac
  ensure_firewall
  break
done

for step in "${SELECTED_STEPS[@]}"; do
  case "$step" in
    raid)      setup_raid ;;
    storage)   setup_storage ;;
    user)      setup_user ;;
    samba)     setup_samba ;;
    discovery) setup_discovery ;;
    network)   setup_network ;;
    ntp)       setup_ntp ;;
    dns)       setup_dns ;;
    speedtest) setup_speedtest ;;
    marcelino_server) setup_marcelino_server ;;
    web)       setup_web ;;
    anytype)   setup_anytype ;;
    syncthing) setup_syncthing ;;
    ssh)       setup_ssh ;;
    dlna)      setup_dlna ;;
    uninstall_samba)     uninstall_samba ;;
    uninstall_discovery) uninstall_discovery ;;
    uninstall_ntp)      uninstall_ntp ;;
    uninstall_dns)      uninstall_dns ;;
    uninstall_speedtest) uninstall_speedtest ;;
    uninstall_marcelino_server) uninstall_marcelino_server ;;
    uninstall_web)       uninstall_web ;;
    uninstall_anytype)   uninstall_anytype ;;
    uninstall_syncthing) uninstall_syncthing ;;
    uninstall_ssh)       uninstall_ssh ;;
    uninstall_dlna)      uninstall_dlna ;;
  esac
  if [[ "$step" == uninstall_* ]]; then
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y
  fi
done

if [[ "$RUN_ALL" == "yes" ]]; then
  log "NAS configurado. Acesse: \\\\$(hostname)\\ (Windows) ou smb://$(hostname).$(LOCAL_DOMAIN) (Linux)."
fi

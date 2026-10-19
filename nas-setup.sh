#!/usr/bin/env bash
# Configura um NAS ( Network Attached Storage) com RAID, criptografia, Samba, DNS/DHCP e firewall.
# Uso: sudo ./nas-setup.sh
set -euo pipefail

###############################################################################
# CONFIGURAÇÃO
###############################################################################

# --- Rede ---
IP_INET="192.168.0.1"              # endereço IP da interface de rede do NAS
IP_GATEWAY="192.168.0.254"         # gateway/roteador de internet da interface de rede do NAS
IP_RANGE_START="192.168.0.100"     # início do intervalo DHCP
IP_RANGE_END="192.168.0.200"       # fim do intervalo DHCP
DNS_UPSTREAM=("1.1.1.1" "9.9.9.9") # resolvedores externos (Cloudflare e Quad9)
LOCAL_DOMAIN="lan"                 # domínio local usado para configuração de rede e Samba

# --- Usuário principal do NAS ---
USER_NAME="meneceu"                # usuário principal do NAS
USER_PASSWORD="sua_senha_aqui"     # senha do usuário principal do NAS
USER_HOME="/srv/nas/$USER_NAME"    # diretório home do usuário principal do NAS (o share Samba usa o nome com a primeira letra maiúscula)

# --- Sistema ---
SBIN_DIR="/sbin"                    # diretório dos executáveis administrativos

# --- RAID ---
RAID_DEVICES=(/dev/sdb /dev/sdc)   # discos/partições que farão parte do RAID
RAID_LEVEL=1                       # 0 (for performance), 1 (mirroring), 5 (striping with parity), 6 (dual parity), 10 (mirrored striping)
RAID_DEVICE="/dev/md0"             # dispositivo RAID a ser criado

# --- Sistema de arquivos sem criptografia ---
# Usado somente quando CRYPT_DEVICE estiver vazio.
RAID_MOUNTPOINT="/srv/nas"

# --- Sistema de arquivos do volume RAID (ou do volume dentro do LUKS) ---
# Opções: ext4, xfs ou btrfs.
RAID_FILESYSTEM="ext4"

# --- Criptografia (LUKS) ---
# Deixe CRYPT_DEVICE vazio para NÃO usar criptografia.
CRYPT_DEVICE="/dev/md0"            # ex.: "/dev/md0" (ou outra partição/disco)
CRYPT_MOUNTPOINT="/srv/nas"        # ponto de montagem do volume descriptografado
CRYPT_KEYFILE="/etc/nas.key"       # ex.: "/root/nas.key". Vazio = não gera keyfile (senha digitada)
CRYPT_AUTO_UNLOCK="yes"            # yes = descriptografa e monta no boot; no = manual
CRYPT_NAME="nas_crypt"             # nome do mapeamento em /dev/mapper

# --- Samba ---
# Formato: "nome_do_share:/caminho/do/diretorio"
SHARES=(
  "Publico:/srv/nas/publico"
  "Privado:/srv/nas/privado"
  "Backup:/srv/nas/backup"
  "Midia:/srv/nas/midia"
  "${USER_NAME^}:$USER_HOME"
  "Monitor:/srv/nas/monitor"
)
PUBLIC_SHARES=("Publico")                          # nomes de shares acessíveis sem senha (guest)
GROUP_SHARES=("Backup" "Midia")                    # shares acessíveis ao grupo SAMBA_GROUP
READONLY_SHARES=("Monitor")                        # shares visíveis, somente leitura (gravados localmente pelo marcelinoServer)
USER_SHARES=("${USER_NAME^}:$USER_NAME" "Privado:$USER_NAME")  # pares share:usuario acessíveis somente por esse usuário
SAMBA_USER="$USER_NAME"                               # usuário Samba (criado se não existir)
SAMBA_GROUP="nasgroup"                             # grupo Samba (criado se não existir)
WORKGROUP="WORKGROUP"                              # grupo de trabalho do Samba
DOMAIN="$LOCAL_DOMAIN"                             # domínio da rede local (para Samba)

# --- IP estático ---
NET_STATIC="yes"                   # yes = configura IP estático no Debian
NAS_IP="$IP_INET"                  # endereço IP do NAS (também usado como DNS/DHCP)
NAS_PREFIX=24                      # tamanho da máscara (24 = 255.255.255.0)
NET_IFACE=""                       # interface de rede. Vazio = detecta pela rota padrão
NET_GATEWAY="$IP_GATEWAY"          # gateway/roteador de internet. Vazio = usa o gateway atual

# --- DNS (dnsmasq) com filtro de anúncios, rastreadores e malware ---
DNS_ENABLE="yes"
# Resolvedores efetivos: DNS_UPSTREAM; se vazio, usa Cloudflare e Quad9
DNS_UPSTREAM_int=("1.1.1.1" "9.9.9.9")
[[ -z "${DNS_UPSTREAM[*]:-}" ]] || DNS_UPSTREAM_int=("${DNS_UPSTREAM[@]}")
DNS_DOMAIN="$LOCAL_DOMAIN"         # domínio local entregue aos clientes
# Listas em formato hosts ou dnsmasq (local=/dominio/)
DNS_BLOCKLISTS=(
  "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"                    # anúncios + malware
  "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/dnsmasq/pro.txt"        # anúncios, rastreadores, spyware
  "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/dnsmasq/tif.medium.txt" # ameaças (malware, phishing)
)

# --- DHCP (dnsmasq) ---
DHCP_ENABLE="yes"
DHCP_RANGE_START="$IP_RANGE_START"
DHCP_RANGE_END="$IP_RANGE_END"
DHCP_LEASE="12h"

# --- Rede / Firewall ---
LAN_CIDR=""                        # ex.: "192.168.1.0/24". Vazio = deriva de NAS_IP/NAS_PREFIX (ou detecta)
SSH_PORT=22                        # porta SSH do servidor NAS
SSH_MAX_SESSION=600                # segundos de inatividade até encerrar a sessão SSH (10 min)

# --- NTP (servidor de horário para a rede local) ---
NTP_ENABLE="yes"                              # yes = instala e configura o chrony como servidor NTP da LAN
NTP_UPSTREAM=("pool.ntp.br" "a.st1.ntp.br")   # fontes de horário externas

# --- Teste de velocidade da internet ---
SPEEDTEST_ENABLE="yes"                                  # yes = instala nas-speedtest e os timers
SPEEDTEST_LOG="/var/log/nas-speedtest.jsonl"             # log dos testes
SPEEDTEST_OUT="$USER_HOME/internet_test"        # gráficos salvos em ano/mes dentro desta pasta
SPEEDTEST_GRAPH_TIME="23:55"                             # horário do gráfico diário (últimas 24 horas)

# --- Dashboard web (marcelinoWeb) ---
WEB_ENABLE="yes"                                         # yes = instala o dashboard com login
WEB_PORT=443                                             # porta HTTP (acessível somente pela LAN)
WEB_STATUS="/srv/nas/monitor/.marcelinoSrv.json"         # JSON publicado pelo marcelinoServer
WEB_USER="marcelinoweb"                                  # usuário de sistema que executa o serviço
WEB_ADMIN="$USER_NAME"                                   # usuário web inicial (usa USER_PASSWORD; vazio = cadastre depois com marcelinoWeb useradd)
WEB_TLS="yes"                                            # yes = gera certificado autoassinado e usa HTTPS

###############################################################################
# FUNÇÕES
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
  [[ -n "$NET_IFACE" ]] || NET_IFACE=$(ip -4 route show default | awk '{print $5; exit}')
  [[ -n "$NET_IFACE" ]] || die "Interface padrão não encontrada; defina NET_IFACE."
}

detect_lan_cidr() {
  if [[ "$NET_STATIC" == "yes" ]]; then
    python3 -c "import sys,ipaddress; print(ipaddress.ip_interface(sys.argv[1]).network)" "$NAS_IP/$NAS_PREFIX"
    return
  fi
  local iface
  iface=$(ip -4 route show default | awk '{print $5; exit}')
  [[ -n "$iface" ]] || die "Interface padrão não encontrada; defina LAN_CIDR."
  ip -4 -o addr show dev "$iface" scope global | awk '{print $4; exit}' |
    python3 -c "import sys,ipaddress; print(ipaddress.ip_interface(sys.stdin.read().strip()).network)"
}

install_packages() {
  log "Instalando pacotes..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y mdadm samba avahi-daemon ufw python3 python3-psutil smartmontools pciutils fontconfig e2fsprogs util-linux sudo
  [[ "$WEB_ENABLE" == "yes" && "$WEB_TLS" == "yes" ]] && apt-get install -y openssl
  case "$RAID_FILESYSTEM" in
    xfs) apt-get install -y xfsprogs ;;
    btrfs) apt-get install -y btrfs-progs ;;
  esac
  [[ -n "$CRYPT_DEVICE" ]] && apt-get install -y cryptsetup
  # No Debian 13+ o gerador do crypttab fica em pacote separado; em versões antigas ele não existe.
  [[ -n "$CRYPT_DEVICE" ]] && { apt-get install -y systemd-cryptsetup || true; }
  [[ "$NTP_ENABLE" == "yes" ]] && apt-get install -y chrony
  [[ "$SPEEDTEST_ENABLE" == "yes" ]] && apt-get install -y speedtest-cli python3-matplotlib
  if [[ "$DNS_ENABLE" == "yes" || "$DHCP_ENABLE" == "yes" ]]; then
    apt-get install -y dnsmasq curl
  fi
  # Nome do pacote WS-Discovery varia entre versões do Debian
  local p
  for p in wsdd-server wsdd wsdd2; do
    if apt-get install -y "$p"; then
      WSDD_PKG="$p"
      return
    fi
    warn "Pacote $p indisponível; tentando o próximo."
  done
  warn "Nenhum pacote wsdd encontrado; Windows pode não listar o servidor automaticamente."
  WSDD_PKG=""
}

show_help() {
  cat <<EOF
Uso: sudo $0 [opção ...]

Opções:
  --all          Executa todas as etapas na ordem recomendada
  --install      Instala os pacotes necessários
  --raid         Cria e configura o RAID
  --storage      Configura o sistema de arquivos e a criptografia/montagem
  --user         Cria o usuário principal (USER_NAME) no sistema e no Samba
  --samba        Configura usuários, permissões e compartilhamentos Samba
  --discovery    Ativa descoberta de rede via Avahi e WS-Discovery
  --network      Configura o IP estático (NAS_IP)
  --ntp          Configura o servidor NTP (se NTP_ENABLE=yes)
  --dns          Configura o servidor DNS/DHCP com listas de bloqueio
  --speedtest    Instala o teste de velocidade da internet e seus timers
  --web          Instala o dashboard web com login (se WEB_ENABLE=yes)
  --ssh          Limita sessões SSH a SSH_MAX_SESSION segundos de inatividade
  --firewall     Configura o firewall para os serviços da rede local
  -h, --help     Mostra esta ajuda

Sem opções, mostra esta ajuda. É possível combinar etapas; elas serão
executadas na ordem acima, independentemente da ordem dos argumentos.
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

setup_user() {
  [[ -z "${USER_READY:-}" ]] || return 0
  [[ -n "$USER_NAME" ]] || die "USER_NAME vazio."
  [[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "USER_NAME inválido: $USER_NAME."
  [[ -n "$USER_PASSWORD" && "$USER_PASSWORD" != "sua_senha_aqui" ]] \
    || die "Defina USER_PASSWORD no topo do script."
  [[ ${#USER_PASSWORD} -ge 8 ]] || die "USER_PASSWORD deve ter ao menos 8 caracteres."
  log "Configurando usuário $USER_NAME..."

  getent group "$SAMBA_GROUP" >/dev/null || "$SBIN_DIR/groupadd" "$SAMBA_GROUP"
  if ! id "$USER_NAME" >/dev/null 2>&1; then
    "$SBIN_DIR/useradd" -M -d "$USER_HOME" -s /bin/bash -G "$SAMBA_GROUP" "$USER_NAME"
  else
    "$SBIN_DIR/usermod" -d "$USER_HOME" -aG "$SAMBA_GROUP" "$USER_NAME"
  fi
  mkdir -p "$USER_HOME"
  chown "$USER_NAME":"$(id -gn "$USER_NAME")" "$USER_HOME"
  printf '%s:%s\n' "$USER_NAME" "$USER_PASSWORD" | "$SBIN_DIR/chpasswd"
  printf '%s\n%s\n' "$USER_PASSWORD" "$USER_PASSWORD" | smbpasswd -s -a "$USER_NAME" >/dev/null
  USER_READY=1
}

setup_raid() {
  if [[ -e "$RAID_DEVICE" ]]; then
    warn "$RAID_DEVICE já existe; pulando criação do RAID."
    return
  fi
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
    # Com keyfile abre sozinho; sem keyfile ("none") a senha é pedida no boot.
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
}

setup_discovery() {
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
}

setup_network() {
  [[ "$NET_STATIC" == "yes" ]] || return 0
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
    # Remove configuração anterior da interface
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
# Gerado por nas-setup.sh: baixa e consolida as listas de bloqueio do dnsmasq.
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
  detect_iface
  local gw="$NET_GATEWAY"
  [[ -n "$gw" ]] || gw=$(ip -4 route show default | awk '{print $3; exit}')
  log "Configurando dnsmasq (DNS: $DNS_ENABLE, DHCP: $DHCP_ENABLE)..."

  # systemd-resolved ocupa a porta 53
  systemctl disable --now systemd-resolved 2>/dev/null || true

  {
    echo "# Gerado por nas-setup.sh"
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
    # Evita que o /etc/hosts (127.0.1.1) seja entregue aos clientes como endereço do NAS
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
    update_blocklist_script
    log "Baixando listas de bloqueio..."
    /usr/local/sbin/update-dns-blocklist || warn "Falha ao baixar as listas."
    # O próprio NAS passa a usar o DNS local
    if [[ -L /etc/resolv.conf ]]; then rm -f /etc/resolv.conf; fi
    chattr -i /etc/resolv.conf 2>/dev/null || true
    printf 'nameserver 127.0.0.1\nsearch %s\n' "$DNS_DOMAIN" > /etc/resolv.conf
  fi

  dnsmasq --test || die "Configuração do dnsmasq inválida."
  systemctl enable dnsmasq
  systemctl restart dnsmasq
}

setup_ntp() {
  [[ "$NTP_ENABLE" == "yes" ]] || return 0
  log "Configurando servidor NTP (chrony) para $LAN_CIDR..."
  systemctl disable --now systemd-timesyncd 2>/dev/null || true

  {
    echo "# Gerado por nas-setup.sh"
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
}

setup_speedtest() {
  [[ "$SPEEDTEST_ENABLE" == "yes" ]] || return 0
  local src
  src="$(dirname "$(readlink -f "$0")")/nas-speedtest.py"
  [[ -f "$src" ]] || die "$src não encontrado."
  log "Instalando teste de velocidade da internet..."
  setup_user

  install -m 755 "$src" /usr/local/bin/nas-speedtest
  sed -i "s|^DEFAULT_LOG = .*|DEFAULT_LOG = \"$SPEEDTEST_LOG\"|; s|^DEFAULT_OUT = .*|DEFAULT_OUT = \"$SPEEDTEST_OUT\"|" /usr/local/bin/nas-speedtest

  mkdir -p "$SPEEDTEST_OUT"
  id "$SAMBA_USER" >/dev/null 2>&1 && chown "$SAMBA_USER":"$(id -gn "$SAMBA_USER")" "$SPEEDTEST_OUT"
  touch "$SPEEDTEST_LOG"
  chmod 644 "$SPEEDTEST_LOG"

  cat > /etc/systemd/system/nas-speedtest.service <<'EOF'
[Unit]
Description=Teste de velocidade da internet
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/nas-speedtest test
EOF
  cat > /etc/systemd/system/nas-speedtest.timer <<'EOF'
[Unit]
Description=Teste de velocidade da internet de hora em hora

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
EOF
  cat > /etc/systemd/system/nas-speedtest-graph.service <<'EOF'
[Unit]
Description=Gráfico diário da velocidade da internet

[Service]
Type=oneshot
ExecStart=/usr/local/bin/nas-speedtest graph --hours 24
EOF
  cat > /etc/systemd/system/nas-speedtest-graph.timer <<EOF
[Unit]
Description=Gráfico diário da velocidade da internet

[Timer]
OnCalendar=*-*-* $SPEEDTEST_GRAPH_TIME:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now nas-speedtest.timer nas-speedtest-graph.timer
}

setup_web() {
  [[ "$WEB_ENABLE" == "yes" ]] || return 0
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
    /usr/local/bin/marcelinoWeb users | grep -qx "$WEB_ADMIN" && web_cmd=passwd
    printf '%s\n' "$USER_PASSWORD" | /usr/local/bin/marcelinoWeb "$web_cmd" "$WEB_ADMIN" \
      || warn "Usuário web não cadastrado."
  else
    warn "Nenhum usuário web cadastrado. Execute: sudo marcelinoWeb useradd NOME"
  fi

  systemctl daemon-reload
  systemctl enable marcelinoWeb.service
  systemctl restart marcelinoWeb.service
}

setup_ssh() {
  log "Configurando SSH: encerra sessões após $SSH_MAX_SESSION s de inatividade..."
  cat > /etc/profile.d/ssh-timeout.sh <<EOF
# Gerado por nas-setup.sh
TMOUT=$SSH_MAX_SESSION
readonly TMOUT
export TMOUT
EOF
  chmod 644 /etc/profile.d/ssh-timeout.sh
  mkdir -p /etc/ssh/sshd_config.d
  cat > /etc/ssh/sshd_config.d/10-timeout.conf <<EOF
ClientAliveInterval 60
ClientAliveCountMax 3
EOF
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
}

setup_firewall() {
  log "Configurando firewall (ufw) para $LAN_CIDR..."
  "$SBIN_DIR/ufw" --force reset >/dev/null
  "$SBIN_DIR/ufw" default deny incoming
  "$SBIN_DIR/ufw" default allow outgoing

  "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port "$SSH_PORT" proto tcp comment 'SSH'
  "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 139,445 proto tcp comment 'Samba'
  "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 137,138 proto udp comment 'NetBIOS'
  "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 5353 proto udp comment 'mDNS'
  "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 3702 proto udp comment 'WS-Discovery'
  "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 5357 proto tcp comment 'WSD HTTP'
  [[ "$WEB_ENABLE" == "yes" ]] && "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port "$WEB_PORT" proto tcp comment 'Dashboard web'
  [[ "$NTP_ENABLE" == "yes" ]] && "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 123 proto udp comment 'NTP'
  if [[ "$DNS_ENABLE" == "yes" ]]; then
    "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 53 comment 'DNS'
  fi
  if [[ "$DHCP_ENABLE" == "yes" ]]; then
    detect_iface
    # Clientes DHCP ainda não têm IP, então a origem não pode ser filtrada
    "$SBIN_DIR/ufw" allow in on "$NET_IFACE" to any port 67 proto udp comment 'DHCP'
  fi

  "$SBIN_DIR/ufw" --force enable
  "$SBIN_DIR/ufw" status verbose
}

###############################################################################
# EXECUÇÃO
###############################################################################

WSDD_PKG=""
RUN_ALL="no"
declare -a SELECTED_STEPS=()

if [[ $# -eq 0 ]]; then
  show_help
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)       RUN_ALL="yes" ;;
    --install)   SELECTED_STEPS+=(install) ;;
    --raid)      SELECTED_STEPS+=(raid) ;;
    --storage)   SELECTED_STEPS+=(storage) ;;
    --user)      SELECTED_STEPS+=(user) ;;
    --samba)     SELECTED_STEPS+=(samba) ;;
    --discovery) SELECTED_STEPS+=(discovery) ;;
    --network)   SELECTED_STEPS+=(network) ;;
    --ntp)       SELECTED_STEPS+=(ntp) ;;
    --dns)       SELECTED_STEPS+=(dns) ;;
    --speedtest) SELECTED_STEPS+=(speedtest) ;;
    --web)       SELECTED_STEPS+=(web) ;;
    --ssh)       SELECTED_STEPS+=(ssh) ;;
    --firewall)  SELECTED_STEPS+=(firewall) ;;
    -h|--help)   show_help; exit 0 ;;
    *)           die "Opção desconhecida: $1. Use --help para ver as opções." ;;
  esac
  shift
done

if [[ "$RUN_ALL" != "yes" && ${#SELECTED_STEPS[@]} -eq 0 ]]; then
  die "Informe ao menos uma etapa ou use --all."
fi

validate_raid_filesystem

[[ $EUID -eq 0 ]] || die "Execute como root."

NEEDS_LAN="no"
if [[ "$RUN_ALL" == "yes" ]]; then
  NEEDS_LAN="yes"
else
  for step in "${SELECTED_STEPS[@]}"; do
    case "$step" in
      samba|ntp|web|firewall) NEEDS_LAN="yes" ;;
    esac
  done
fi
if [[ "$NEEDS_LAN" == "yes" ]]; then
  [[ -n "$LAN_CIDR" ]] || LAN_CIDR=$(detect_lan_cidr)
  log "Rede local: $LAN_CIDR"
fi

if [[ "$RUN_ALL" == "yes" ]]; then
  SELECTED_STEPS=(install network raid storage user samba discovery ntp dns speedtest web ssh firewall)
fi

# Executa na ordem canônica, independentemente da ordem dos argumentos
ORDERED_STEPS=()
for s in install network raid storage user samba discovery ntp dns speedtest web ssh firewall; do
  for step in "${SELECTED_STEPS[@]}"; do
    [[ "$step" == "$s" ]] && ORDERED_STEPS+=("$s")
  done
done
SELECTED_STEPS=("${ORDERED_STEPS[@]}")

for step in "${SELECTED_STEPS[@]}"; do
  case "$step" in
    install)   install_packages ;;
    raid)      setup_raid ;;
    storage)   setup_storage ;;
    user)      setup_user ;;
    samba)     setup_samba ;;
    discovery) detect_wsdd_package; setup_discovery ;;
    network)   setup_network ;;
    ntp)       setup_ntp ;;
    dns)       setup_dns ;;
    speedtest) setup_speedtest ;;
    web)       setup_web ;;
    ssh)       setup_ssh ;;
    firewall)  setup_firewall ;;
  esac
done

if [[ "$RUN_ALL" == "yes" ]]; then
  log "NAS configurado. Acesse: \\\\$(hostname)\\ (Windows) ou smb://$(hostname).local (Linux)."
fi

#!/usr/bin/env bash
# Configura um NAS (RAID + LUKS opcional + Samba + descoberta + firewall) em Debian limpo.
# Uso: sudo ./nas-setup.sh
set -euo pipefail

###############################################################################
# CONFIGURAÇÃO
###############################################################################

# --- Sistema ---
SBIN_DIR="/sbin"                    # diretório dos executáveis administrativos

# --- RAID ---
RAID_DEVICES=(/dev/sdb /dev/sdc)   # discos/partições que farão parte do RAID
RAID_LEVEL=1                       # 0 (for performance), 1 (mirroring), 5 (striping with parity), 6 (dual parity), 10 (mirrored striping)
RAID_DEVICE="/dev/md0"             # dispositivo RAID a ser criado

# --- Sistema de arquivos sem criptografia ---
# Usado somente quando CRYPT_DEVICE estiver vazio.
RAID_MOUNTPOINT="/srv/nas"

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
  "Marcelo:/srv/marcelo"
)
PUBLIC_SHARES=("Publico")                          # nomes de shares acessíveis sem senha (guest)
GROUP_SHARES=("Backup" "Midia")                    # shares acessíveis ao grupo SAMBA_GROUP
USER_SHARES=("Marcelo:marcelo" "Privado:marcelo")  # pares share:usuario acessíveis somente por esse usuário
SAMBA_USER="marcelo"                               # usuário Samba (criado se não existir)
SAMBA_GROUP="nasgroup"                             # grupo Samba (criado se não existir)
WORKGROUP="WORKGROUP"                              # grupo de trabalho do Samba
DOMAIN="local"                                     # domínio da rede local (para Samba)

# --- Rede / Firewall ---
LAN_CIDR=""                        # ex.: "192.168.1.0/24". Vazio = detecta automaticamente
SSH_PORT=22                        # porta SSH do servidor NAS

# --- NTP (servidor de horário para a rede local) ---
NTP_ENABLE="yes"                              # yes = instala e configura o chrony como servidor NTP da LAN
NTP_UPSTREAM=("pool.ntp.br" "a.st1.ntp.br")   # fontes de horário externas

###############################################################################
# FUNÇÕES
###############################################################################

log()  { echo -e "\e[1;32m[+]\e[0m $*"; }
warn() { echo -e "\e[1;33m[!]\e[0m $*"; }
die()  { echo -e "\e[1;31m[x]\e[0m $*" >&2; exit 1; }

detect_lan_cidr() {
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
  apt-get install -y mdadm samba avahi-daemon ufw python3 e2fsprogs util-linux
  [[ -n "$CRYPT_DEVICE" ]] && apt-get install -y cryptsetup
  # No Debian 13+ o gerador do crypttab fica em pacote separado; em versões antigas ele não existe.
  [[ -n "$CRYPT_DEVICE" ]] && { apt-get install -y systemd-cryptsetup || true; }
  [[ "$NTP_ENABLE" == "yes" ]] && apt-get install -y chrony
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
  --samba        Configura usuários, permissões e compartilhamentos Samba
  --discovery    Ativa descoberta de rede via Avahi e WS-Discovery
  --ntp          Configura o servidor NTP (se NTP_ENABLE=yes)
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

add_fstab() { # uuid mountpoint options
  sed -i "\|[[:space:]]$2[[:space:]]|d" /etc/fstab
  echo "UUID=$1 $2 ext4 $3 0 2" >> /etc/fstab
}

setup_storage() {
  if [[ -z "$CRYPT_DEVICE" ]]; then
    log "Criptografia desativada; formatando $RAID_DEVICE em ext4."
    "$SBIN_DIR/blkid" "$RAID_DEVICE" >/dev/null 2>&1 || "$SBIN_DIR/mkfs.ext4" -F "$RAID_DEVICE"
    mkdir -p "$RAID_MOUNTPOINT"
    add_fstab "$("$SBIN_DIR/blkid" -s UUID -o value "$RAID_DEVICE")" "$RAID_MOUNTPOINT" "defaults,nofail"
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
  "$SBIN_DIR/blkid" "/dev/mapper/$CRYPT_NAME" >/dev/null 2>&1 || "$SBIN_DIR/mkfs.ext4" -F "/dev/mapper/$CRYPT_NAME"
  mkdir -p "$CRYPT_MOUNTPOINT"

  local luks_uuid fs_uuid
  luks_uuid=$("$SBIN_DIR/cryptsetup" luksUUID "$CRYPT_DEVICE")
  fs_uuid=$("$SBIN_DIR/blkid" -s UUID -o value "/dev/mapper/$CRYPT_NAME")

  sed -i "/^$CRYPT_NAME[[:space:]]/d" /etc/crypttab 2>/dev/null || true
  if [[ "$CRYPT_AUTO_UNLOCK" == "yes" ]]; then
    # Com keyfile abre sozinho; sem keyfile ("none") a senha é pedida no boot.
    echo "$CRYPT_NAME UUID=$luks_uuid $crypttab_key $crypttab_opts,nofail" >> /etc/crypttab
    add_fstab "$fs_uuid" "$CRYPT_MOUNTPOINT" "defaults,nofail,x-systemd.requires=systemd-cryptsetup@$CRYPT_NAME.service"
  else
    echo "$CRYPT_NAME UUID=$luks_uuid $crypttab_key $crypttab_opts,noauto" >> /etc/crypttab
    add_fstab "$fs_uuid" "$CRYPT_MOUNTPOINT" "defaults,noauto,nofail"
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
  id "$SAMBA_USER" >/dev/null 2>&1 || "$SBIN_DIR/useradd" -M -s "$SBIN_DIR/nologin" -G "$SAMBA_GROUP" "$SAMBA_USER"
  if ! pdbedit -L 2>/dev/null | grep -q "^$SAMBA_USER:"; then
    log "Defina a senha Samba do usuário $SAMBA_USER:"
    smbpasswd -a "$SAMBA_USER"
  fi

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
    for category_share in "${PUBLIC_SHARES[@]}" "${GROUP_SHARES[@]}"; do
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
  [[ "$NTP_ENABLE" == "yes" ]] && "$SBIN_DIR/ufw" allow from "$LAN_CIDR" to any port 123 proto udp comment 'NTP'

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
    --samba)     SELECTED_STEPS+=(samba) ;;
    --discovery) SELECTED_STEPS+=(discovery) ;;
    --ntp)       SELECTED_STEPS+=(ntp) ;;
    --firewall)  SELECTED_STEPS+=(firewall) ;;
    -h|--help)   show_help; exit 0 ;;
    *)           die "Opção desconhecida: $1. Use --help para ver as opções." ;;
  esac
  shift
done

if [[ "$RUN_ALL" != "yes" && ${#SELECTED_STEPS[@]} -eq 0 ]]; then
  die "Informe ao menos uma etapa ou use --all."
fi

[[ $EUID -eq 0 ]] || die "Execute como root."

NEEDS_LAN="no"
if [[ "$RUN_ALL" == "yes" ]]; then
  NEEDS_LAN="yes"
else
  for step in "${SELECTED_STEPS[@]}"; do
    case "$step" in
      samba|ntp|firewall) NEEDS_LAN="yes" ;;
    esac
  done
fi
if [[ "$NEEDS_LAN" == "yes" ]]; then
  [[ -n "$LAN_CIDR" ]] || LAN_CIDR=$(detect_lan_cidr)
  log "Rede local: $LAN_CIDR"
fi

if [[ "$RUN_ALL" == "yes" ]]; then
  SELECTED_STEPS=(install raid storage samba discovery ntp firewall)
fi

for step in "${SELECTED_STEPS[@]}"; do
  case "$step" in
    install)   install_packages ;;
    raid)      setup_raid ;;
    storage)   setup_storage ;;
    samba)     setup_samba ;;
    discovery) detect_wsdd_package; setup_discovery ;;
    ntp)       setup_ntp ;;
    firewall)  setup_firewall ;;
  esac
done

if [[ "$RUN_ALL" == "yes" ]]; then
  log "NAS configurado. Acesse: \\\\$(hostname)\\ (Windows) ou smb://$(hostname).local (Linux)."
fi

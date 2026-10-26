# Marcelino NAS e Cliente

[English](README.md) | [Português (Brasil)](README.pt-BR.md)

Este projeto reúne o script de configuração de um NAS Debian, um agente que publica estatísticas do servidor em um compartilhamento Samba e o aplicativo Windows `marcelino Client`.
Uma versão alternativa multiplataforma em .NET do Marcelino Client está disponível para Windows em [MarcelinoClientDotNet](https://github.com/marceloh220/MarcelinoClientDotNet)


## Componentes

| Componente | Arquivo | Função |
|---|---|---|
| Configuração do NAS | `nas-setup.sh` | Prepara IP estático, armazenamento, Samba, descoberta de rede, NTP, DNS/DHCP com bloqueio de anúncios/rastreadores e firewall. |
| Teste de velocidade | `nas-speedtest.py` | Mede download, upload e ping; registra resultados em JSONL, lista testes e gera gráficos. |
| Agente de estatísticas | `marcelinoServer.py` | Publica métricas do servidor, estado dos arrays RAID e regras de compartilhamento Samba em JSON. |
| Serviço systemd | `marcelinoServer.service` | Executa o agente após a inicialização do sistema. |
| Instalador do agente | `install_marcelinoServer.sh` | Atalho compatível para `nas-setup.sh --marcelino-server`. |
| Dashboard web | `marcelinoWeb.py`, `marcelinoWeb.service` | Servidor web com login que exibe as métricas publicadas pelo agente, incluindo estado e sincronização do RAID, acessível pela rede local. |
| Cliente Windows | `marcelinoClient.py` | Interface para métricas do servidor, discos e RAID, compartilhamentos, backup/restauração e terminal SSH. |
| Prévia do cliente | `preview_marcelinoClient.py` | Abre uma aba com dados genéricos locais, sem conexão com servidor. |
| Instalador do cliente | `install_marcelinoClient.ps1` | Instala dependências, gera o executável e cria atalhos. |

## Configurar o NAS

O `nas-setup.sh` destina-se a Debian e deve ser executado como root. Consulte as opções antes de escolher as etapas:

```bash
sudo bash nas-setup.sh --help
```

Cada opção de serviço instala suas dependências, configura o serviço e abre somente suas portas no UFW. As opções podem ser executadas separadamente ou combinadas:

| Opção | O que configura |
|---|---|
| `--network` | Configura o IP estático `NAS_IP`/`NAS_PREFIX` na interface (NetworkManager ou `/etc/network/interfaces`, com backup em `interfaces.orig`). A conexão SSH atual pode cair; reconecte no novo IP. |
| `--raid` | Cria o RAID com `mdadm` e atualiza a configuração de boot. |
| `--storage` | Formata e monta o volume; pode configurar criptografia LUKS e desbloqueio automático. |
| `--user` | Cria os cadastros ausentes do usuário principal (`USER_NAME`, senha `USER_PASSWORD`) no sistema e no Samba; preserva o home e as senhas de contas existentes e garante associação ao grupo Samba. Também é executado pelas etapas Samba, dashboard e teste de velocidade. |
| `--samba` / `--uninstall-samba` | Instala/configura Samba e libera suas portas; a desinstalação para o serviço e remove o pacote, mantendo shares e arquivos. |
| `--discovery` / `--uninstall-discovery` | Configura/remove Avahi e WS-Discovery, com suas portas de firewall. |
| `--ntp` / `--uninstall-ntp` | Configura/remove chrony como servidor de horário da LAN, com a porta NTP. |
| `--dns` / `--uninstall-dns` | Configura/remove dnsmasq para DNS e DHCP da LAN (veja abaixo), com restauração do backup de `resolv.conf`. |
| `--speedtest` / `--uninstall-speedtest` | Instala/remove o comando e os timers de teste/gráfico; logs e gráficos existentes são preservados. |
| `--marcelino-server` / `--uninstall-marcelino-server` | Instala/remove o agente de métricas, sem abrir portas; configuração e JSON existentes são preservados na remoção. |
| `--web` / `--uninstall-web` | Instala/remove o dashboard e sua regra de firewall; usuários, certificados e configuração persistente são preservados. |
| `--anytype` / `--uninstall-anytype` | Configura/remove o serviço Anytype em Docker e seu timer de backup diário; dados e backups são preservados e Docker só é desabilitado/removido se não houver outros contêineres ativos. |
| `--syncthing` / `--uninstall-syncthing` | Instala/remove Syncthing como serviço do usuário principal, com portas de sincronização na LAN; configuração, identidade e pastas sincronizadas são preservadas. |
| `--ssh` / `--uninstall-ssh` | Instala/configura ou remove o servidor SSH, configurações gerenciadas e regra da porta `SSH_PORT`. A desinstalação encerra o acesso SSH remoto. |
| `--dlna` / `--uninstall-dlna` | Instala/remove MiniDLNA e abre/fecha SSDP e a porta `DLNA_PORT`; mídia, banco/cache e logs são preservados. |
| `--all` | Configura rede, RAID, armazenamento, usuário e todos os serviços, incluindo SSH, Syncthing e DLNA. Não executa desinstalações. |

As opções de desinstalação aceitam também a grafia `--unistall-*` por compatibilidade. Não há opção para desinstalar RAID ou armazenamento: nenhuma opção de remoção apaga partições, altera LUKS, desmonta volumes ou desativa arrays RAID. O firewall não é redefinido; somente as regras da etapa selecionada são alteradas.

As opções são editadas na seção **CONFIGURAÇÃO** no começo do script. Os valores fornecidos incluem:

- RAID espelhado nível 1 com `/dev/sdb` e `/dev/sdc`, criado como `/dev/md0`.
- Sistema de arquivos configurável em `RAID_FILESYSTEM`: `ext4` (padrão), `xfs` ou `btrfs`.
- Volume em `/srv/nas`; LUKS habilitado sobre `/dev/md0`, com mapeamento `nas_crypt`, keyfile `/etc/nas.key` e desbloqueio automático.
- Aplicativos em `APP_STORAGE` (padrão `/srv/app`): Monitor, Anytype, configuração do Syncthing e banco/cache DLNA. Esse caminho não fica automaticamente no RAID nem dentro do LUKS; configure uma montagem própria ou use um caminho dentro do volume se precisar dessa proteção.
- Shares `Publico`, `Privado`, `Backup`, `Midia`, `Monitor` e o share do usuário principal (`USER_NAME` com a primeira letra maiúscula).
- `Publico` permite convidados; `Backup` e `Midia` usam o grupo `nasgroup`; `Privado` e o share do usuário são restritos a `USER_NAME`.
- Usuário principal: `USER_NAME` e `USER_PASSWORD` (troque o valor `sua_senha_aqui`; mínimo 8 caracteres para novos cadastros). Se `USER_PASSWORD=""`, a senha será solicitada interativamente no terminal, sem exibir os caracteres digitados, quando necessária para cadastrar contas ou configurar o login web. Em execuções não interativas, preencha a variável previamente. Contas Linux e Samba existentes mantêm suas senhas; se ambas já existirem, a etapa de usuário não exige uma nova senha. O dashboard usa `USER_PASSWORD` separadamente para o login web. Se preenchida no script, a senha fica em texto puro; restrinja o acesso ao arquivo.
- Grupo de trabalho `WORKGROUP`, domínio `lan` (`LOCAL_DOMAIN`), SSH na porta 22 e chrony habilitado.
- Rede: `IP_INET`, `IP_GATEWAY`, `IP_RANGE_START`, `IP_RANGE_END`, `DNS_UPSTREAM` e `LOCAL_DOMAIN` ficam no topo do script e alimentam `NAS_IP`, `NET_GATEWAY`, a faixa DHCP e o domínio. Ajuste-os para a sua rede (o NAS, o gateway e a faixa DHCP devem estar na mesma sub-rede).

### Anytype: sync host local

A etapa `--anytype` usa o projeto comunitário [any-sync-bundle](https://github.com/grishy/any-sync-bundle), não a stack oficial completa do Anytype. O contêiner reúne os serviços de sincronização e armazena os dados no NAS. A imagem all-in-one exige uma CPU compatível com seu MongoDB (AVX em x86_64).

Revise `ANYTYPE_ENABLE` (padrão `yes`), `ANYTYPE_IMAGE` (versão fixada), `ANYTYPE_DATA` (padrão `/srv/app/anytype`), `ANYTYPE_BACKUP` (padrão `/srv/nas/backup/anytype`) e `ANYTYPE_HOST` (padrão `NAS_IP`). O destino do backup não pode ficar dentro de `ANYTYPE_DATA`. Monte o armazenamento antes de configurar o serviço:

```bash
sudo bash nas-setup.sh --anytype
sudo systemctl status nas-anytype.service
sudo journalctl -u nas-anytype.service -n 50 --no-pager
```

O serviço systemd inicia o contêiner com a rede do host, sem publicar portas via NAT do Docker; assim o UFW controla o acesso. Somente `LAN_CIDR` recebe acesso a **33010/tcp** (sync) e **33020/udp** (QUIC). Não é necessário abrir portas no roteador para uso na LAN.

Após a primeira inicialização, transfira com segurança `/srv/app/anytype/client-config.yml` para cada dispositivo e importe-o em **Network > Self-hosted** no Anytype. Se alterar `ANYTYPE_DATA`, use o caminho correspondente. O diretório é restrito ao root; não compartilhe `bundle-config.yml`, pois contém chaves privadas. Recomenda-se usar uma identidade dedicada e exportar/importar os dados ao migrar de outra rede.

`ANYTYPE_HOST` define o endereço anunciado apenas no primeiro início. Para mudar esse endereço posteriormente, edite `externalAddr` em `bundle-config.yml`, reinicie `nas-anytype.service` e importe novamente o `client-config.yml` gerado nos dispositivos.

O timer `nas-anytype-backup.timer` faz backup diário à meia-noite (horário local) de todo `ANYTYPE_DATA`, incluindo `bundle-config.yml`, em arquivos `anytype-*.tar.gz` dentro de `ANYTYPE_BACKUP`, restritos ao root. O serviço é parado durante a compactação e reiniciado ao final, inclusive em caso de falha; se já estiver parado, permanece assim. Backups interrompidos não são publicados como arquivos completos. Com `Persistent=true`, uma execução perdida é disparada quando o timer voltar a ficar ativo. Os arquivos são acumulados sem exclusão automática; monitore o espaço disponível.

Para executar um backup manualmente, use `sudo systemctl start nas-anytype-backup.service`. Consulte o agendamento com `systemctl list-timers nas-anytype-backup.timer` e os logs com `sudo journalctl -u nas-anytype-backup.service`. O uninstall remove o timer, o serviço de backup e o script auxiliar, preservando os dados e os backups existentes.

Não apague `ANYTYPE_DATA` ao atualizar a imagem. Definir `ANYTYPE_ENABLE=no` apenas pula a configuração futura; para parar uma instalação existente e seu agendamento, execute também `sudo systemctl disable --now nas-anytype-backup.timer nas-anytype.service`.

### Syncthing

`--syncthing` instala o pacote dos repositórios Debian e configura `nas-syncthing.service` para iniciar no boot como `USER_NAME`, sem executar como root. A etapa também executa `setup_user`, criando os cadastros Linux/Samba ausentes. Ajuste `SYNCTHING_ENABLE` (padrão `yes`) e `SYNCTHING_HOME` (padrão `/srv/app/syncthing`, para configuração, identidade e índices). Monte o armazenamento antes da instalação.

```bash
sudo bash nas-setup.sh --syncthing
sudo systemctl status nas-syncthing.service
sudo journalctl -u nas-syncthing.service -n 50 --no-pager
```

O painel fica restrito a `127.0.0.1:8384`, sem regra de firewall para acesso direto pela LAN. No seu computador, abra um túnel (ajuste usuário, IP e porta SSH conforme sua configuração):

```bash
ssh -p 22 -L 8384:127.0.0.1:8384 meneceu@192.168.0.1
```

Com o túnel aberto, acesse `http://127.0.0.1:8384`, defina usuário/senha do painel e adicione os dispositivos e pastas. A pasta padrão é `Sync` no home do usuário; outras pastas precisam permitir acesso a `USER_NAME`. Evite compartilhar a pasta `SYNCTHING_HOME`, que contém chaves e configuração. O UFW libera somente para `LAN_CIDR` as portas padrão **22000/tcp**, **22000/udp** (QUIC) e **21027/udp** (descoberta local); se mudar as portas pelo painel, ajuste também o firewall. Descoberta global, relays e mapeamento automático de portas seguem os padrões do Syncthing; desative-os no painel para uso exclusivamente local. Sincronização pode propagar exclusões e não substitui backup.

Para remover, execute `sudo bash nas-setup.sh --uninstall-syncthing`. O serviço, pacote e regras são removidos, mas a conta, configuração, identidade, índices e arquivos sincronizados permanecem. Reinstale com o mesmo `USER_NAME` e `SYNCTHING_HOME` para reutilizar a identidade. `SYNCTHING_ENABLE=no` apenas pula instalações futuras, sem parar uma instalação existente.

### DLNA

`--dlna` instala MiniDLNA e exige que o volume de armazenamento já esteja montado. Ajuste `DLNA_ENABLE`, `DLNA_PORT`, `DLNA_MEDIA_MUSIC`, `DLNA_MEDIA_VIDEO`, `DLNA_MEDIA_PICTURES`, `DLNA_CACHE` e `DLNA_LOG` no início de `nas-setup.sh`. A regra do UFW é criada junto com o serviço; para remover MiniDLNA e fechar as portas, execute `sudo bash nas-setup.sh --uninstall-dlna` (ou `--unistall-dlna`). A remoção mantém as mídias, o banco/cache e os logs.

### Teste de velocidade da internet

A etapa `--speedtest` (`SPEEDTEST_ENABLE`) instala `speedtest-cli` e `python3-matplotlib`, copia `nas-speedtest.py` para `/usr/local/bin/nas-speedtest` e ativa dois timers:

- `nas-speedtest.timer`: um teste de hora em hora, gravado em `SPEEDTEST_LOG` (`/var/log/nas-speedtest.jsonl`).
- `nas-speedtest-graph.timer`: todo dia às `SPEEDTEST_GRAPH_TIME` (23:55) gera o gráfico das últimas 24 horas em `SPEEDTEST_OUT/ANO/MES/` (padrão `/srv/marcelo/internet_test`), a pasta é escolhida pela data do gráfico.

Uso manual:

```bash
sudo nas-speedtest test              # teste novo, registrado no log
nas-speedtest log -n 10              # últimos 10 testes
nas-speedtest --log /caminho/log.jsonl log -n 10  # usa outro arquivo de log
sudo nas-speedtest graph -n 24       # gráfico dos últimos 24 testes
sudo nas-speedtest graph --hours 24  # gráfico das últimas 24 horas
```

No subcomando `log`, use `-n` para definir a quantidade de testes. A opção global `--log` indica o caminho do arquivo JSON; por isso, `nas-speedtest --log 10 log` procura um arquivo chamado `10` e pode informar que não há testes registrados.

Os gráficos incluem grades major e minor, com estilos distintos para facilitar a leitura.

#### Gerenciar timers

Consulte o estado dos timers e os próximos horários programados:

```bash
systemctl list-timers --all 'nas-speedtest*'
sudo systemctl status nas-speedtest.timer nas-speedtest-graph.timer
```

Para executar cada tarefa imediatamente, sem esperar pelo próximo horário:

```bash
sudo systemctl start nas-speedtest.service        # executa um teste e atualiza o log
sudo systemctl start nas-speedtest-graph.service  # gera o gráfico das últimas 24 horas
```

Para pausar os agendamentos e impedir que voltem após reiniciar, ou para reativá-los:

```bash
sudo systemctl disable --now nas-speedtest.timer nas-speedtest-graph.timer
sudo systemctl enable --now nas-speedtest.timer nas-speedtest-graph.timer
```

Consulte a saída das últimas execuções pelo journal do systemd:

```bash
sudo journalctl -u nas-speedtest.service -n 50 --no-pager
sudo journalctl -u nas-speedtest-graph.service -n 50 --no-pager
```

Os PNG são nomeados `speedtest_AAAA-MM-DD_<periodo>.png` (por exemplo `speedtest_2026-10-15_24h.png`), com a data do último resultado incluído, e herdam o dono da pasta base.

### IP estático, DNS e DHCP

- **IP estático (`--network`):** controlado por `NET_STATIC`, `NAS_IP`, `NAS_PREFIX`, `NET_IFACE` e `NET_GATEWAY`. Interface e gateway vazios são detectados pela rota padrão. Se `LAN_CIDR` estiver vazio, é derivado de `NAS_IP/NAS_PREFIX`.
- **DNS (`--dns`, `DNS_ENABLE`):** dnsmasq com cache, resolvedores externos em `DNS_UPSTREAM` e domínio local `DNS_DOMAIN`. Clientes DHCP são registrados por nome (`pc1` e `pc1.lan`) e o próprio NAS responde pelo `NAS_IP`. Máquinas com IP fixo precisam de entrada no `/etc/hosts` do NAS ou de `dhcp-host=` em `/etc/dnsmasq.d/nas.conf`; o script mantém uma cópia filtrada em `/var/lib/nas-dns/hosts`, removendo entradas loopback (`127.x.x.x` e `::1`) antes de servi-las na LAN. Reexecute `sudo bash nas-setup.sh --dns` após alterar `/etc/hosts`. O `systemd-resolved` é desativado e o `/etc/resolv.conf` do NAS aponta para `127.0.0.1`. A configuração ativa `log-queries=extra` e `log-dhcp`; as listas de acessos liberados/bloqueados exibem os últimos eventos da última hora. O agente guarda os eventos classificados em `/var/log/marcelinoServer/dnsmasq-access.jsonl`, com rotação de 5 MB e três cópias, e o journal original pode ser consultado com `journalctl -u dnsmasq`.
- **Bloqueio de anúncios, rastreadores e malware:** as listas em `DNS_BLOCKLISTS` (padrão: StevenBlack, HaGeZi Pro e HaGeZi TIF medium) são consolidadas em `/var/lib/nas-dns/blocklist.hosts` por `/usr/local/sbin/update-dns-blocklist`, executado na instalação e semanalmente pelo timer `nas-dns-blocklist.timer`. São aceitos os formatos hosts e dnsmasq (`local=/dominio/`).
- **DHCP (`DHCP_ENABLE`):** faixa `DHCP_RANGE_START`–`DHCP_RANGE_END` com concessão `DHCP_LEASE`; entrega gateway, o NAS como DNS e, com NTP ativo, o NAS como servidor de horário. Desative o DHCP do roteador antes para evitar dois servidores na rede.

Para escolher o formato, altere `RAID_FILESYSTEM` na seção **CONFIGURAÇÃO** antes de executar, por exemplo:

```bash
RAID_FILESYSTEM="xfs"
```

Antes de configurar RAID ou storage, o script verifica o ponto de montagem configurado (`CRYPT_MOUNTPOINT` com LUKS, ou `RAID_MOUNTPOINT` sem LUKS). Se ele estiver montado ou registrado no `/etc/fstab`, ambas as etapas são puladas, inclusive com `--all`, sem alterar discos, keyfile, `fstab` ou `crypttab`. A detecção considera o destino de montagem, não valida a integridade do volume nem sua correspondência com os discos configurados. Se estiver desmontado ou com LUKS fechado, abra e monte o volume antes de executar os serviços que precisam dos dados. O RAID também é preservado quando `RAID_DEVICE` já existe.

Quando não há montagem nem entrada existente no `fstab`, sem LUKS o formato é aplicado diretamente em `RAID_DEVICE`. Com LUKS, o contêiner criptografado fica em `CRYPT_DEVICE` e o sistema de arquivos é criado no volume aberto `/dev/mapper/$CRYPT_NAME`. O tipo escolhido também é gravado no `/etc/fstab`. Se o volume já tiver o mesmo sistema de arquivos, o script o preserva; se encontrar um formato diferente, interrompe a etapa para evitar uma montagem incorreta. Se não encontrar um sistema de arquivos, cria o escolhido.

**Atenção:** revise `RAID_DEVICES`, `RAID_DEVICE`, `RAID_FILESYSTEM`, `CRYPT_DEVICE` e as opções LUKS antes de executar. A criação do RAID apaga assinaturas dos discos selecionados após confirmação. A configuração LUKS e a criação do sistema de arquivos podem destruir dados existentes; mantenha backups. A etapa `--samba` substitui `/etc/samba/smb.conf`; guarda uma cópia inicial em `/etc/samba/smb.conf.orig` se ela ainda não existir.

Exemplo para configurar o NAS completo depois de revisar os valores:

```bash
sudo bash nas-setup.sh --all
```

Para rodar somente etapas específicas, por exemplo Samba e descoberta:

```bash
sudo bash nas-setup.sh --samba --discovery
```

## Serviço de estatísticas

O agente `marcelinoServer.py` coleta carga, frequência e temperatura da CPU, memória, tempo ativo, rede, informações do sistema, sistemas de arquivos montados, discos físicos/SMART, estado e atividade dos arrays RAID via `/proc/mdstat`, configuração dos compartilhamentos Samba e estado/atividade DNS e DHCP. Frequência e temperatura são opcionais: aparecem indisponíveis se o hardware ou o sistema não expuserem esses dados. O JSON padrão chama-se `.marcelinoSrv.json`.

O instalador deve ser executado no servidor Debian com systemd. Mantenha `marcelinoServer.py` e `marcelinoServer.service` junto do script:

```bash
sudo bash nas-setup.sh --marcelino-server
```

Ele instala `python3`, `python3-psutil` e `smartmontools`, copia os arquivos para `/opt/marcelinoServer`, instala a unidade em `/etc/systemd/system/marcelinoServer.service`, atualiza `/etc/default/marcelinoServer`, habilita a inicialização no boot e reinicia o serviço. `MARCELINOSRV_STATUS` usa `WEB_STATUS` (padrão `/srv/app/monitor/.marcelinoSrv.json`), compartilhando o destino com o dashboard e o share Monitor. Reinstalações atualizam os caminhos do script e do JSON e preservam as demais variáveis. Não é necessário configurar rede, Samba ou firewall para instalar apenas o agente. `--all` também instala o agente antes do dashboard; o instalador antigo permanece como atalho.

Para remover, use `sudo bash nas-setup.sh --uninstall-marcelino-server` (ou `--unistall-marcelino-server`). A remoção desativa o serviço e remove a unidade e o script instalado, preservando `/etc/default/marcelinoServer`, métricas e dependências compartilhadas.

### Configuração do serviço

A unidade lê suas variáveis de:

```text
/etc/default/marcelinoServer
```

Variáveis utilizadas pela unidade:

| Variável | Finalidade | Padrão do instalador |
|---|---|---|
| `MARCELINOSRV_SCRIPT` | Caminho do agente Python. | `/opt/marcelinoServer/marcelinoServer.py` |
| `MARCELINOSRV_STATUS` | Caminho completo do JSON publicado no share Monitor. | `WEB_STATUS`, padrão `/srv/app/monitor/.marcelinoSrv.json`. |
| `MARCELINOSRV_INTERVAL` | Intervalo entre coletas, em segundos. | `5` |
| `MARCELINOSRV_FILE_MODE` | Permissões Unix do JSON, em octal. | `0644` |

O pacote `smartmontools` fornece `smartctl` para métricas SMART, `pciutils` fornece `lspci` para identificar GPUs e `fontconfig` fornece `fc-match` para detectar a fonte padrão. O agente continua funcionando sem esses comandos, mas as informações correspondentes ficam indisponíveis. `python3-psutil` é necessário para coletar as métricas do sistema.

O agente também aceita `--smb-config`, `--public-share`, `--output`, `--interval` e `--file-mode` quando executado manualmente. A unidade systemd usa `--output`, `--interval` e `--file-mode`, obtidos do arquivo de ambiente. Se alterar o arquivo manualmente, reinicie a unidade para aplicar os valores.

Comandos usuais de operação:

```bash
sudo systemctl status marcelinoServer.service
sudo systemctl start marcelinoServer.service
sudo systemctl restart marcelinoServer.service
sudo systemctl stop marcelinoServer.service
sudo journalctl -u marcelinoServer.service -f
```

O serviço é executado como `root`, aguarda a rede e `smbd` e publica o arquivo atomicamente. Com `Restart=on-failure` e `RestartSec=5`, o systemd tenta reiniciar o agente cinco segundos após uma falha. O acesso ao arquivo também depende das permissões do diretório/share Samba.

## Dashboard web

O `marcelinoWeb.py` (somente biblioteca padrão do Python) lê o JSON publicado pelo `marcelinoServer` e o exibe em uma página com atualização a cada 5 s: sistema, CPU e memória, rede e internet, armazenamento, estado e sincronização do RAID, discos SMART, shares Samba, DNS/DHCP, leases e consultas bloqueadas. O acesso exige login.

Instalação (`marcelinoWeb.py` e `marcelinoWeb.service` devem estar na pasta do `nas-setup.sh`):

```bash
sudo bash nas-setup.sh --web
```

Variáveis `WEB_*` no topo do `nas-setup.sh`:

| Variável | Finalidade |
|---|---|
| `WEB_ENABLE` | `yes` instala o dashboard. |
| `WEB_PORT` | Porta do serviço (portas abaixo de 1024 funcionam via `CAP_NET_BIND_SERVICE`). |
| `WEB_STATUS` | JSON do `marcelinoServer` (padrão `/srv/app/monitor/.marcelinoSrv.json`), também usado ao instalar o agente. |
| `WEB_USER` | Usuário de sistema sem privilégios que executa o serviço. |
| `WEB_ADMIN` | Usuário web inicial; padrão `USER_NAME`, com a senha `USER_PASSWORD`. Vazio = cadastre depois. |
| `WEB_TLS` | `yes` gera certificado autoassinado e serve HTTPS; o navegador mostra um aviso na primeira visita. |

Com `WEB_TLS=yes` a porta serve apenas HTTPS; acesse `https://IP_DO_NAS:PORTA` (por exemplo `https://192.168.100.1:443`). O firewall libera a porta somente para `LAN_CIDR` quando `--web` é executado.

### Usuários

```bash
sudo marcelinoWeb useradd NOME   # senha mínima de 8 caracteres
sudo marcelinoWeb passwd NOME
sudo marcelinoWeb userdel NOME
sudo marcelinoWeb users
```

Os usuários ficam em `/etc/marcelinoWeb/users.json` (modo 600, senhas com PBKDF2-SHA256). As sessões duram 8 h em cookie `HttpOnly`/`SameSite=Strict`, e 5 falhas de login seguidas bloqueiam o IP por 5 min. Configuração do serviço em `/etc/default/marcelinoWeb`.

```bash
sudo systemctl status marcelinoWeb.service
sudo journalctl -u marcelinoWeb.service -n 50 --no-pager
```

## Instalar o cliente Windows

O instalador cria um ambiente Python temporário, instala os pacotes de `requirements.txt`, empacota `marcelinoClient.py` com PyInstaller e cria atalhos no Desktop e Menu Iniciar. Execute no PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process RemoteSigned
.\install_marcelinoClient.ps1
```

O executável é instalado em `%LOCALAPPDATA%\Programs\MarcelinoClient\MarcelinoClient.exe`. As principais dependências incluem CustomTkinter, `smbprotocol`, Paramiko e `pyte`.

## Telas e funcionalidades do cliente

O cliente inicia na tela de login. Depois de autenticar no servidor SMB, abre a área principal, onde as abas compartilham a mesma sessão. O cabeçalho mostra usuário e servidor; **Desconectar** encerra a sessão SMB e SSH e retorna ao login.

### Prévia local

Para inspecionar uma aba sem autenticar ou conectar a um servidor, execute `preview_marcelinoClient.py` no ambiente Python do projeto e informe a tela desejada:

```powershell
.\.venv\Scripts\python.exe preview_marcelinoClient.py --screen Sistema
.\.venv\Scripts\python.exe preview_marcelinoClient.py --screen "DNS / DHCP"
```

As opções de `--screen` são `Sistema`, `Discos`, `RAID`, `Internet`, `DNS / DHCP`, `Compartilhamentos`, `Backup` e `Terminal`. O preview usa dados genéricos locais; a tela RAID mostra um array de demonstração em recuperação, e Backup e Terminal também mostram exemplos sem iniciar transferências nem conexão SSH.

### Login

Selecione um servidor da lista, informe usuário e senha Samba e escolha **Validar e conectar**. **Procurar na rede** procura servidores SMB na rede local e atualiza as opções disponíveis.

![Tela de login do cliente](assets/01_tela_login.png)

### Sistema

Mostra carga, frequência e temperatura da CPU, uso de memória, tempo ativo, tráfego de rede e identificação de sistema/hardware. Frequência e temperatura aparecem como `n/d` quando indisponíveis; uptime e tráfego de rede são apresentados em painéis compactos. O intervalo pode ser escolhido entre 1 e 300 segundos; a escolha é salva e restaurada na próxima abertura. **Atualizar agora** solicita uma nova leitura.

![Painel de sistema](assets/02_tela_sistema.png)

### Discos

Apresenta barras de uso dos sistemas de arquivos montados e uma tabela dos discos físicos com modelo, capacidade, temperatura e status SMART. Os dados SMART dependem do `smartmontools`/`smartctl` no servidor.

![Sistemas de arquivos e discos SMART](assets/03_tela_discos.png)

### RAID

Mostra o estado geral dos arrays RAID detectados pelo agente e, para cada array, dispositivo, nível, estado, quantidade de discos ativos e atividade de sincronização. O campo **Atividade** indica operações em andamento, como `resync`, `recovery`, `reshape` ou `check`, com o percentual informado pelo sistema; `--` significa que nenhuma atividade com progresso foi reportada. Os dados vêm de `/proc/mdstat` no servidor Linux e exigem uma versão do agente que publique o campo `raid` no JSON.

### Internet

Mostra o IP local e remoto, além de testes de velocidade de download, upload e ping. Os resultados são apresentados em uma tabela com timestamp, taxa de download, taxa de upload, ping e servidor utilizado para o teste.

![Teste de velocidade de internet](assets/04_tela_internet.png)

### DNS / DHCP

Exibe o estado do serviço DNS/DHCP, incluindo se está ativo, se o DNS e o DHCP estão habilitados, a contagem de leases e os eventos permitidos e bloqueados. Mostra também os leases atuais com IP, hostname, MAC e tempo de expiração.

![Configurações de DNS e DHCP](assets/05_tela_dns_dhcp.png)

### Compartilhamentos

Lista os shares publicados pelo Samba, caminho e regras configuradas: usuários/grupos permitidos, listas de leitura e escrita, administradores, bloqueados, acesso de convidados e modo somente leitura. O agente extrai essas diretivas do `smb.conf` e as publica no JSON de estatísticas; grupos são exibidos como configurados, por exemplo `@nasgroup`.

![Compartilhamentos Samba e regras de acesso](assets/06_tela_compartilhamentos.png)

### Backup

Navegue pelos compartilhamentos acessíveis para escolher o destino, adicione pastas locais de origem, inicie o backup e acompanhe o progresso e o log. Arquivos que já estão atualizados no destino são ignorados; o botão **Cancelar** interrompe a operação.

![Tela de backup](assets/07_tela_backup.png)

### Terminal

Conecta por SSH ao servidor e usuário da sessão autenticada. Permite usar senha SMB como autenticação e selecionar um arquivo de chave privada, com passphrase opcional. A primeira conexão com um host desconhecido pede confirmação digitando `yes` e salva a chave em `~/.ssh/known_hosts`. O terminal emula ANSI e aceita entrada interativa para comandos e aplicações TUI, como `btop`.

![Terminal SSH](assets/08_tela_terminal.png)

### SSH Conectado

Mostra o estado da conexão SSH ativa com o servidor, incluindo o usuário conectado e o host. Permite interagir com o servidor através de um terminal SSH em tempo real.

![Terminal SSH](assets/09_tela_sshconectado.png)

## Fluxo de dados

```text
marcelinoServer.py
    -> coleta métricas, estado RAID e diretivas de smb.conf
    -> publica .marcelinoSrv.json no share público
    -> cliente SMB lê e apresenta os dados nas abas Sistema, Discos, RAID e Compartilhamentos

marcelino Client
    -> acessa arquivos e destinos por SMB
    -> envia backups para o compartilhamento escolhido
    -> abre um terminal interativo SSH para o mesmo servidor
```

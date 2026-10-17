# Marcelino NAS e Cliente

Este projeto reúne o script de configuração de um NAS Debian, um agente que publica estatísticas do servidor em um compartilhamento Samba e o aplicativo Windows `marcelino Client`.

## Componentes

| Componente | Arquivo | Função |
|---|---|---|
| Configuração do NAS | `nas-setup.sh` | Prepara IP estático, armazenamento, Samba, descoberta de rede, NTP, DNS/DHCP com bloqueio de anúncios/rastreadores e firewall. |
| Teste de velocidade | `nas-speedtest.py` | Mede download, upload e ping; registra resultados em JSONL, lista testes e gera gráficos. |
| Agente de estatísticas | `marcelinoServer.py` | Publica métricas do servidor e regras de compartilhamento Samba em JSON. |
| Serviço systemd | `marcelinoServer.service` | Executa o agente após a inicialização do sistema. |
| Instalador do agente | `install_marcelinoServer.sh` | Instala o agente, configura o arquivo de ambiente e inicia o serviço. |
| Dashboard web | `marcelinoWeb.py`, `marcelinoWeb.service` | Servidor web com login que exibe as métricas publicadas pelo agente, acessível pela rede local. |
| Cliente Windows | `marcelinoClient.py` | Interface para estatísticas, compartilhamentos, backup/restauração e terminal SSH. |
| Prévia do cliente | `preview_marcelinoClient.py` | Abre uma aba com dados genéricos locais, sem conexão com servidor. |
| Instalador do cliente | `install_marcelinoClient.ps1` | Instala dependências, gera o executável e cria atalhos. |

## Configurar o NAS

O `nas-setup.sh` destina-se a Debian e deve ser executado como root. Consulte as opções antes de escolher as etapas:

```bash
sudo bash nas-setup.sh --help
```

As etapas podem ser executadas separadamente ou combinadas:

| Opção | O que configura |
|---|---|
| `--install` | Instala mdadm, Samba, Avahi, UFW, `python3-psutil`, `smartmontools`, `pciutils`, `fontconfig`, utilitários de armazenamento e WS-Discovery; instala `xfsprogs` ou `btrfs-progs` conforme `RAID_FILESYSTEM`, além de cryptsetup, chrony, `dnsmasq` e `curl` conforme as opções configuradas. |
| `--network` | Configura o IP estático `NAS_IP`/`NAS_PREFIX` na interface (NetworkManager ou `/etc/network/interfaces`, com backup em `interfaces.orig`). A conexão SSH atual pode cair; reconecte no novo IP. |
| `--raid` | Cria o RAID com `mdadm` e atualiza a configuração de boot. |
| `--storage` | Formata e monta o volume; pode configurar criptografia LUKS e desbloqueio automático. |
| `--user` | Cria o usuário principal (`USER_NAME`, senha `USER_PASSWORD`) no sistema e no Samba; também é executado pelas etapas Samba, dashboard e teste de velocidade. |
| `--samba` | Cria usuário/grupo, diretórios, permissões e shares; valida a configuração com `testparm` e reinicia Samba. |
| `--discovery` | Ativa anúncio SMB por Avahi/mDNS e WS-Discovery quando disponível. |
| `--ntp` | Configura o chrony como servidor de horário da LAN quando `NTP_ENABLE=yes`. |
| `--dns` | Configura o dnsmasq como DNS e DHCP da LAN (veja abaixo). |
| `--speedtest` | Instala `nas-speedtest` (a partir de `nas-speedtest.py`) e os timers de teste e gráfico (veja abaixo). |
| `--web` | Instala o dashboard web com login (veja abaixo). |
| `--firewall` | Configura UFW para SSH, Samba, descoberta, DNS (53), DHCP (67/udp) e, se habilitado, NTP e a porta do dashboard (`WEB_PORT`). A configuração redefine as regras atuais do UFW. |
| `--all` | Executa as etapas na ordem recomendada: instalação, rede, RAID, armazenamento, Samba, descoberta, NTP, DNS/DHCP, teste de velocidade, dashboard web e firewall. |

As opções são editadas na seção **CONFIGURAÇÃO** no começo do script. Os valores fornecidos incluem:

- RAID espelhado nível 1 com `/dev/sdb` e `/dev/sdc`, criado como `/dev/md0`.
- Sistema de arquivos configurável em `RAID_FILESYSTEM`: `ext4` (padrão), `xfs` ou `btrfs`.
- Volume em `/srv/nas`; LUKS habilitado sobre `/dev/md0`, com mapeamento `nas_crypt`, keyfile `/etc/nas.key` e desbloqueio automático.
- Shares `Publico`, `Privado`, `Backup`, `Midia`, `Monitor` e o share do usuário principal (`USER_NAME` com a primeira letra maiúscula).
- `Publico` permite convidados; `Backup` e `Midia` usam o grupo `nasgroup`; `Privado` e o share do usuário são restritos a `USER_NAME`.
- Usuário principal: `USER_NAME` e `USER_PASSWORD` (troque o valor `sua_senha_aqui`; mínimo 8 caracteres). A senha fica em texto puro no script; restrinja o acesso ao arquivo.
- Grupo de trabalho `WORKGROUP`, domínio `lan` (`LOCAL_DOMAIN`), SSH na porta 22 e chrony habilitado.
- Rede: `IP_INET`, `IP_GATEWAY`, `IP_RANGE_START`, `IP_RANGE_END`, `DNS_UPSTREAM` e `LOCAL_DOMAIN` ficam no topo do script e alimentam `NAS_IP`, `NET_GATEWAY`, a faixa DHCP e o domínio. Ajuste-os para a sua rede (o NAS, o gateway e a faixa DHCP devem estar na mesma sub-rede).

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

Sem LUKS, o formato é aplicado diretamente em `RAID_DEVICE`. Com LUKS, o contêiner criptografado fica em `CRYPT_DEVICE` e o sistema de arquivos é criado no volume aberto `/dev/mapper/$CRYPT_NAME`. O tipo escolhido também é gravado no `/etc/fstab`. Se o volume já tiver o mesmo sistema de arquivos, o script o preserva; se encontrar um formato diferente, interrompe a etapa para evitar uma montagem incorreta. Se não encontrar um sistema de arquivos, cria o escolhido.

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

O agente `marcelinoServer.py` coleta carga, frequência e temperatura da CPU, memória, tempo ativo, rede, informações do sistema, sistemas de arquivos montados, discos físicos/SMART, configuração dos compartilhamentos Samba e estado/atividade DNS e DHCP. Frequência e temperatura são opcionais: aparecem indisponíveis se o hardware ou o sistema não expuserem esses dados. O JSON padrão chama-se `.marcelinoSrv.json`.

O instalador deve ser executado no servidor Linux, depois de instalar/configurar o Samba:

```bash
sudo bash install_marcelinoServer.sh
```

Ele instala os arquivos em `/opt/marcelinoServer`, instala a unidade em `/etc/systemd/system/marcelinoServer.service`, atualiza `/etc/default/marcelinoServer`, habilita a inicialização no boot via `WantedBy=multi-user.target` e reinicia o serviço. O caminho de `MARCELINOSRV_STATUS` é calculado com base no caminho do share público encontrado em `/etc/samba/smb.conf` (`public`, `publico` ou `público`) e aponta para `.marcelinoSrv.json` na raiz desse share. Ao atualizar uma instalação existente, o instalador preserva as demais variáveis e substitui/adiciona apenas `MARCELINOSRV_STATUS`.

### Configuração do serviço

A unidade lê suas variáveis de:

```text
/etc/default/marcelinoServer
```

Variáveis utilizadas pela unidade:

| Variável | Finalidade | Padrão do instalador |
|---|---|---|
| `MARCELINOSRV_SCRIPT` | Caminho do agente Python. | `/opt/marcelinoServer/marcelinoServer.py` |
| `MARCELINOSRV_STATUS` | Caminho completo do JSON publicado no share público. | Calculado pelo instalador com base no `smb.conf`. |
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

O `marcelinoWeb.py` (somente biblioteca padrão do Python) lê o JSON publicado pelo `marcelinoServer` e o exibe em uma página com atualização a cada 5 s: sistema, CPU e memória, rede e internet, armazenamento, discos SMART, shares Samba, DNS/DHCP, leases e consultas bloqueadas. O acesso exige login.

Instalação (`marcelinoWeb.py` e `marcelinoWeb.service` devem estar na pasta do `nas-setup.sh`):

```bash
sudo bash nas-setup.sh --web --firewall
```

Variáveis `WEB_*` no topo do `nas-setup.sh`:

| Variável | Finalidade |
|---|---|
| `WEB_ENABLE` | `yes` instala o dashboard. |
| `WEB_PORT` | Porta do serviço (portas abaixo de 1024 funcionam via `CAP_NET_BIND_SERVICE`). |
| `WEB_STATUS` | JSON do `marcelinoServer` (padrão `/srv/nas/monitor/.marcelinoSrv.json`). |
| `WEB_USER` | Usuário de sistema sem privilégios que executa o serviço. |
| `WEB_ADMIN` | Usuário web inicial; padrão `USER_NAME`, com a senha `USER_PASSWORD`. Vazio = cadastre depois. |
| `WEB_TLS` | `yes` gera certificado autoassinado e serve HTTPS; o navegador mostra um aviso na primeira visita. |

Com `WEB_TLS=yes` a porta serve apenas HTTPS; acesse `https://IP_DO_NAS:PORTA` (por exemplo `https://192.168.100.1:80`). O firewall libera a porta somente para `LAN_CIDR`, mas apenas na etapa `--firewall`.

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

As opções de `--screen` são `Sistema`, `Discos`, `Internet`, `DNS / DHCP`, `Compartilhamentos`, `Backup` e `Terminal`. O preview usa dados genéricos locais; Backup e Terminal também mostram exemplos sem iniciar transferências nem conexão SSH.

### Login

Selecione um servidor da lista, informe usuário e senha Samba e escolha **Validar e conectar**. **Procurar na rede** procura servidores SMB na rede local e atualiza as opções disponíveis.

![Tela de login do cliente](assets/01_tela_login.png)

### Sistema

Mostra carga, frequência e temperatura da CPU, uso de memória, tempo ativo, tráfego de rede e identificação de sistema/hardware. Frequência e temperatura aparecem como `n/d` quando indisponíveis; uptime e tráfego de rede são apresentados em painéis compactos. O intervalo pode ser escolhido entre 1 e 300 segundos; a escolha é salva e restaurada na próxima abertura. **Atualizar agora** solicita uma nova leitura.

![Painel de sistema](assets/02_tela_sistema.png)

### Discos

Apresenta barras de uso dos sistemas de arquivos montados e uma tabela dos discos físicos com modelo, capacidade, temperatura e status SMART. Os dados SMART dependem do `smartmontools`/`smartctl` no servidor.

![Sistemas de arquivos e discos SMART](assets/03_tela_discos.png)

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
    -> coleta métricas e diretivas de smb.conf
    -> publica .marcelinoSrv.json no share público
    -> cliente SMB lê e apresenta os dados nas abas Sistema, Discos e Compartilhamentos

marcelino Client
    -> acessa arquivos e destinos por SMB
    -> envia backups para o compartilhamento escolhido
    -> abre um terminal interativo SSH para o mesmo servidor
```

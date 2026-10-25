#!/usr/bin/bash

install() {
    echo "Installing required packages, enter with sudo password..."
    sudo apt update && sudo apt upgrade -y
    sudo apt install -y lsd zoxide batcat rsync nano fzf
}

fish_cmd() {
    fish -c "$1"
}

fish_config_fzf() {
    fish_cmd "set -U FZF_DEFAULT_OPTS '--height 40% --layout=reverse --border'"
}

fish_config_greeting() {
    fish_cmd "set -U fish_greeting"
    cat > ~/.config/fish/config.fish << EOF
if status --is-interactive
    zoxide init fish | source
    whatthedouchsay
end
EOF
}

fish_config_aliases() {
    # lsd aliases
    fish_cmd "alias --save ls='lsd --group-directories-first'"
    fish_cmd "alias --save la='lsd -a --group-directories-first'"
    fish_cmd "alias --save ll='lsd -l --group-directories-first'"
    fish_cmd "alias --save lla='lsd -la --group-directories-first'"
    fish_cmd "alias --save lt='lsd --tree --depth 3 --group-directories-first'"
    fish_cmd "alias --save lta='lsd --tree -a --depth 3 --group-directories-first'"
    # apt aliases
    fish_cmd "alias --save ainstall='sudo apt install'"
    fish_cmd "alias --save aupdate='sudo apt update && sudo apt upgrade'"
    fish_cmd "alias --save aupgrade='sudo apt update && sudo apt upgrade'"
    fish_cmd "alias --save aremove='sudo apt remove'"
    fish_cmd "alias --save aautoremove='sudo apt autoremove'"
    fish_cmd "alias --save asearch='sudo apt search'"
    # nano alias
    fish_cmd "alias --save nano='nano --linenumbers --tabtospaces --tabsize 4'"
    # utils aliases
    fish_cmd "alias --save cat='batcat'"
    fish_cmd "alias --save cls='clear'"
    fish_cmd "alias --save cd='z'"
    fish_cmd "alias --save rsync='rsync -ahv'"
    fish_cmd "alias --save kernel='uname -r | sed 's/[1-9]\+[0-9]*\.[0-9]\+\.[0-9]\+-//' | sed 's/[1-9]\+[0-9]*\.[0-9]*\-rc[0-9]\+-//''"
}

main() {
    install
    fish_config_greeting
    fish_config_aliases
    fish_config_fzf
}

main

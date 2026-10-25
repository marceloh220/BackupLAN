#!/usr/bin/fish

set -U fish_greeting ""
cat > ~/.config/fish/config.fish << EOF
if status --is-interactive
    zoxide init fish | source
    whatthedouchsay
end
EOF

# lsd aliases
alias --save ls='lsd --group-directories-first'
alias --save la='lsd -a --group-directories-first'
alias --save ll='lsd -l --group-directories-first'
alias --save lla='lsd -la --group-directories-first'
alias --save lt='lsd --tree --depth 3 --group-directories-first'
alias --save lta='lsd --tree -a --depth 3 --group-directories-first'

# apt aliases
alias --save ainstall='sudo apt install'
alias --save aupdate='sudo apt update && sudo apt upgrade'
alias --save aupgrade='sudo apt update && sudo apt upgrade'
alias --save aremove='sudo apt remove'
alias --save aautoremove='sudo apt autoremove'
alias --save asearch='sudo apt search'

# nano alias
alias --save nano="nano --linenumbers --tabtospaces --tabsize 4"

# utils aliases
alias --save cat='batcat'
alias --save cls='clear'
alias --save cd='z'
alias --save rsync='rsync -ahv'

alias --save kernel="uname -r | sed 's/[1-9]\+[0-9]*\.[0-9]\+\.[0-9]\+-//' | sed 's/[1-9]\+[0-9]*\.[0-9]*\-rc[0-9]\+-//'"
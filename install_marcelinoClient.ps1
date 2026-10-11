$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RequirementsFile = Join-Path $ProjectDir "requirements.txt"
$ClientScript = Join-Path $ProjectDir "marcelinoClient.py"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\MarcelinoClient"
$ExecutablePath = Join-Path $InstallDir "MarcelinoClient.exe"
$InstalledIconPath = Join-Path $InstallDir "MarcelinoClient.ico"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$StartMenuPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutPaths = @(
    (Join-Path $DesktopPath "Marcelino Client.lnk"),
    (Join-Path $StartMenuPath "Marcelino Client.lnk")
)
$BuildDir = Join-Path ([System.IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())

function New-MarcelinoIcon($Path) {
    Add-Type -AssemblyName System.Drawing
    $Bitmap = [System.Drawing.Bitmap]::new(256, 256)
    $Graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
    $WhiteBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $TealBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(22, 123, 136))
    $GreenBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(90, 190, 130))

    try {
        $Graphics.Clear([System.Drawing.Color]::FromArgb(20, 52, 62))
        $Graphics.FillRectangle($WhiteBrush, 36, 48, 184, 160)
        foreach ($Y in @(64, 110, 156)) {
            $Graphics.FillRectangle($TealBrush, 52, $Y, 152, 34)
            $Graphics.FillEllipse($GreenBrush, 66, ($Y + 10), 14, 14)
            $Graphics.FillRectangle($WhiteBrush, 94, ($Y + 12), 90, 10)
        }

        $IconHandle = $Bitmap.GetHicon()
        $Icon = [System.Drawing.Icon]::FromHandle($IconHandle)
        $FileStream = [System.IO.File]::Create($Path)
        try {
            $Icon.Save($FileStream)
        } finally {
            $FileStream.Dispose()
            $Icon.Dispose()
        }
    } finally {
        $GreenBrush.Dispose()
        $TealBrush.Dispose()
        $WhiteBrush.Dispose()
        $Graphics.Dispose()
        $Bitmap.Dispose()
    }
}

if (-not (Test-Path $RequirementsFile) -or -not (Test-Path $ClientScript)) {
    throw "Coloque este instalador na pasta que contém marcelinoClient.py e requirements.txt."
}

$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PythonLauncher) {
    $PythonCommand = $PythonLauncher.Source
    $PythonArguments = @("-3")
} else {
    $PythonLauncher = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonLauncher) {
        throw "Python 3 não encontrado. Instale o Python 3 e tente novamente."
    }
    $PythonCommand = $PythonLauncher.Source
    $PythonArguments = @()
}

try {
    New-Item -ItemType Directory -Path $BuildDir | Out-Null
    $VenvPython = Join-Path $BuildDir "venv\Scripts\python.exe"

    & $PythonCommand @PythonArguments -m venv (Join-Path $BuildDir "venv")
    if ($LASTEXITCODE -ne 0) { throw "Não foi possível criar o ambiente Python temporário." }

    & $VenvPython -m pip install -r $RequirementsFile
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependências do projeto." }

    $DistDir = Join-Path $BuildDir "dist"
    $IconPath = Join-Path $BuildDir "MarcelinoClient.ico"
    New-MarcelinoIcon $IconPath
    & $VenvPython -m PyInstaller --noconfirm --clean --onefile --windowed `
        --collect-all customtkinter --icon $IconPath `
        --name MarcelinoClient --distpath $DistDir `
        --workpath (Join-Path $BuildDir "work") `
        --specpath $BuildDir $ClientScript
    if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o executável com PyInstaller." }

    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Copy-Item (Join-Path $DistDir "MarcelinoClient.exe") $ExecutablePath -Force
    Copy-Item $IconPath $InstalledIconPath -Force

    $Shell = New-Object -ComObject WScript.Shell
    foreach ($ShortcutPath in $ShortcutPaths) {
        $ShortcutDirectory = Split-Path -Parent $ShortcutPath
        New-Item -ItemType Directory -Path $ShortcutDirectory -Force | Out-Null
        $Shortcut = $Shell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath = $ExecutablePath
        $Shortcut.WorkingDirectory = $InstallDir
        $Shortcut.IconLocation = $InstalledIconPath
        $Shortcut.Save()
    }

    Write-Host "Instalação concluída."
    Write-Host "Executável: $ExecutablePath"
    Write-Host "Ícone: $InstalledIconPath"
    Write-Host "Atalho na Área de Trabalho: $($ShortcutPaths[0])"
    Write-Host "Atalho no Menu Iniciar: $($ShortcutPaths[1])"
} finally {
    if (Test-Path $BuildDir) {
        Remove-Item $BuildDir -Recurse -Force
    }
}
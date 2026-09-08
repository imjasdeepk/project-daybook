# project-ledger installer for Windows.
#
#   irm https://raw.githubusercontent.com/imjasdeepk/project-ledger/main/install.ps1 | iex
#
# Prefer to read it first? That is the right instinct with any piped installer:
#
#   irm https://raw.githubusercontent.com/imjasdeepk/project-ledger/main/install.ps1 -OutFile install.ps1
#   notepad install.ps1 ; .\install.ps1
#
# Everything can be set up front, which skips all prompts:
#
#   $env:LEDGER_DIR="$HOME\Documents\ledger"; $env:LEDGER_BACKUP="git"; .\install.ps1

$ErrorActionPreference = 'Stop'

function Say  { param($m) Write-Host $m }
function Step { param($m) Write-Host ""; Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $m }
function Note { param($m) Write-Host "    $m" -ForegroundColor DarkGray }
function Die  { param($m) Write-Host ""; Write-Host "error: $m" -ForegroundColor Red; exit 1 }

function Ask {
    param($Prompt, $Default)
    if ($env:LEDGER_NONINTERACTIVE) { return $Default }
    $answer = Read-Host "    $Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer
}

$RepoUrl    = if ($env:LEDGER_REPO) { $env:LEDGER_REPO } else { 'https://github.com/imjasdeepk/project-ledger.git' }
$InstallDir = if ($env:LEDGER_INSTALL_DIR) { $env:LEDGER_INSTALL_DIR } else { Join-Path $HOME 'project-ledger' }

Say ""
Say "project-ledger"
Say "A personal ledger you talk to, built so it cannot invent numbers."

Step "Checking what you already have"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is not installed. Get it from https://git-scm.com/downloads and run this again."
}
Note "git $((git --version) -replace 'git version ','')"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    # uv installs its own Python, so nothing here depends on the Python you may
    # or may not already have.
    Say ""
    Say "This needs uv, which manages Python for you."
    $go = Ask "Install uv now from astral.sh? (y/n)" "y"
    if ($go -notmatch '^[Yy]') {
        Die "uv is required. See https://docs.astral.sh/uv/getting-started/installation/"
    }
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$HOME\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die "uv installed but is not on your PATH. Open a new terminal and run this again."
    }
}
Note "uv $((uv --version) -replace 'uv ','')"

Step "Getting the tool"
if (Test-Path (Join-Path $InstallDir '.git')) {
    Note "Already at $InstallDir, updating it"
    git -C $InstallDir pull --ff-only --quiet
} elseif (Test-Path $InstallDir) {
    Die "$InstallDir already exists and is not a git checkout. Move it, or set LEDGER_INSTALL_DIR."
} else {
    git clone --quiet $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Die "Could not clone $RepoUrl. If it is private, check your access." }
    Note "Cloned into $InstallDir"
}

Set-Location $InstallDir
Note "Installing dependencies, which takes a minute the first time"
uv sync --quiet
if ($LASTEXITCODE -ne 0) { Die "uv sync failed. Run 'uv sync' in $InstallDir to see why." }

Step "Where should your records live?"
Say "    Any folder. It is yours, and nothing you record is ever stored with the tool."
$LedgerDir = if ($env:LEDGER_DIR) { $env:LEDGER_DIR } else { Ask "Folder" (Join-Path $HOME 'Documents\ledger') }

Step "How do you want them backed up?"
Say "    synced  put the folder in Google Drive, OneDrive or Dropbox"
Say "    git     make it a private git repository, with every entry committed"
$Backup = if ($env:LEDGER_BACKUP) { $env:LEDGER_BACKUP } else { Ask "Choice" "synced" }

$Currencies = if ($env:LEDGER_CURRENCIES) { $env:LEDGER_CURRENCIES } else { Ask "Which currencies? Comma separated" "USD" }

Step "Creating your ledger"
if (Test-Path (Join-Path $LedgerDir 'main.beancount')) {
    Note "A ledger already exists at $LedgerDir, keeping it"
    Set-Content -Path (Join-Path $InstallDir '.ledger-root') -Value $LedgerDir -Encoding utf8
} else {
    $initArgs = @('run','ledger','init',$LedgerDir,'--currencies',$Currencies)
    if ($Backup -eq 'git') { $initArgs += '--git' }
    & uv @initArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "Could not create the ledger." }
    Note "Created $LedgerDir"
}
uv run ledger check | Out-Null
if ($LASTEXITCODE -ne 0) { Die "The new ledger did not validate." }
Note "Validated"

$Global = if ($env:LEDGER_GLOBAL_SKILL) { $env:LEDGER_GLOBAL_SKILL } else { Ask "Use the ledger from any folder, not just this one? (y/n)" "y" }
if ($Global -match '^(y|yes)$') {
    $skills = Join-Path $HOME '.claude\skills'
    New-Item -ItemType Directory -Force -Path $skills | Out-Null
    $link = Join-Path $skills 'ledger'
    if (Test-Path $link) { Remove-Item $link -Recurse -Force }
    $target = Join-Path $InstallDir '.claude\skills\ledger'
    try {
        New-Item -ItemType SymbolicLink -Path $link -Target $target -ErrorAction Stop | Out-Null
    } catch {
        # Symlinks need Developer Mode or an elevated shell on Windows; a copy
        # works just as well, it simply will not track updates to the tool.
        Copy-Item $target $link -Recurse
        Note "Copied the skill (symlinks need Developer Mode); re-run after updating the tool"
    }
    Set-Content -Path (Join-Path $HOME '.ledger-root') -Value $LedgerDir -Encoding utf8
    Note "Installed the skill into ~\.claude\skills and pointed it at your records"
}

Step "Done"
Say ""
Say "    Records   $LedgerDir"
Say "    Tool      $InstallDir"
Say ""
Say "  Open the folder in Claude Code or Claude Cowork and just talk to it:"
Say ""
Say "      lent dad 5000 rupees for the car last tuesday"
Say "      how much does dad owe me?"
Say "      dad's birthday is 14 March 1958"
Say ""
Say "  Or use it directly:  cd $InstallDir ; uv run ledger --help"
if ($Backup -eq 'git') {
    Say ""
    Say "  Your records are a git repository. For an off-machine copy, add a private"
    Say "  remote and then run 'uv run ledger sync':"
    Say ""
    Say "      cd $LedgerDir"
    Say "      gh repo create <you>/my-ledger --private --source . --remote origin --push"
}
Say ""

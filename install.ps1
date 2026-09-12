# project-daybook installer for Windows.
#
#   irm https://raw.githubusercontent.com/imjasdeepk/project-daybook/main/install.ps1 | iex
#
# Prefer to read it first? That is the right instinct with any piped installer:
#
#   irm https://raw.githubusercontent.com/imjasdeepk/project-daybook/main/install.ps1 -OutFile install.ps1
#   notepad install.ps1 ; .\install.ps1
#
# Everything can be set up front, which skips all prompts:
#
#   $env:DAYBOOK_DIR="$HOME\Documents\daybook"; $env:DAYBOOK_BACKUP="git"; .\install.ps1

$ErrorActionPreference = 'Stop'

function Say  { param($m) Write-Host $m }
function Step { param($m) Write-Host ""; Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $m }
function Note { param($m) Write-Host "    $m" -ForegroundColor DarkGray }
function Die  { param($m) Write-Host ""; Write-Host "error: $m" -ForegroundColor Red; exit 1 }

function Ask {
    param($Prompt, $Default)
    if ($env:DAYBOOK_NONINTERACTIVE) { return $Default }
    $answer = Read-Host "    $Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer
}

$RepoUrl    = if ($env:DAYBOOK_REPO) { $env:DAYBOOK_REPO } else { 'https://github.com/imjasdeepk/project-daybook.git' }
$InstallDir = if ($env:DAYBOOK_INSTALL_DIR) { $env:DAYBOOK_INSTALL_DIR } else { Join-Path $HOME 'project-daybook' }

Say ""
Say "project-daybook"
Say "A daybook you talk to: a diary and a ledger, built so it cannot invent facts."

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
    Die "$InstallDir already exists and is not a git checkout. Move it, or set DAYBOOK_INSTALL_DIR."
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
$DaybookDir = if ($env:DAYBOOK_DIR) { $env:DAYBOOK_DIR } else { Ask "Folder" (Join-Path $HOME 'Documents\daybook') }

Step "How do you want them backed up?"
Say "    synced  put the folder in Google Drive, OneDrive or Dropbox"
Say "    git     make it a private git repository, with every entry committed"
$Backup = if ($env:DAYBOOK_BACKUP) { $env:DAYBOOK_BACKUP } else { Ask "Choice" "synced" }

$Currencies = if ($env:DAYBOOK_CURRENCIES) { $env:DAYBOOK_CURRENCIES } else { Ask "Which currencies? Comma separated" "USD" }

Step "Creating your ledger"
if (Test-Path (Join-Path $DaybookDir 'main.beancount')) {
    Note "A ledger already exists at $DaybookDir, keeping it"
    Set-Content -Path (Join-Path $InstallDir '.daybook-root') -Value $DaybookDir -Encoding utf8
} else {
    $initArgs = @('run','daybook','init',$DaybookDir,'--currencies',$Currencies)
    if ($Backup -eq 'git') { $initArgs += '--git' }
    & uv @initArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "Could not create the ledger." }
    Note "Created $DaybookDir"
}
uv run daybook check | Out-Null
if ($LASTEXITCODE -ne 0) { Die "The new ledger did not validate." }
Note "Validated"

$NotesDir = Join-Path $DaybookDir 'notes'
if (Test-Path (Join-Path $NotesDir 'notes.toml')) {
    Note "Notes already set up at $NotesDir, keeping them"
} else {
    uv run daybook note init $NotesDir --period week --no-remember | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "Could not create the notes folder." }
    Note "Created $NotesDir for your diary and knowledge base"
}

$Global = if ($env:DAYBOOK_GLOBAL_SKILL) { $env:DAYBOOK_GLOBAL_SKILL } else { Ask "Use daybook from any folder, not just this one? (y/n)" "y" }
if ($Global -match '^(y|yes)$') {
    $skills = Join-Path $HOME '.claude\skills'
    New-Item -ItemType Directory -Force -Path $skills | Out-Null
    foreach ($skill in @('ledger', 'notes')) {
        $link = Join-Path $skills $skill
        if (Test-Path $link) { Remove-Item $link -Recurse -Force }
        $target = Join-Path $InstallDir (Join-Path '.claude\skills' $skill)
        try {
            New-Item -ItemType SymbolicLink -Path $link -Target $target -ErrorAction Stop | Out-Null
        } catch {
            # Symlinks need Developer Mode or an elevated shell on Windows; a copy
            # works just as well, it simply will not track updates to the tool.
            Copy-Item $target $link -Recurse
            Note "Copied the $skill skill (symlinks need Developer Mode); re-run after updating"
        }
    }
    Set-Content -Path (Join-Path $HOME '.daybook-root') -Value $DaybookDir -Encoding utf8
    Note "Installed both skills into ~\.claude\skills and pointed them at your records"
}

Step "Done"
Say ""
Say "    Records   $DaybookDir"
Say "    Tool      $InstallDir"
Say ""
Say "  Open the folder in Claude Code or Claude Cowork and just talk to it:"
Say ""
Say "      lent dad 5000 rupees for the car last tuesday"
Say "      how much does dad owe me?"
Say "      dad's birthday is 14 March 1958"
Say ""
Say "  Or use it directly:  cd $InstallDir ; uv run daybook --help"
if ($Backup -eq 'git') {
    Say ""
    Say "  Your records are a git repository. For an off-machine copy, add a private"
    Say "  remote and then run 'uv run daybook sync':"
    Say ""
    Say "      cd $DaybookDir"
    Say "      gh repo create <you>/my-daybook --private --source . --remote origin --push"
}
Say ""

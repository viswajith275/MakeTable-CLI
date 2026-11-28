<#
.SYNOPSIS
    MakeTable-CLI Installer (Windows)
.DESCRIPTION
    Installs the timetable binary from GitHub Releases or a local archive/executable
    and adds the install directory to the User PATH.
#>
[CmdletBinding()]
param(
    [Alias("d")]
    [string]$Dir = "",

    [Alias("v")]
    [string]$Version = "latest",

    [Alias("f")]
    [string]$File = "",

    [switch]$NoVerify,
    [switch]$DryRun,

    [Alias("h")]
    [switch]$Help,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UnexpectedArgs
)

$Repo = "viswajith275/MakeTable-CLI"
$BinName = "timetable"
$ExeName = "timetable.exe"
$ArchiveExt = ".zip"

$LocalAppData = if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    $env:LOCALAPPDATA
} elseif (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    Join-Path $env:USERPROFILE "AppData\Local"
} else {
    Join-Path $HOME "AppData\Local"
}
$DefaultInstallDir = Join-Path $LocalAppData "Programs\MakeTable"

function Resolve-InstallDir([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $DefaultInstallDir
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue.Trim())
    $homeDir = if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { $env:USERPROFILE } else { $HOME }
    if ($expanded -eq "~") {
        $expanded = $homeDir
    } elseif ($expanded.StartsWith("~/") -or $expanded.StartsWith("~\")) {
        $expanded = Join-Path $homeDir $expanded.Substring(2)
    }
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    $fsCwd = (Get-Location -PSProvider FileSystem).ProviderPath
    return [System.IO.Path]::GetFullPath((Join-Path $fsCwd $expanded))
}

$Dir = Resolve-InstallDir $Dir
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = "latest"
} else {
    $Version = $Version.Trim()
}
if ($Version -ne "latest" -and -not $Version.StartsWith("v")) {
    $Version = "v$Version"
}

function Write-LogStep([string]$Message)    { Write-Host "==> " -ForegroundColor Blue -NoNewline; Write-Host $Message }
function Write-LogInfo([string]$Message)    { Write-Host "  -> " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-LogSuccess([string]$Message) { Write-Host "  [OK] " -ForegroundColor Green -NoNewline; Write-Host $Message }
function Write-LogWarn([string]$Message)    { Write-Host "  ! " -ForegroundColor Yellow -NoNewline; Write-Host $Message }
function Write-LogError([string]$Message)   { Write-Host "  [X] " -ForegroundColor Red -NoNewline; Write-Host $Message }

function Show-Usage {
    @"
Usage: install.ps1 [options]

Installs the $BinName binary from GitHub Releases or a local archive.

Options:
  -Dir, -d <path>      Target install directory (default: $DefaultInstallDir)
  -Version, -v <tag>   Release version to install (default: latest)
  -File, -f <path>     Install directly from a local .zip archive or .exe binary
  -NoVerify            Skip SHA-256 checksum verification
  -DryRun              Preview actions without making changes
  -Help, -h            Show this help message
"@ | Write-Host
}

function Normalize-PathEntry([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return ""
    }
    return $PathValue.Trim().TrimEnd('\', '/').ToLowerInvariant()
}

function Get-WindowsTargetPlatform {
    $isWindowsOs = ($env:OS -eq "Windows_NT")
    if (-not $isWindowsOs) {
        Write-LogError "This installer is for Windows. Use scripts/install.sh on Linux or macOS."
        throw "Unsupported operating system."
    }

    $arch = ""
    try {
        $arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    } catch {
        if (-not [string]::IsNullOrWhiteSpace($env:PROCESSOR_ARCHITEW6432)) {
            $arch = $env:PROCESSOR_ARCHITEW6432
        } else {
            $arch = $env:PROCESSOR_ARCHITECTURE
        }
    }

    if ([string]::IsNullOrWhiteSpace($arch)) {
        Write-LogError "Could not determine Windows architecture."
        throw "Unsupported architecture."
    }

    switch ($arch.ToUpperInvariant()) {
        "X64"   { $platform = "windows-x86_64" }
        "AMD64" { $platform = "windows-x86_64" }
        "ARM64" {
            Write-LogError "Windows ARM64 is not currently supported by prebuilt releases."
            throw "Unsupported architecture: $arch"
        }
        default {
            Write-LogError "Unsupported operating system/architecture: Windows/$arch"
            throw "Unsupported architecture: $arch"
        }
    }

    Write-LogInfo "Detected target: $platform"
    return $platform
}

function Verify-ArchiveChecksum([string]$TargetFile, [string]$SidecarUrl) {
    if ($NoVerify) {
        return
    }

    Write-LogInfo "Verifying SHA-256 checksum..."
    $sidecarContent = $null
    try {
        $response = Invoke-WebRequest -Uri $SidecarUrl -UseBasicParsing
        if ($response.Content -is [byte[]]) {
            $sidecarContent = [System.Text.Encoding]::UTF8.GetString($response.Content)
        } else {
            $sidecarContent = [string]$response.Content
        }
    } catch {
        $sidecarContent = $null
    }

    if ([string]::IsNullOrWhiteSpace($sidecarContent)) {
        Write-LogWarn "Checksum sidecar not found. Skipping verification."
        return
    }

    $cleanSidecar = $sidecarContent.TrimStart([char]0xFEFF).Trim()
    $firstToken = ($cleanSidecar -split "\s+")[0].ToLowerInvariant()
    if ($firstToken -notmatch "^[0-9a-f]{64}$") {
        Write-LogWarn "Malformed checksum file. Skipping verification."
        return
    }

    $actual = (Get-FileHash -Path $TargetFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($firstToken -ne $actual) {
        Write-LogError "Checksum mismatch! Expected $firstToken, got $actual."
        throw "Checksum verification failed."
    }

    Write-LogSuccess "Checksum verified"
}

function Install-TimetableBinary([string]$Platform, [string]$TempDir) {
    $target = Join-Path $Dir $ExeName
    $sourceArchive = ""

    if (-not [string]::IsNullOrWhiteSpace($File)) {
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
            Write-LogError "Local file not found: $File"
            throw "Local file not found: $File"
        }
        $sourceArchive = (Resolve-Path -LiteralPath $File).Path
        Write-LogInfo "Source: local archive ($sourceArchive)"
    } else {
        $relPath = if ($Version -eq "latest") { "latest/download" } else { "download/$Version" }
        $dlUrl = "https://github.com/$Repo/releases/$relPath/$BinName-$Platform$ArchiveExt"

        if ($DryRun) {
            Write-LogInfo "[Dry-run] Would download $dlUrl"
            Write-LogInfo "[Dry-run] Would install binary to $target"
            return
        }

        Write-LogStep "Downloading $BinName ($Version)..."
        $sourceArchive = Join-Path $TempDir "download$ArchiveExt"
        try {
            Invoke-WebRequest -Uri $dlUrl -OutFile $sourceArchive -UseBasicParsing
        } catch {
            Write-LogError "Failed to download asset from $dlUrl"
            throw
        }
        Verify-ArchiveChecksum -TargetFile $sourceArchive -SidecarUrl "$dlUrl.sha256"
    }

    if ($DryRun) {
        Write-LogInfo "[Dry-run] Would unpack and install to $target"
        return
    }

    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $extractDir = Join-Path $TempDir "extracted"
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

    if ($sourceArchive.ToLowerInvariant().EndsWith(".zip")) {
        Expand-Archive -LiteralPath $sourceArchive -DestinationPath $extractDir -Force
    } else {
        Copy-Item -LiteralPath $sourceArchive -Destination (Join-Path $extractDir $ExeName) -Force
    }

    $binarySrc = Get-ChildItem -Path $extractDir -Filter $ExeName -File -Recurse | Select-Object -First 1
    if ($null -eq $binarySrc) {
        Write-LogError "Could not locate '$ExeName' binary in archive."
        throw "Binary missing from archive."
    }

    try {
        Copy-Item -LiteralPath $binarySrc.FullName -Destination $target -Force
    } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
        Write-LogError "Cannot overwrite $target (it may be running or locked). Close $BinName processes and retry."
        throw
    }
    Unblock-File -LiteralPath $target -ErrorAction SilentlyContinue
    Write-LogSuccess "Binary installed at $target"
}

function Update-UserPath {
    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if (-not [string]::IsNullOrEmpty($currentUserPath)) {
        $entries = @($currentUserPath -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }

    $normTarget = Normalize-PathEntry $Dir
    $alreadyPresent = $false
    foreach ($entry in $entries) {
        if ((Normalize-PathEntry $entry) -eq $normTarget) {
            $alreadyPresent = $true
            break
        }
    }

    if ($alreadyPresent) {
        Write-LogInfo "PATH configuration already present in User PATH"
    } elseif ($DryRun) {
        Write-LogInfo "[Dry-run] Would add '$Dir' to User PATH"
        return
    } else {
        $updatedEntries = @($entries + $Dir) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $updatedEntries, "User")
        Write-LogSuccess "Added $Dir to User PATH"
    }

    if (-not $DryRun) {
        $sessionEntries = @($env:Path -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $inSession = $false
        foreach ($entry in $sessionEntries) {
            if ((Normalize-PathEntry $entry) -eq $normTarget) {
                $inSession = $true
                break
            }
        }
        if (-not $inSession) {
            $env:Path = "$Dir;$env:Path"
        }
    }
}

if ($Help) {
    Show-Usage
    return
}
if (@($UnexpectedArgs | Where-Object { $null -ne $_ }).Count -gt 0) {
    Write-LogError "Unknown option(s): $($UnexpectedArgs -join ' ')"
    Show-Usage
    throw "Unknown option."
}

$prevErrorAction = $ErrorActionPreference
$prevProgress = $ProgressPreference
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$tempDir = $null

try {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch {
        # Best effort for older Windows PowerShell builds
    }

    $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("maketable-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

    Write-LogStep "Installing $BinName..."
    if ($DryRun) {
        Write-LogWarn "Running in DRY-RUN mode. No changes will be committed."
    }

    $platform = Get-WindowsTargetPlatform
    Install-TimetableBinary -Platform $platform -TempDir $tempDir
    Update-UserPath

    if ($DryRun) {
        Write-LogInfo "Dry run complete."
        return
    }

    Write-Host ""
    Write-Host "[OK] Installation successful!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Open a new terminal (or use the current PowerShell session where PATH is updated)"
    Write-Host "  2. Test binary: $BinName --help"
} finally {
    $ErrorActionPreference = $prevErrorAction
    $ProgressPreference = $prevProgress
    if ($tempDir -and (Test-Path -LiteralPath $tempDir)) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

<#
.SYNOPSIS
    MakeTable-CLI Uninstaller (Windows)
.DESCRIPTION
    Uninstalls the timetable binary and cleans up User PATH and PowerShell profile entries.
#>
[CmdletBinding()]
param(
    [Alias("d")]
    [string]$Dir = "",

    [Alias("PurgeRc")]
    [switch]$PurgeProfile,

    [switch]$DryRun,

    [Alias("h")]
    [switch]$Help,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UnexpectedArgs
)

$BinName = "timetable"
$ExeName = "timetable.exe"

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

function Write-LogStep([string]$Message)    { Write-Host "==> " -ForegroundColor Blue -NoNewline; Write-Host $Message }
function Write-LogInfo([string]$Message)    { Write-Host "  -> " -ForegroundColor Cyan -NoNewline; Write-Host $Message }
function Write-LogSuccess([string]$Message) { Write-Host "  [OK] " -ForegroundColor Green -NoNewline; Write-Host $Message }
function Write-LogWarn([string]$Message)    { Write-Host "  ! " -ForegroundColor Yellow -NoNewline; Write-Host $Message }
function Write-LogError([string]$Message)   { Write-Host "  [X] " -ForegroundColor Red -NoNewline; Write-Host $Message }

function Show-Usage {
    @"
Usage: uninstall.ps1 [options]

Uninstalls the $BinName binary and cleans up PATH and PowerShell configurations.

Options:
  -Dir, -d <path>           Target install directory containing binary (default: $DefaultInstallDir)
  -PurgeProfile, -PurgeRc   Remove PATH lines and custom $BinName functions from PowerShell profile files
  -DryRun                   Preview changes without deleting or modifying files
  -Help, -h                 Show this help message
"@ | Write-Host
}

function Normalize-PathEntry([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return ""
    }
    return $PathValue.Trim().TrimEnd('\', '/').ToLowerInvariant()
}

function Remove-TimetableBinary {
    $target = Join-Path $Dir $ExeName
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if ($DryRun) {
            Write-LogInfo "[Dry-run] Would remove binary at $target"
        } else {
            Remove-Item -LiteralPath $target -Force
            Write-LogSuccess "Removed binary from $target"

            if ((Normalize-PathEntry $Dir) -eq (Normalize-PathEntry $DefaultInstallDir) -and (Test-Path -LiteralPath $Dir -PathType Container)) {
                $remaining = @(Get-ChildItem -LiteralPath $Dir -Force)
                if ($remaining.Count -eq 0) {
                    Remove-Item -LiteralPath $Dir -Force
                }
            }
        }
    } else {
        Write-LogWarn "Binary not found at $target (skipping)"
    }
}

function Cleanup-UserPath {
    $normTarget = Normalize-PathEntry $Dir
    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not [string]::IsNullOrEmpty($currentUserPath)) {
        $entries = @($currentUserPath -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $filtered = @($entries | Where-Object { (Normalize-PathEntry $_) -ne $normTarget })
        if ($filtered.Count -ne $entries.Count) {
            if ($DryRun) {
                Write-LogInfo "[Dry-run] Would remove '$Dir' from User PATH"
            } else {
                $updated = $filtered -join ";"
                [Environment]::SetEnvironmentVariable("Path", $updated, "User")
                Write-LogSuccess "Removed $Dir from User PATH"
            }
        }
    }

    if (-not $DryRun -and -not [string]::IsNullOrEmpty($env:Path)) {
        $sessionEntries = @($env:Path -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $sessionFiltered = @($sessionEntries | Where-Object { (Normalize-PathEntry $_) -ne $normTarget })
        if ($sessionFiltered.Count -ne $sessionEntries.Count) {
            $env:Path = $sessionFiltered -join ";"
        }
    }
}

function Cleanup-Profiles {
    $docs = [Environment]::GetFolderPath("MyDocuments")
    $rawCandidates = New-Object System.Collections.Generic.List[string]
    if ($null -ne $PROFILE) {
        foreach ($propName in @("CurrentUserAllHosts", "CurrentUserCurrentHost")) {
            $prop = $PROFILE.PSObject.Properties[$propName]
            if ($null -ne $prop -and -not [string]::IsNullOrWhiteSpace([string]$prop.Value)) {
                $rawCandidates.Add([string]$prop.Value)
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($docs)) {
        foreach ($p in @(
            (Join-Path $docs "WindowsPowerShell\Microsoft.PowerShell_profile.ps1"),
            (Join-Path $docs "WindowsPowerShell\profile.ps1"),
            (Join-Path $docs "PowerShell\Microsoft.PowerShell_profile.ps1"),
            (Join-Path $docs "PowerShell\profile.ps1")
        )) {
            $rawCandidates.Add($p)
        }
    }

    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($p in $rawCandidates) {
        if (-not [string]::IsNullOrWhiteSpace($p) -and -not $candidates.Contains($p)) {
            $candidates.Add($p)
        }
    }

    $markerPattern = "^#\s*Added by " + [regex]::Escape($BinName) + " installer"
    $funcPattern = "^(?:function|filter)\s+(?:[A-Za-z]+:)?" + [regex]::Escape($BinName) + "(?:\s|\(|\{|$)"

    foreach ($profilePath in $candidates) {
        if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
            continue
        }

        $raw = Get-Content -LiteralPath $profilePath -Raw -ErrorAction SilentlyContinue
        if ([string]::IsNullOrEmpty($raw) -or $raw.IndexOf($BinName, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            continue
        }

        $lines = @(Get-Content -LiteralPath $profilePath)
        $newLines = New-Object System.Collections.Generic.List[string]
        $skipNext = $false
        $inFunc = $false
        $braceDepth = 0
        $changed = $false

        foreach ($line in $lines) {
            if ($skipNext) {
                $skipNext = $false
                $changed = $true
                continue
            }
            if ($line -match $markerPattern) {
                $skipNext = $true
                $changed = $true
                continue
            }
            if ($PurgeProfile) {
                if (-not $inFunc -and $line -match $funcPattern) {
                    $inFunc = $true
                    $changed = $true
                    $braceDepth = ([regex]::Matches($line, "\{")).Count - ([regex]::Matches($line, "\}")).Count
                    if ($braceDepth -le 0 -and $line -match "\}") {
                        $inFunc = $false
                    }
                    continue
                }
                if ($inFunc) {
                    $changed = $true
                    $braceDepth += ([regex]::Matches($line, "\{")).Count - ([regex]::Matches($line, "\}")).Count
                    if ($braceDepth -le 0) {
                        $inFunc = $false
                    }
                    continue
                }
            }
            $newLines.Add($line)
        }

        if (-not $changed) {
            continue
        }

        if ($DryRun) {
            Write-LogInfo "[Dry-run] Would clean up entries in $profilePath"
            continue
        }

        $eol = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
        $updated = $newLines -join $eol
        if ($raw.EndsWith("`n") -and -not $updated.EndsWith("`n") -and $updated.Length -gt 0) {
            $updated += $eol
        }

        if ($updated -ne $raw) {
            [System.IO.File]::WriteAllText($profilePath, $updated, [System.Text.UTF8Encoding]::new($false))
            Write-LogSuccess "Cleaned configuration entries in $profilePath"
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
$ErrorActionPreference = "Stop"

try {
    Write-LogStep "Uninstalling $BinName..."
    if ($DryRun) {
        Write-LogWarn "Running in DRY-RUN mode. No changes will be committed."
    }

    Remove-TimetableBinary
    Cleanup-UserPath
    Cleanup-Profiles

    if ($DryRun) {
        Write-LogInfo "Dry run complete."
    } else {
        Write-Host ""
        Write-Host "[OK] Uninstallation complete!" -ForegroundColor Green
    }
} finally {
    $ErrorActionPreference = $prevErrorAction
}

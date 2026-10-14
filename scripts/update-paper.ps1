param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$Revision
)

$ErrorActionPreference = "Stop"
$projectDir = (Get-Location).Path
if (-not (Test-Path (Join-Path $projectDir "app\main.py"))) {
    throw "Run from the project folder. Stop the bot with Ctrl+C first."
}
$stageDir = Join-Path ([System.IO.Path]::GetTempPath()) ("arbitrage-update-" + [guid]::NewGuid().ToString("N"))
$backupDir = Join-Path $projectDir ("backup-update-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
$changedFiles = [System.Collections.Generic.List[string]]::new()
try {
    New-Item -ItemType Directory -Path $stageDir | Out-Null
    $zipPath = Join-Path $stageDir "source.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/mfievmkmkm/arbitrage-engine/zip/$Revision" -OutFile $zipPath
    Expand-Archive -LiteralPath $zipPath -DestinationPath (Join-Path $stageDir "source")
    $sourceDir = Join-Path $stageDir ("source\arbitrage-engine-" + $Revision)
    if (-not (Test-Path (Join-Path $sourceDir "app\main.py"))) { throw "Incomplete source archive" }
    $files = @(Get-ChildItem -LiteralPath $sourceDir -File -Recurse -Force)
    New-Item -ItemType Directory -Path $backupDir | Out-Null
    # Archive contains tracked source only. Never replace local credentials/data.
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($sourceDir.Length + 1)
        if ($relative -match '(^|[\\/])(\.env|bot\.env|server-transfer|data)([\\/]|$)' -or $relative -match '\.(sqlite3|db)(-|$)') { continue }
        $destination = Join-Path $projectDir $relative
        $saved = Join-Path $backupDir $relative
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            New-Item -ItemType Directory -Force -Path (Split-Path $saved) | Out-Null
            Copy-Item -LiteralPath $destination -Destination $saved
        }
        New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
        $changedFiles.Add($relative)
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
    Set-Content -LiteralPath (Join-Path $projectDir "installed-revision.txt") -Value $Revision -Encoding ASCII
    Write-Output "Updated source to $Revision"
    Write-Output "Backup: $backupDir"
    Write-Output "Existing .env, database and runtime state preserved. Run pytest before starting."
} catch {
    foreach ($relative in $changedFiles) {
        $destination = Join-Path $projectDir $relative
        $saved = Join-Path $backupDir $relative
        if (Test-Path -LiteralPath $saved -PathType Leaf) {
            Copy-Item -LiteralPath $saved -Destination $destination -Force
        } elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
            Remove-Item -LiteralPath $destination
        }
    }
    throw
} finally {
    if (Test-Path -LiteralPath $stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
}

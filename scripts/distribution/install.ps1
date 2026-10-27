param([string]$Source = (Split-Path $PSScriptRoot -Parent), [switch]$Update, [switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath($Source)
$programsRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$installRoot = Join-Path $programsRoot 'Xilogue'
$previousRoot = Join-Path $programsRoot 'Xilogue.previous'
$stagingRoot = Join-Path $programsRoot ('Xilogue.stage-' + [Guid]::NewGuid().ToString('N'))
foreach ($target in @($installRoot, $previousRoot, $stagingRoot)) {
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($target)) -ne $programsRoot) { throw 'Unsafe install path' }
    if ((Test-Path -LiteralPath $target) -and (Get-Item -LiteralPath $target).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'Reparse install path rejected' }
}
if ($sourceRoot -eq $installRoot -or $sourceRoot -eq $previousRoot) { throw 'Run installer from the unpacked new package' }
if (Test-Path -LiteralPath $previousRoot) { throw 'Keep or move Xilogue.previous before another update; it is your rollback copy' }
if ((Test-Path -LiteralPath $installRoot) -and -not $Update) { throw 'Already installed. Use -Update after exiting the application' }
$manifestPath = Join-Path $sourceRoot 'package-files.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($entry in $manifest.PSObject.Properties) {
    $path = [IO.Path]::GetFullPath((Join-Path $sourceRoot $entry.Name))
    if (-not $path.StartsWith($sourceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe package entry' }
    $stream = [IO.File]::OpenRead($path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
    if ($hash -ne $entry.Value) { throw ('Package hash mismatch: ' + $entry.Name) }
}
$running = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($installRoot + '\', [StringComparison]::OrdinalIgnoreCase) }
if ($running) { throw 'Exit 昔录 · Xilogue before install/update' }
New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
try {
    foreach ($entry in $manifest.PSObject.Properties) {
        $destination = Join-Path $stagingRoot $entry.Name
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $sourceRoot $entry.Name) -Destination $destination
    }
    Copy-Item -LiteralPath $manifestPath -Destination $stagingRoot
    if (Test-Path -LiteralPath $installRoot) { Move-Item -LiteralPath $installRoot -Destination $previousRoot }
    Move-Item -LiteralPath $stagingRoot -Destination $installRoot
} catch {
    if ((Test-Path -LiteralPath $previousRoot) -and -not (Test-Path -LiteralPath $installRoot)) { Move-Item -LiteralPath $previousRoot -Destination $installRoot }
    throw
}
if (-not $NoShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) '昔录 · Xilogue.lnk'))
    $shortcut.TargetPath = Join-Path $installRoot 'Xilogue.exe'
    $shortcut.WorkingDirectory = $installRoot
    $shortcut.Save()
}
Write-Output ('Installed: ' + $installRoot + '. Startup is not enabled. User data was preserved.')

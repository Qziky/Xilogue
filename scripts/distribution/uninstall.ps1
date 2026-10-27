param([switch]$RemoveData, [switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$programsRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$installRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
if ($installRoot -ne (Join-Path $programsRoot 'Xilogue')) { throw 'Only uninstall the installed per-user application' }
if ((Get-Item -LiteralPath $installRoot).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'Reparse install path rejected' }
if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'build-info.json'))) { throw 'Install marker missing' }
$running = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($installRoot + '\', [StringComparison]::OrdinalIgnoreCase) }
if ($running) { throw 'Exit 昔录 · Xilogue first' }
$runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$value = (Get-ItemProperty -LiteralPath $runPath -Name 'Xilogue' -ErrorAction SilentlyContinue).Xilogue
if ($value -and $value.Contains($installRoot)) { Remove-ItemProperty -LiteralPath $runPath -Name 'Xilogue' }
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) '昔录 · Xilogue.lnk'
if (-not $NoShortcut -and (Test-Path -LiteralPath $shortcut)) { Remove-Item -LiteralPath $shortcut }
if ($RemoveData) {
    $dataRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Xilogue'))
    if ([IO.Path]::GetDirectoryName($dataRoot) -ne [IO.Path]::GetFullPath($env:LOCALAPPDATA)) { throw 'Unsafe data path' }
    if ((Test-Path -LiteralPath $dataRoot) -and (Get-Item -LiteralPath $dataRoot).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'Reparse data path rejected' }
    $marker = Join-Path $dataRoot '.xilogue'
    if ((Test-Path -LiteralPath $marker) -and (Get-Content -LiteralPath $marker -Raw) -eq "Xilogue/v1`n") { Remove-Item -LiteralPath $dataRoot -Recurse -Force }
}
Remove-Item -LiteralPath $installRoot -Recurse -Force
Write-Output 'Application removed. Custom data directories, model caches and previous versions are retained.'

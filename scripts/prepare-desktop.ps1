$ErrorActionPreference = 'Stop'
$desktopRoot = (Resolve-Path (Join-Path $PSScriptRoot '../desktop')).Path
$runtimeCache = Join-Path $desktopRoot '.runtime-cache'
New-Item -ItemType Directory -Path $runtimeCache -Force | Out-Null
$electronVersion = (Get-Content (Join-Path $desktopRoot 'node_modules/electron/package.json') -Raw | ConvertFrom-Json).version
$archiveName = "electron-v$electronVersion-win32-x64.zip"
$archivePath = Join-Path $runtimeCache $archiveName
$expectedHash = (Get-Content (Join-Path $desktopRoot 'node_modules/electron/checksums.json') -Raw | ConvertFrom-Json).$archiveName
if (-not $expectedHash) { throw 'Pinned Electron package has no checksum for this runtime.' }
if (-not (Test-Path -LiteralPath $archivePath)) {
    Invoke-WebRequest -Uri "https://github.com/electron/electron/releases/download/v$electronVersion/$archiveName" -OutFile $archivePath
}
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Electron runtime checksum mismatch.' }
$runtimeDirectory = Join-Path $desktopRoot 'node_modules/electron/dist'
if (-not (Test-Path -LiteralPath (Join-Path $runtimeDirectory 'electron.exe'))) {
    Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimeDirectory
}
Set-Content -LiteralPath (Join-Path $desktopRoot 'node_modules/electron/path.txt') -Value 'electron.exe' -NoNewline -Encoding ascii
$assetDirectory = Join-Path $desktopRoot 'assets'
New-Item -ItemType Directory -Path $assetDirectory -Force | Out-Null
Add-Type -AssemblyName System.Drawing
$logoSource = [System.Drawing.Image]::FromFile((Join-Path $desktopRoot '../frontend/public/brand/wudun-multi-agent-icon.png'))
$logoBitmap = [System.Drawing.Bitmap]::new(256,256)
$logoGraphics = [System.Drawing.Graphics]::FromImage($logoBitmap)
try {
    $logoGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $logoGraphics.DrawImage($logoSource,0,0,256,256)
    $logoBitmap.Save((Join-Path $assetDirectory 'icon.png'),[System.Drawing.Imaging.ImageFormat]::Png)
} finally { $logoGraphics.Dispose(); $logoBitmap.Dispose(); $logoSource.Dispose() }
$iconPng = [IO.File]::ReadAllBytes((Join-Path $assetDirectory 'icon.png'))
$iconStream = [IO.MemoryStream]::new()
$iconWriter = [IO.BinaryWriter]::new($iconStream)
try {
    $iconWriter.Write([uint16]0); $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]1)
    $iconWriter.Write([byte]0); $iconWriter.Write([byte]0); $iconWriter.Write([byte]0); $iconWriter.Write([byte]0)
    $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]32); $iconWriter.Write([uint32]$iconPng.Length); $iconWriter.Write([uint32]22); $iconWriter.Write($iconPng)
    [IO.File]::WriteAllBytes((Join-Path $assetDirectory 'icon.ico'),$iconStream.ToArray())
} finally { $iconWriter.Dispose(); $iconStream.Dispose() }
Write-Output "Desktop runtime $electronVersion verified and prepared."

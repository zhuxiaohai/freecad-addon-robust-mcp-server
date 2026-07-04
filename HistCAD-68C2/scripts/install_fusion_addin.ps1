param(
    [string]$AddInDir = "$env:APPDATA\Autodesk\Autodesk Fusion 360\API\AddIns\fusion360_server"
)

$ErrorActionPreference = "Stop"
$Source = Resolve-Path (Join-Path $PSScriptRoot "..\fusion360_tools\server")
New-Item -ItemType Directory -Force -Path $AddInDir | Out-Null
Copy-Item -Path (Join-Path $Source "*") -Destination $AddInDir -Recurse -Force
Write-Host "Installed Fusion server add-in to: $AddInDir"
Write-Host "Open Fusion 360, then run the 'fusion360_server' add-in from Scripts and Add-Ins."

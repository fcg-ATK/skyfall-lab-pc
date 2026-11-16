param(
    [ValidateSet('Launch', 'Setup', 'Check')]
    [string]$Mode = 'Launch',
    [string]$EngineRoot = $env:UE_ROOT
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectFile = Join-Path $projectRoot 'SkyfallPC.uproject'

function Test-Unreal58([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    $editor = Join-Path $Candidate 'Engine\Binaries\Win64\UnrealEditor.exe'
    $version = Join-Path $Candidate 'Engine\Build\Build.version'
    if (!(Test-Path -LiteralPath $editor) -or !(Test-Path -LiteralPath $version)) { return $false }
    try {
        $build = Get-Content -LiteralPath $version -Raw | ConvertFrom-Json
        return $build.MajorVersion -eq 5 -and $build.MinorVersion -eq 8
    } catch { return $false }
}

try {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($EngineRoot) {
        if (!(Test-Unreal58 $EngineRoot)) {
            throw 'UE_ROOT / -EngineRoot must point to an installed Unreal Engine 5.8 directory containing Engine.'
        }
        $candidates.Add($EngineRoot)
    } else {
        foreach ($key in @('HKLM:\SOFTWARE\EpicGames\Unreal Engine\5.8',
                           'HKLM:\SOFTWARE\WOW6432Node\EpicGames\Unreal Engine\5.8')) {
            $installed = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
            if ($installed.InstalledDirectory) { $candidates.Add($installed.InstalledDirectory) }
        }
        $launcherFile = Join-Path $env:ProgramData 'Epic\UnrealEngineLauncher\LauncherInstalled.dat'
        if (Test-Path -LiteralPath $launcherFile) {
            $launcher = Get-Content -LiteralPath $launcherFile -Raw | ConvertFrom-Json
            foreach ($entry in $launcher.InstallationList) {
                if ($entry.AppName -like 'UE_5.8*') { $candidates.Add($entry.InstallLocation) }
            }
        }
        foreach ($drive in Get-PSDrive -PSProvider FileSystem) {
            foreach ($relative in @('UE_5.8', 'Program Files\Epic Games\UE_5.8', 'Epic Games\UE_5.8')) {
                $candidates.Add((Join-Path $drive.Root $relative))
            }
        }
    }
    $resolvedEngine = $candidates | Where-Object { Test-Unreal58 $_ } | Select-Object -First 1
    if (!$resolvedEngine) {
        throw 'Unreal Engine 5.8 was not found. Install it with Epic Games Launcher, or set UE_ROOT to the engine installation directory.'
    }
    $resolvedEngine = (Resolve-Path -LiteralPath $resolvedEngine).Path
    if ($Mode -eq 'Check') {
        Write-Output "Unreal Engine 5.8: $resolvedEngine"
        exit 0
    }
    $saved = Join-Path $projectRoot 'Saved'
    $statusFile = Join-Path $saved 'setup-project.json'
    if ($Mode -eq 'Setup') {
        New-Item -ItemType Directory -Path $saved -Force | Out-Null
        if (Test-Path -LiteralPath $statusFile) { Remove-Item -LiteralPath $statusFile }
        $editorCmd = Join-Path $resolvedEngine 'Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
        $script = Join-Path $PSScriptRoot 'setup_project.py'
        $log = Join-Path $saved 'setup-project.log'
        Write-Host 'Generating six local Niagara systems from your installed UE templates. The first shader compilation may take several minutes.'
        & $editorCmd $projectFile '-run=pythonscript' "-script=$script" '-EnablePlugins=NiagaraToolsets' '-AllowCommandletRendering' '-d3d12' '-unattended' '-nop4' "-abslog=$log"
        $engineExit = $LASTEXITCODE
        if ($engineExit -ne 0 -or !(Test-Path -LiteralPath $statusFile)) {
            throw "Setup failed. See $log (engine exit code $engineExit)."
        }
        $status = Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json
        if ($status.status -ne 'passed') { throw "Setup did not pass. See $statusFile" }
        Write-Host 'Setup complete. Run LaunchSkyfall.cmd to play.'
        exit 0
    }
    $requiredSystems = @(
        'CombatV2/Effects/NS_HighEnergyImpact', 'CombatV2/Effects/NS_LocalDust',
        'CombatV2/Effects/NS_RocketExhaust', 'Meteor/NS_MeteorExit',
        'Meteor/NS_MeteorImpact', 'Meteor/NS_MeteorTail'
    )
    foreach ($asset in $requiredSystems) {
        if (!(Test-Path -LiteralPath (Join-Path $projectRoot "Content/Skyfall/$asset.uasset"))) {
            throw 'Local effects are missing. Run SetupProject.cmd successfully before launching.'
        }
    }
    if (!(Test-Path -LiteralPath $statusFile) -or
        (Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json).status -ne 'passed') {
        throw 'Setup has not passed. Run SetupProject.cmd before launching.'
    }
    $editor = Join-Path $resolvedEngine 'Engine\Binaries\Win64\UnrealEditor.exe'
    Start-Process -FilePath $editor -ArgumentList @("`"$projectFile`"", '/Game/Skyfall/Maps/DemolitionYard', '-game', '-windowed', '-ResX=1600', '-ResY=900', '-NoSplash') -WindowStyle Normal
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
<#
.SYNOPSIS
    Run gpygap on a Windows online machine, in a Linux container.

.DESCRIPTION
    The same image as on Linux, run through Docker Desktop or Podman Desktop with the
    Linux-container backend. Everything inside is unchanged; only the mounts differ,
    because a Windows path is not a path the container can use.

    The project directory is mounted at /work, so gpygap.toml must carry the matching
    translation for the sibling containers that build wheels:

        [build]
        container_root = "/work"
        host_root = "C:\\gpygap"      # wherever this repository actually lives

    See docs/online-on-windows.md. The Linux host is what CI tests; this path is
    supported but exercised by hand.

.EXAMPLE
    .\bin\gpygap.ps1 sync --config gpygap.toml
#>
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Arguments)

$ErrorActionPreference = 'Stop'

$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$image = if ($env:GPYGAP_IMAGE) { $env:GPYGAP_IMAGE } else { 'localhost/gpygap:latest' }
$engine = if ($env:GPYGAP_ENGINE) { $env:GPYGAP_ENGINE } else { 'docker' }

# Rebuilds only when the sources changed; otherwise served from the engine's cache.
& $engine build --quiet --file (Join-Path $projectDir 'Containerfile') --tag $image $projectDir | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$mounts = @('--volume', "${projectDir}:/work")

# Directories outside the project, separated by semicolons, the way PATH is on Windows.
if ($env:GPYGAP_MOUNTS) {
    foreach ($dir in $env:GPYGAP_MOUNTS.Split(';')) {
        if ($dir) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            $mounts += @('--volume', "${dir}:$((Resolve-Path $dir).Path)")
        }
    }
}

# Wheel builds start sibling containers, so the container needs the engine socket.
# Docker Desktop exposes its named pipe under this path to Linux containers.
$socketEnv = @()
if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'sync' -and -not $env:GPYGAP_NO_BUILD_SOCKET) {
    $mounts += @('--volume', '//var/run/docker.sock:/var/run/docker.sock')
    $socketEnv = @('--env', 'DOCKER_HOST=unix:///var/run/docker.sock')
}

& $engine run --rm --interactive `
    @mounts @socketEnv `
    --workdir /work `
    $image @Arguments
exit $LASTEXITCODE

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$a = Join-Path $repo 'firmware\node-a-integrated'
$b = Join-Path $repo 'firmware\node-b-integrated'
$autostart = Join-Path $repo 'firmware\node-a-thread-autostart'
$out = Join-Path $repo 'build-output\node-a-integrated-0.1.0-host-tests'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$gcc = 'C:\msys64\ucrt64\bin\gcc.exe'
if (-not (Test-Path $gcc)) { throw "GCC not found: $gcc" }
$env:PATH = 'C:\msys64\ucrt64\bin;C:\msys64\usr\bin;' + $env:PATH

function Invoke-HostTest([string]$name, [string[]]$inputs, [string[]]$includes) {
    $exe = Join-Path $out "$name.exe"
    $arguments = @('-std=c11', '-Wall', '-Wextra', '-Werror')
    foreach ($include in $includes) { $arguments += @('-I', $include) }
    $arguments += $inputs
    $arguments += @('-o', $exe)
    & $gcc @arguments
    if ($LASTEXITCODE -ne 0) { throw "$name compile failed ($LASTEXITCODE)" }
    & $exe
    if ($LASTEXITCODE -ne 0) { throw "$name failed ($LASTEXITCODE)" }
    Write-Output "PASS $name"
}

$main = Join-Path $a 'main'
Invoke-HostTest 'mq2_logic' @((Join-Path $a 'test\test_mq2_logic.c'), (Join-Path $main 'mq2_logic.c')) @($main)
Invoke-HostTest 'mq2_adc' @((Join-Path $a 'test\test_mq2_adc.c'), (Join-Path $main 'mq2_adc.c')) @((Join-Path $a 'test\fake_adc'), $main)
Invoke-HostTest 'node_a_response' @((Join-Path $a 'test\test_node_a_response.c'), (Join-Path $main 'node_a_response.c'), (Join-Path $main 'mq2_logic.c'), (Join-Path $main 'integrated_control.c')) @($main)
Invoke-HostTest 'integrated_control' @((Join-Path $b 'test\test_integrated_control.c'), (Join-Path $main 'integrated_control.c')) @($main)
Invoke-HostTest 'servo_output' @((Join-Path $b 'test\test_servo_output.c'), (Join-Path $main 'servo_output.c'), (Join-Path $main 'integrated_control.c')) @((Join-Path $b 'test\fake_driver'), $main)
Invoke-HostTest 'probe_state' @((Join-Path $b 'test\test_probe_state.c'), (Join-Path $main 'probe_state.c')) @($main)
Invoke-HostTest 'thread_start' @((Join-Path $autostart 'test\test_thread_start.c'), (Join-Path $main 'thread_start.c')) @((Join-Path $autostart 'test\fake_openthread'), $main)

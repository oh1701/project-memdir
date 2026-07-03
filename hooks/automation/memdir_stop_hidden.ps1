$ErrorActionPreference = "SilentlyContinue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$hookScript = Join-Path $scriptDir "memdir_hook.py"

function Quote-Argument([string] $Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Invoke-HookProcess($Command, [string] $Arguments, [string] $Payload) {
    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $Command.Source
    $processInfo.Arguments = $Arguments
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $processInfo.RedirectStandardInput = $true
    $processInfo.EnvironmentVariables["PYTHONUTF8"] = "1"

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    if (-not $process.Start()) {
        return $null
    }

    $process.StandardInput.Write($Payload)
    $process.StandardInput.Close()
    $process.WaitForExit()

    return $process.ExitCode
}

try {
    $payload = [Console]::In.ReadToEnd()

    $launchers = @(
        @{ Command = "py"; Arguments = "-3 " + (Quote-Argument $hookScript) + " stop" },
        @{ Command = "python"; Arguments = (Quote-Argument $hookScript) + " stop" },
        @{ Command = "python3"; Arguments = (Quote-Argument $hookScript) + " stop" }
    )

    foreach ($launcher in $launchers) {
        $command = Get-Command $launcher.Command -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }

        $exitCode = Invoke-HookProcess $command $launcher.Arguments $payload
        if ($null -ne $exitCode) {
            exit $exitCode
        }
    }

    exit 1
}
finally {
}

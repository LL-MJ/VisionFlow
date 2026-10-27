$ErrorActionPreference = 'Continue'
Write-Output "OS: $([System.Runtime.InteropServices.RuntimeInformation]::OSDescription)"
Write-Output "Architecture: $([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)"
foreach ($tool in @('python', 'node', 'npm', 'git', 'ffmpeg', 'ffprobe', 'nvidia-smi')) {
    if (Get-Command $tool -ErrorAction SilentlyContinue) {
        Write-Output "--- $tool ---"
        if ($tool -eq 'nvidia-smi') {
            & $tool --query-gpu=name,memory.total,driver_version --format=csv,noheader
        } elseif ($tool -in @('ffmpeg', 'ffprobe')) {
            & $tool -version 2>&1 | Select-Object -First 1
        } else {
            & $tool --version
        }
    } else {
        Write-Output "${tool}: not found (optional for P0-01 where applicable)"
    }
}

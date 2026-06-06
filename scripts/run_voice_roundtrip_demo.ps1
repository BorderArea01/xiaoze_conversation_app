param(
  [string]$HostIp = "192.168.0.234",
  [string]$Model = "qwen-plus",
  [string]$InputDevice = "",
  [switch]$ListDevices,
  [switch]$FixedRecord,
  [double]$Duration = 5.0
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo

if ($ListDevices) {
  python .\scripts\voice_roundtrip_demo.py --list-devices
  exit $LASTEXITCODE
}

if (-not $env:DASHSCOPE_API_KEY -and -not $env:BAILIAN_API_KEY) {
  $secure = Read-Host "请输入阿里云百炼 DASHSCOPE_API_KEY" -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    $env:DASHSCOPE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  }
}

$argsList = @(
  ".\scripts\voice_roundtrip_demo.py",
  "--host", $HostIp,
  "--model", $Model
)

if ($InputDevice -ne "") {
  $argsList += @("--input-device", $InputDevice)
}

if ($FixedRecord) {
  $argsList += @("--record-mode", "fixed", "--duration", "$Duration")
}

python @argsList

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Python {
	param(
		[Parameter(ValueFromRemainingArguments = $true)]
		[string[]]$Args
	)

	if (Get-Command py -ErrorAction SilentlyContinue) {
		& py -3 @Args
		return
	}

	if (Get-Command python -ErrorAction SilentlyContinue) {
		& python @Args
		return
	}

	throw "Neither 'py' nor 'python' was found on PATH."
}

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
	Invoke-Python -m venv .venv
}
.\.venv\Scripts\Activate.ps1

Invoke-Python -m pip install --upgrade pip
Invoke-Python -m pip install -r requirements-build.txt

pyinstaller --noconfirm --clean multiboxer.spec

Write-Host "Build complete. Windows executable: dist\multiboxer.exe"

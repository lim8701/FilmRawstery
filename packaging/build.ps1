# Film Rawstery — deterministic onedir packaging.
# Usage:  .\packaging\build.ps1            (release; smoke-tests 10s)
#         .\packaging\build.ps1 -SmokeSeconds 6
# Does: stop running app -> clean dist -> PyInstaller build -> smoke-test the exe
#       from a different directory -> zip -> print result. Throws on any failure.
param([int]$SmokeSeconds = 10)

$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $PSScriptRoot          # packaging/ -> project root
$venvPy = Join-Path $proj '.venv\Scripts\python.exe'
$spec = Join-Path $proj 'FilmRawstery.spec'
$exe  = Join-Path $proj 'dist\FilmRawstery\FilmRawstery.exe'
# zip 은 dist/ 안에 생성(프로젝트 루트 오염 방지, gitignore 동일 적용). [1/4] 클린이 이전 zip 도 제거.
$zip  = Join-Path $proj 'dist\FilmRawstery-win64.zip'

if (-not (Test-Path $venvPy)) { throw "venv python not found: $venvPy" }
if (-not (Test-Path $spec))   { throw "spec not found: $spec" }

# spec 은 상대경로(luts/shaders/fonts 등)를 쓰므로 호출 위치와 무관하게 항상 프로젝트 루트에서 빌드.
Set-Location $proj

Write-Host "[1/4] stopping any running app + cleaning dist..."
Get-Process FilmRawstery -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400
Remove-Item -Recurse -Force (Join-Path $proj 'dist') -ErrorAction SilentlyContinue

Write-Host "[2/4] building (PyInstaller)..."
# `-m PyInstaller` (not the console-script wrapper) — robust to venv path quirks.
& $venvPy -m PyInstaller $spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit $LASTEXITCODE)" }
if (-not (Test-Path $exe)) { throw "exe not produced: $exe" }

Write-Host "[3/4] smoke-testing exe from a different directory ($SmokeSeconds s)..."
$err = Join-Path $env:TEMP 'fr_smoke_err.txt'
Remove-Item $err -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $exe -WorkingDirectory $env:TEMP -PassThru -RedirectStandardError $err
Start-Sleep -Seconds $SmokeSeconds
if ($p.HasExited) {
    Write-Host "  SMOKE FAILED — app exited before ${SmokeSeconds}s. stderr:" -ForegroundColor Red
    if (Test-Path $err) { Get-Content $err -Tail 25 }
    throw "Smoke test failed"
}
Stop-Process -Id $p.Id -Force
Start-Sleep -Milliseconds 600

Write-Host "[4/4] zipping..."
Compress-Archive -Path (Join-Path $proj 'dist\FilmRawstery') -DestinationPath $zip -CompressionLevel Optimal -Force
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "OK  ->  $zip  ($mb MB)" -ForegroundColor Green

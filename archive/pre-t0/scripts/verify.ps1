# Smoke verify for local-appliance
param([int]$ApiPort = 8090)
$ErrorActionPreference = "Continue"
$failed = 0
$base = "http://127.0.0.1:$ApiPort"

function Check($name, $url) {
  try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
      Write-Host "OK  $name" -ForegroundColor Green
      return
    }
    Write-Host "FAIL $name status=$($r.StatusCode)" -ForegroundColor Red
    $script:failed++
  } catch {
    Write-Host "FAIL $name $($_.Exception.Message)" -ForegroundColor Red
    $script:failed++
  }
}

Check "api.health" "$base/health"
Check "api.ready" "$base/ready"

try {
  $body = '{"email":"admin@dms.local","password":"admin","org_slug":"default"}'
  $login = Invoke-RestMethod -Uri "$base/auth/login" -Method POST -ContentType "application/json" -Body $body
  if ($login.access_token) { Write-Host "OK  auth.login" -ForegroundColor Green }
  else { Write-Host "FAIL auth.login no token"; $failed++ }
  $headers = @{ Authorization = "Bearer $($login.access_token)" }
  $spaces = Invoke-RestMethod -Uri "$base/spaces" -Headers $headers
  Write-Host "OK  spaces.list count=$($spaces.spaces.Count)" -ForegroundColor Green
} catch {
  Write-Host "FAIL auth/spaces $($_.Exception.Message)" -ForegroundColor Red
  $failed++
}

if ($failed -gt 0) {
  Write-Host "VERIFY FAILED ($failed)" -ForegroundColor Red
  exit 1
}
Write-Host "VERIFY PASSED" -ForegroundColor Green

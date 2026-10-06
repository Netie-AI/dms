# Self-check for Get-DmsDbPassword in Start-DMSStack.ps1 (BANK-04, #271, D4).
# Pester-free: extracts the function from the launcher and runs it on a table of
# shell and .env inputs. The expected values are what `docker compose` itself
# resolves for the same .env text (checked with `docker compose --env-file X
# config`), except that an empty or whitespace-only value is "absent" here.
# ASCII-only: Windows PowerShell 5.1 misparses UTF-8 punctuation.
#
#   pwsh -NoProfile -File scripts/windows/Test-GetDmsDbPassword.ps1
#   powershell -NoProfile -File scripts/windows/Test-GetDmsDbPassword.ps1
#   ... -LauncherPath <other Start-DMSStack.ps1>      # to run it against a copy
#
# Exit 0 when every case holds, 1 otherwise.
param(
  [string]$LauncherPath = (Join-Path $PSScriptRoot "Start-DMSStack.ps1")
)
$ErrorActionPreference = "Stop"

$src = Get-Content -Raw -LiteralPath $LauncherPath
$m = [regex]::Match($src, '(?s)function Get-DmsDbPassword.*?\r?\n\}\r?\n')
if (-not $m.Success) {
  Write-Host "FAIL could not find function Get-DmsDbPassword in $LauncherPath"
  exit 1
}
Invoke-Expression $m.Value

# Name, shell value ($null = variable not set), .env lines ($null = no file), expected ($null = absent).
$cases = @(
  @{ N = "shell whitespace-only, no .env";            E = "   ";    F = $null;                                         X = $null },
  @{ N = "shell empty, no .env";                      E = "";       F = $null;                                         X = $null },
  @{ N = "nothing set, no .env";                      E = $null;    F = $null;                                         X = $null },
  @{ N = ".env whitespace-only value";                E = $null;    F = @("DMS_DB_PASSWORD=   ");                      X = $null },
  @{ N = ".env empty value";                          E = $null;    F = @("DMS_DB_PASSWORD=");                         X = $null },
  @{ N = ".env inline comment after space";           E = $null;    F = @("DMS_DB_PASSWORD=abc # note");               X = "abc" },
  @{ N = ".env two inline comments";                  E = $null;    F = @("DMS_DB_PASSWORD=ab #c #d");                 X = "ab" },
  @{ N = ".env duplicate keys: last wins";            E = $null;    F = @("DMS_DB_PASSWORD=first", "DMS_DB_PASSWORD=second"); X = "second" },
  @{ N = ".env last duplicate empty: absent";         E = $null;    F = @("DMS_DB_PASSWORD=first", "DMS_DB_PASSWORD=");      X = $null },
  @{ N = ".env single quotes";                        E = $null;    F = @("DMS_DB_PASSWORD='p w'");                    X = "p w" },
  @{ N = ".env double quotes keep # and spaces";      E = $null;    F = @('DMS_DB_PASSWORD="pw # not a comment"');     X = "pw # not a comment" },
  @{ N = ".env double quotes with escaped quote";     E = $null;    F = @('DMS_DB_PASSWORD="a\"b"');                   X = 'a"b' },
  @{ N = ".env quoted then trailing comment";         E = $null;    F = @('DMS_DB_PASSWORD="dq # c" # trailing');      X = "dq # c" },
  @{ N = ".env single quoted then trailing comment";  E = $null;    F = @("DMS_DB_PASSWORD='sq' # trailing");          X = "sq" },
  @{ N = ".env export prefix";                        E = $null;    F = @("export DMS_DB_PASSWORD=zzz");               X = "zzz" },
  @{ N = ".env value starting with #";                E = $null;    F = @("DMS_DB_PASSWORD=#only");                    X = "#only" },
  @{ N = ".env # inside a value";                     E = $null;    F = @("DMS_DB_PASSWORD=a#b");                      X = "a#b" },
  @{ N = ".env commented-out line";                   E = $null;    F = @("# DMS_DB_PASSWORD=commented");              X = $null },
  @{ N = ".env spaces around =";                      E = $null;    F = @("DMS_DB_PASSWORD = spaced ");                X = "spaced" },
  @{ N = ".env trailing whitespace trimmed";          E = $null;    F = @("DMS_DB_PASSWORD=abc   ");                   X = "abc" },
  @{ N = "shell value beats .env";                    E = "fromenv"; F = @("DMS_DB_PASSWORD=other");                   X = "fromenv" },
  @{ N = "shell value is trimmed";                    E = "  sp  "; F = $null;                                         X = "sp" },
  @{ N = "shell whitespace-only falls through to .env"; E = "  ";   F = @("DMS_DB_PASSWORD=real");                      X = "real" }
)

$saved = $env:DMS_DB_PASSWORD
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("dms-pw-check-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $root | Out-Null
$failed = 0
try {
  $i = 0
  foreach ($c in $cases) {
    $i++
    $dir = Join-Path $root ("c" + $i)
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    if ($null -ne $c.F) {
      Set-Content -LiteralPath (Join-Path $dir ".env") -Value $c.F -Encoding ascii
    }
    if ($null -eq $c.E) {
      Remove-Item Env:DMS_DB_PASSWORD -ErrorAction SilentlyContinue
    } else {
      $env:DMS_DB_PASSWORD = $c.E
    }
    $got = Get-DmsDbPassword $dir
    if ($null -eq $c.X) { $ok = ($null -eq $got) } else { $ok = ($null -ne $got) -and ($got -ceq $c.X) }
    $gotText = if ($null -eq $got) { "<absent>" } else { "'" + $got + "'" }
    $wantText = if ($null -eq $c.X) { "<absent>" } else { "'" + $c.X + "'" }
    if ($ok) {
      Write-Host ("PASS  {0}" -f $c.N)
    } else {
      $failed++
      Write-Host ("FAIL  {0}: got {1}, expected {2}" -f $c.N, $gotText, $wantText)
    }
  }
} finally {
  if ($null -eq $saved) { Remove-Item Env:DMS_DB_PASSWORD -ErrorAction SilentlyContinue } else { $env:DMS_DB_PASSWORD = $saved }
  Remove-Item -Recurse -Force -LiteralPath $root -ErrorAction SilentlyContinue
}

Write-Host ""
if ($failed -ne 0) {
  Write-Host ("{0} of {1} cases failed" -f $failed, $cases.Count)
  exit 1
}
Write-Host ("all {0} cases passed" -f $cases.Count)
exit 0

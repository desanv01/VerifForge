"""Exercise the helper's actual cleanup block with a fake Docker function only."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("label_matches", [True, False])
def test_diagnostic_failure_still_stops_only_label_owned_container(tmp_path, label_matches):
    helper = Path(__file__).resolve().parents[2] / "scripts/verify-backup-restore.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    # Parse, extract and execute ONLY the existing finally block. No helper setup,
    # -Execute path, Docker executable, backup read, restore or service is invoked.
    script = r"""
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(HELPER, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Invalid helper source' }
$outer = @($ast.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.TryStatementAst] })[-1]
$body = $outer.Finally.Extent.Text
$body = $body.Substring(1, $body.Length - 2)
$script:calls = @()
$containerId = 'synthetic-owned-container'
$runId = 'synthetic-review-label'
$passed = $true; $failure = $null
$cleanupIncomplete = $false; $diagnosticFailure = $null; $stopConfirmed = $null
$volumeCreated = $false; $networkCreated = $false
$labelMatches = LABEL
$output = OUTPUT
function Invoke-RecoveryDocker {
    param([string[]]$DockerArgs, [string]$Name, [switch]$AllowFailure, [switch]$Cleanup)
    $script:calls += $DockerArgs[0]
    if ($DockerArgs[0] -eq 'logs') { throw 'Synthetic diagnostic timeout; no Docker executed' }
    if ($Name -eq 'confirm-stopped') { return [pscustomobject]@{ ExitCode=0; Output='false' } }
    if ($Name -eq 'label-container' -and -not $labelMatches) { return [pscustomobject]@{ ExitCode=0; Output='different-owner' } }
    return [pscustomobject]@{ ExitCode=0; Output=$runId }
}
& ([scriptblock]::Create($body))
[ordered]@{ calls=$script:calls; passed=$passed; failure=$failure } | ConvertTo-Json -Compress
""".replace("HELPER", quote(helper)).replace("OUTPUT", quote(tmp_path)).replace("LABEL", "$true" if label_matches else "$false")
    result = subprocess.run([shutil.which("pwsh") or "powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert ("stop" in observed["calls"]) == label_matches, observed
    receipt = json.loads((tmp_path / "qualification-receipt.json").read_text(encoding="utf-8-sig"))
    assert "Synthetic diagnostic timeout" in receipt["diagnostic_failure"]
    assert receipt["container_stop_confirmed"] == label_matches
    assert receipt["cleanup_incomplete"] == (not label_matches)
    assert receipt["resources_removed"] == label_matches

param([switch]$Execute, [ValidateRange(60,300)][int]$TimeoutSeconds = 240)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$image = 'sha256:32ebb7185baf2c9d38f91684417d140c0745aeff5082dfbb62edb330c2af6197'
$backupFolder = Join-Path $repo 'infra/app/evidence/vf024-pre-upgrade-20261024T015821851Z'
$backup = Join-Path $backupFolder 'veriforge.dump'
$expected = Join-Path $repo 'infra/app/evidence/vf024-upgrade/pre-upgrade-state.json'
$knownBackupHash = '091d8e811bf1862995e7c45b3333e41cb4df94a0cde5c6d493ea2745df941a55'
if (-not $Execute) {
    [ordered]@{ status='plan_only'; image=$image; backup=$backup; expected=$expected; timeout_seconds=$TimeoutSeconds; mutation='none; explicit main live grant and -Execute required' } | ConvertTo-Json
    return
}
$control = Get-Content (Join-Path $repo '.coordination/current-state.json') -Raw | ConvertFrom-Json
if ($control.control -ne 'RUNNING') { throw 'Current controls do not permit new work.' }
if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $knownBackupHash) { throw 'Known retained archive hash differs.' }
$backupReceipt = Get-Content (Join-Path $backupFolder 'backup-receipt.json') -Raw | ConvertFrom-Json
if ($backupReceipt.sha256 -ne $knownBackupHash -or (Get-Item -LiteralPath $backup).Length -ne $backupReceipt.size_bytes) { throw 'Archive differs from retained backup receipt.' }
if ((Get-Item -LiteralPath $backup).Length -gt 8388608 -or $backup.Contains(',')) { throw 'Archive exceeds this qualification bound or has an unsupported mount path.' }
$runId = [Guid]::NewGuid().ToString('N')
$containerName = "veriforge-recovery-$runId"
$networkName = "veriforge-recovery-net-$runId"
$volumeName = "veriforge-recovery-data-$runId"
$label = "com.veriforge.recovery=$runId"
$output = Join-Path $repo "infra/app/evidence/vf025a-$runId"
New-Item -ItemType Directory -Path $output | Out-Null
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$dockerPath = (Get-Command docker).Source
$step = 0
$containerId = $null
$networkCreated = $false
$volumeCreated = $false
$passed = $false
$failure = $null
$diagnosticFailure = $null
$stopConfirmed = $null
$cleanupIncomplete = $false

function Invoke-RecoveryDocker {
    param([string[]]$DockerArgs, [string]$Name, [switch]$AllowFailure, [switch]$Cleanup, [string]$Executable = $script:dockerPath)
    $script:step++
    $remaining = if ($Cleanup) { 10 } else { [Math]::Min(90, [Math]::Floor(($script:deadline - [DateTime]::UtcNow).TotalSeconds)) }
    if ($remaining -le 0) { throw 'Qualification deadline reached.' }
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($arg in $DockerArgs) { $info.ArgumentList.Add($arg) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $timedOut = -not $process.WaitForExit([int]($remaining * 1000))
    if ($timedOut) { $process.Kill($true); $process.WaitForExit() }
    $outText = $stdout.GetAwaiter().GetResult()
    $errText = $stderr.GetAwaiter().GetResult()
    $stem = '{0:D2}-{1}' -f $script:step,$Name
    [IO.File]::WriteAllText((Join-Path $script:output "$stem.stdout.txt"),$outText)
    [IO.File]::WriteAllText((Join-Path $script:output "$stem.stderr.txt"),$errText)
    if ($timedOut) { throw "Bounded Docker step timed out: $Name" }
    if ($process.ExitCode -ne 0 -and -not $AllowFailure) { throw "Docker step failed: $Name (exit $($process.ExitCode)); see retained log." }
    return [pscustomobject]@{ ExitCode=$process.ExitCode; Output=$outText.Trim() }
}

function Assert-RecoveryLabel {
    param([string]$Kind,[string]$Identifier)
    $check = Invoke-RecoveryDocker -DockerArgs @($Kind,'inspect',$Identifier,'--format','{{ index .Labels "com.veriforge.recovery" }}') -Name "label-$Kind" -Cleanup
    if ($check.Output -ne $script:runId) { throw "Refusing cleanup of unowned $Kind." }
}

try {
    [void](Invoke-RecoveryDocker -DockerArgs @('image','inspect',$image,'--format','{{.Id}}') -Name 'image-present')
    [void](Invoke-RecoveryDocker -DockerArgs @('network','create','--internal','--label',$label,$networkName) -Name 'create-network')
    $networkCreated = $true
    $networkPolicy = Invoke-RecoveryDocker -DockerArgs @('network','inspect',$networkName,'--format','{{.Internal}}') -Name 'network-policy'
    if ($networkPolicy.Output -ne 'true') { throw 'Recovery network is not internal.' }
    [void](Invoke-RecoveryDocker -DockerArgs @('volume','create','--label',$label,$volumeName) -Name 'create-volume')
    $volumeCreated = $true
    $createArgs = @('create','--pull=never','--name',$containerName,'--label',$label,'--network',$networkName,
        '--restart=no','--read-only','--memory=512m','--memory-swap=512m','--cpus=1','--pids-limit=128',
        '--security-opt=no-new-privileges','--tmpfs','/tmp:rw,nosuid,noexec,size=32m',
        '--tmpfs','/var/run/postgresql:rw,nosuid,noexec,size=16m',
        '--mount',"type=volume,src=$volumeName,dst=/var/lib/postgresql/data",
        '--mount',"type=bind,src=$backup,dst=/backup/archive.dump,readonly",
        '--env','POSTGRES_USER=veriforge','--env','POSTGRES_DB=restore_qualification','--env','POSTGRES_HOST_AUTH_METHOD=trust',
        $image,'postgres','-c','listen_addresses=','-c','statement_timeout=60000','-c','shared_buffers=32MB','-c','max_connections=8','-c','autovacuum=off')
    $containerId = (Invoke-RecoveryDocker -DockerArgs $createArgs -Name 'create-container').Output
    $inspect = Invoke-RecoveryDocker -DockerArgs @('inspect',$containerId,'--format','{{json .}}') -Name 'isolation-inspect'
    $details = $inspect.Output | ConvertFrom-Json
    if ($details.Image -ne $image -or -not $details.HostConfig.ReadonlyRootfs -or @($details.Mounts).Count -ne 2) { throw 'Unexpected recovery image/filesystem mounts.' }
    if (@($details.NetworkSettings.Networks.PSObject.Properties.Name).Count -ne 1 -or -not $details.NetworkSettings.Networks.$networkName) { throw 'Recovery network differs.' }
    if ($details.HostConfig.PortBindings -and @($details.HostConfig.PortBindings.PSObject.Properties).Count) { throw 'Recovery unexpectedly publishes ports.' }
    $archiveMount = @($details.Mounts | Where-Object Destination -EQ '/backup/archive.dump')
    if ($archiveMount.Count -ne 1 -or $archiveMount[0].RW -or $archiveMount[0].Type -ne 'bind') { throw 'Backup is not a read-only bind mount.' }
    $dataMount = @($details.Mounts | Where-Object Destination -EQ '/var/lib/postgresql/data')
    if ($dataMount.Count -ne 1 -or $dataMount[0].Name -ne $volumeName) { throw 'Unexpected recovery data volume.' }
    [void](Invoke-RecoveryDocker -DockerArgs @('start',$containerId) -Name 'start')
    while ($true) {
        # PID1 becomes postgres only after official entrypoint's temporary init server exits.
        $comm = Invoke-RecoveryDocker -DockerArgs @('exec',$containerId,'cat','/proc/1/comm') -Name 'readiness-process' -AllowFailure
        if ($comm.ExitCode -eq 0 -and $comm.Output -eq 'postgres') {
            $ready = Invoke-RecoveryDocker -DockerArgs @('exec',$containerId,'pg_isready','-h','/var/run/postgresql','-U','veriforge','-d','restore_qualification') -Name 'readiness-db' -AllowFailure
            if ($ready.ExitCode -eq 0) { break }
        }
        Start-Sleep -Seconds 2
    }
    $mountedHash = Invoke-RecoveryDocker -DockerArgs @('exec',$containerId,'sha256sum','/backup/archive.dump') -Name 'mounted-backup-hash'
    if (($mountedHash.Output -split '\s+')[0] -ne $knownBackupHash) { throw 'Mounted archive differs from original backup.' }
    [void](Invoke-RecoveryDocker -DockerArgs @('exec',$containerId,'pg_restore','--host=/var/run/postgresql','--username=veriforge','--dbname=restore_qualification','--single-transaction','--exit-on-error','--no-owner','--no-acl','/backup/archive.dump') -Name 'restore')
    $tablePairs = @('campaigns','attempts','findings','run_events','dispatches','contracts','projects') | ForEach-Object {
        $key = if ($_ -eq 'dispatches') { 'attempt_id' } else { 'id' }
        "'$_',(select coalesce(json_agg(to_jsonb(t) order by $key),'[]') from $_ t)"
    }
    $sql = @"
SET TIME ZONE 'UTC';
SELECT json_build_object(
 'revision',(select version_num from alembic_version),
 'tables',json_build_object($($tablePairs -join ',')),
 'columns',(select json_object_agg(table_name,cols) from (select table_name,json_agg(column_name order by ordinal_position) cols from information_schema.columns where table_schema='public' group by table_name) s),
 'constraints',(select json_object_agg(table_name,kinds) from (select table_name,json_object_agg(constraint_type,n) kinds from (select table_name,constraint_type,count(*) n from information_schema.table_constraints where table_schema='public' group by table_name,constraint_type) k group by table_name) s),
 'unvalidated_constraints',(select count(*) from pg_constraint c join pg_class r on r.oid=c.conrelid join pg_namespace n on n.oid=r.relnamespace where n.nspname='public' and not c.convalidated));
"@
    $snapshot = Invoke-RecoveryDocker -DockerArgs @('exec',$containerId,'psql','-h','/var/run/postgresql','-U','veriforge','-d','restore_qualification','-X','-qAt','-v','ON_ERROR_STOP=1','-c',$sql) -Name 'restored-snapshot'
    $snapshotFile = Join-Path $output 'restored-snapshot.json'
    [IO.File]::WriteAllText($snapshotFile,$snapshot.Output)
    $python = Join-Path $repo '.venv/Scripts/python.exe'
    [void](Invoke-RecoveryDocker -Executable $python -DockerArgs @((Join-Path $repo 'tests/integration/test_backup_restore.py'),'--snapshot',$snapshotFile,'--expected',$expected,'--receipt',(Join-Path $output 'comparison-receipt.json')) -Name 'compare-restored')
    if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $knownBackupHash) { throw 'Original backup changed.' }
    $passed = $true
} catch { $failure = $_.Exception.Message } finally {
    # Diagnostics must not prevent a label-checked stop, even on a client timeout.
    if ($containerId) {
        $stopConfirmed = $false
        try { [void](Invoke-RecoveryDocker -DockerArgs @('logs',$containerId) -Name 'postgres-log' -AllowFailure -Cleanup) }
        catch { $diagnosticFailure = $_.Exception.Message }
    }
    try {
        if ($containerId) {
            $owned = Invoke-RecoveryDocker -DockerArgs @('inspect',$containerId,'--format','{{ index .Config.Labels "com.veriforge.recovery" }}') -Name 'label-container' -Cleanup
            if ($owned.Output -ne $runId) { throw 'Refusing stop of unowned container.' }
            [void](Invoke-RecoveryDocker -DockerArgs @('stop','--time=5',$containerId) -Name 'stop' -Cleanup)
            $stopped = Invoke-RecoveryDocker -DockerArgs @('inspect',$containerId,'--format','{{.State.Running}}') -Name 'confirm-stopped' -Cleanup
            if ($stopped.Output -ne 'false') { throw 'Recovery container is not confirmed stopped.' }
            $stopConfirmed = $true
            if ($passed) { [void](Invoke-RecoveryDocker -DockerArgs @('rm',$containerId) -Name 'remove-container' -Cleanup) }
        }
        if ($passed -and $volumeCreated) { Assert-RecoveryLabel 'volume' $volumeName; [void](Invoke-RecoveryDocker -DockerArgs @('volume','rm',$volumeName) -Name 'remove-volume' -Cleanup) }
        if ($passed -and $networkCreated) { Assert-RecoveryLabel 'network' $networkName; [void](Invoke-RecoveryDocker -DockerArgs @('network','rm',$networkName) -Name 'remove-network' -Cleanup) }
    } catch { $cleanupIncomplete=$true; $passed=$false; $failure="Cleanup incomplete: $($_.Exception.Message); earlier failure: $failure" }
    [ordered]@{ status=$(if($passed){'passed'}else{'failed'}); run_id=$runId; image=$image; container_id=$containerId; container_name=$containerName; network=$networkName; volume=$volumeName; backup_sha256=$knownBackupHash; failure=$failure; diagnostic_failure=$diagnosticFailure; container_stop_confirmed=$stopConfirmed; cleanup_incomplete=$cleanupIncomplete; resources_removed=$passed; scope='isolated database restore only; no live application connections or artifact restore' } | ConvertTo-Json | Set-Content (Join-Path $output 'qualification-receipt.json')
}
Write-Output $output
if (-not $passed) { throw "Restore qualification failed; retained evidence/resources in $output. $failure" }

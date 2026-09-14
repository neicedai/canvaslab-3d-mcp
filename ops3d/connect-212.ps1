$ErrorActionPreference = 'Stop'
$existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8031 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    throw 'Port 8031 already has a listener. Verify its identity before replacing it.'
}
Start-Process -FilePath 'ssh.exe' -WindowStyle Hidden -ArgumentList @(
    '-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
    '-L', '127.0.0.1:8031:127.0.0.1:8031', 'root@192.168.101.213'
)
